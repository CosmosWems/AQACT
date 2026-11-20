/* ==========================================================================
   Local AI cache

   Small, dependency-free TTL/LRU cache on top of localStorage.  It keeps the
   compact dataset context, attachment reductions, chart explanations, insights
   and the conversation on the user's machine so repeat questions are instant
   and a page reload does not lose the thread.

   The cache is deliberately best-effort: private browsing, a full quota or a
   disabled storage API must never break the assistant.
   ========================================================================== */

const NAMESPACE = "aqat:ai:v1";
const DEFAULT_TTL_MS = 10 * 60 * 1000;
const MAX_ENTRIES = 80;
const CONVERSATION_TURNS = 60;

function createMemoryStorage() {
  const map = new Map();
  return {
    get length() {
      return map.size;
    },
    key(index) {
      return Array.from(map.keys())[index] ?? null;
    },
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    removeItem(key) {
      map.delete(key);
    },
    clear() {
      map.clear();
    },
  };
}

const memoryStorage = createMemoryStorage();
let customStorage = null;

/** Replace the backing store.  Used by tests; production uses localStorage. */
export function setCacheStorage(store) {
  customStorage = store || null;
}

function storage() {
  if (customStorage) return customStorage;
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_) {
    /* storage can throw in private mode or sandboxed frames */
  }
  return memoryStorage;
}

function fullKey(key) {
  return `${NAMESPACE}:${key}`;
}

function readEntry(key) {
  const store = storage();
  try {
    const raw = store.getItem(fullKey(key));
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry || typeof entry !== "object") return null;
    if (entry.expires && entry.expires <= Date.now()) {
      store.removeItem(fullKey(key));
      return null;
    }
    return entry;
  } catch (_) {
    return null;
  }
}

function namespaceKeys() {
  const store = storage();
  const keys = [];
  try {
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key && key.startsWith(`${NAMESPACE}:`)) keys.push(key);
    }
  } catch (_) {
    /* ignore */
  }
  return keys;
}

function evictOldest(keepKey) {
  const entries = namespaceKeys()
    .filter((key) => key !== fullKey(keepKey))
    .map((key) => {
      try {
        const entry = JSON.parse(storage().getItem(key) || "{}");
        return { key, at: Number(entry.at) || 0 };
      } catch (_) {
        return { key, at: 0 };
      }
    })
    .sort((a, b) => a.at - b.at);
  const overflow = Math.max(0, entries.length - MAX_ENTRIES + 1);
  entries.slice(0, overflow).forEach(({ key }) => {
    try {
      storage().removeItem(key);
    } catch (_) {
      /* ignore */
    }
  });
}

export function cacheGet(key) {
  const entry = readEntry(key);
  return entry ? entry.value : null;
}

export function cacheSet(key, value, ttlMs = DEFAULT_TTL_MS) {
  const store = storage();
  const entry = {
    value,
    at: Date.now(),
    expires: ttlMs > 0 ? Date.now() + ttlMs : 0,
  };
  try {
    store.setItem(fullKey(key), JSON.stringify(entry));
    return true;
  } catch (_) {
    // Quota exceeded: drop the oldest entries and retry once.
    try {
      evictOldest(key);
      store.setItem(fullKey(key), JSON.stringify(entry));
      return true;
    } catch (_) {
      return false;
    }
  }
}

export function cacheRemove(key) {
  try {
    storage().removeItem(fullKey(key));
  } catch (_) {
    /* ignore */
  }
}

/** Remove every cached entry, or only those whose key starts with a prefix. */
export function cacheClear(prefix = "") {
  const match = String(prefix || "");
  namespaceKeys().forEach((key) => {
    if (!match || key.startsWith(fullKey(match))) {
      try {
        storage().removeItem(key);
      } catch (_) {
        /* ignore */
      }
    }
  });
}

export function cacheStats() {
  const keys = namespaceKeys();
  let bytes = 0;
  keys.forEach((key) => {
    try {
      bytes += (storage().getItem(key) || "").length;
    } catch (_) {
      /* ignore */
    }
  });
  return { entries: keys.length, bytes, namespace: NAMESPACE };
}

/* ── Domain keys ─────────────────────────────────────────────────────────── */

function hash(value) {
  const text = String(value == null ? "" : value);
  let result = 5381;
  for (let index = 0; index < text.length; index += 1) {
    result = ((result << 5) + result + text.charCodeAt(index)) | 0;
  }
  return (result >>> 0).toString(36);
}

export function contextKey(sessionId, version) {
  return `context:${sessionId || "none"}:${version == null ? "latest" : version}`;
}

export function getCachedContext(sessionId, version) {
  return cacheGet(contextKey(sessionId, version));
}

export function setCachedContext(sessionId, version, payload) {
  return cacheSet(contextKey(sessionId, version), payload, 5 * 60 * 1000);
}

export function invalidateContext(sessionId) {
  cacheClear(`context:${sessionId || ""}`);
}

export function chartKey(seed) {
  return `chart:${hash(seed)}`;
}

export function getChartExplanation(seed) {
  return cacheGet(chartKey(seed));
}

export function setChartExplanation(seed, value) {
  return cacheSet(chartKey(seed), value, 30 * 60 * 1000);
}

export function conversationKey(sessionId) {
  return `conversation:${sessionId || "none"}`;
}

export function loadConversation(sessionId) {
  const value = cacheGet(conversationKey(sessionId));
  return Array.isArray(value) ? value : [];
}

export function saveConversation(sessionId, messages) {
  const trimmed = (messages || []).slice(-CONVERSATION_TURNS);
  return cacheSet(conversationKey(sessionId), trimmed, 0);
}

export function appendConversation(sessionId, message) {
  const messages = loadConversation(sessionId);
  messages.push({ ...message, at: message.at || Date.now() });
  saveConversation(sessionId, messages);
  return messages;
}

export function clearConversation(sessionId) {
  cacheRemove(conversationKey(sessionId));
}

export { hash, createMemoryStorage };
