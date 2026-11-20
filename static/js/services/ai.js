/* ==========================================================================
   Puter.js AI transport

   The only module that talks to a model.  Puter.js runs in the browser, so the
   app needs no API key, no server proxy and no provider-specific configuration.
   Everything here is defensive: the script may be blocked by a network policy,
   the user may be offline, a model may be busy, or a response may arrive in a
   different shape.  Callers always receive plain text or a friendly AIError —
   never a raw provider message.
   ========================================================================== */

import { MODEL_LABELS } from "./router.js";

export const PUTER_SCRIPT_URL = "https://js.puter.com/v2/";
export const DEFAULT_MODEL = "qwen";
export const DEFAULT_TIMEOUT_MS = 45000;

export class AIError extends Error {
  constructor(message, kind = "unknown", options = {}) {
    super(message);
    this.name = "AIError";
    this.kind = kind;
    this.detail = options.detail || "";
    this.model = options.model || "";
    if (options.cause) this.cause = options.cause;
  }
}

let loaderPromise = null;
let sessionAuthenticated = false;
let sessionStartedAt = Date.now();
let sessionTokens = 0;
let recentRates = [];
let authInitialization = null;

function emitUsage(detail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("aqat:ai-usage", { detail }));
}

function tokenCount(response, options = {}) {
  const usage = response && (response.usageDetails || response.usage);
  if (usage && typeof usage === "object") {
    const input = Number(usage.inputTokens ?? usage.input_tokens ?? usage.prompt_tokens ?? 0);
    const output = Number(usage.outputTokens ?? usage.output_tokens ?? usage.completion_tokens ?? 0);
    const total = Number(usage.total_tokens ?? usage.totalTokens ?? (input + output));
    if (Number.isFinite(total) && total > 0) return { count: total, reported: true };
  }
  const chars = [options.system, options.prompt, options.responseText, ...(options.history || []).map(x => x && (x.text || x.content))]
    .filter(Boolean).join(" ").length;
  return { count: Math.max(1, Math.ceil(chars / 4)), reported: false };
}

/** Clear any restored Puter identity before a new AQACT page session. */
export function initializePuterSession() {
  if (authInitialization) return authInitialization;
  authInitialization = ensurePuter().then(puter => {
    try { if (puter.auth && puter.auth.isSignedIn()) puter.auth.signOut(); } catch (_) { /* runtime versions vary */ }
    sessionAuthenticated = false;
    emitUsage({ type: "auth", authenticated: false });
    return true;
  }).catch(() => false);
  return authInitialization;
}

export async function signInPuter() {
  const puter = await ensurePuter();
  if (!puter.auth || typeof puter.auth.signIn !== "function") throw new AIError("Puter authentication is unavailable.", "auth");
  try { if (puter.auth.isSignedIn && puter.auth.isSignedIn()) puter.auth.signOut(); } catch (_) { /* sign-in below remains authoritative */ }
  await puter.auth.signIn();
  // Puter builds differ: some report sign-in synchronously, others settle a
  // moment later.  Never claim an AI session before the runtime confirms it.
  let confirmed = true;
  if (typeof puter.auth.isSignedIn === "function") {
    confirmed = Boolean(puter.auth.isSignedIn());
    if (!confirmed && typeof puter.auth.getUser === "function") {
      try { confirmed = Boolean(await puter.auth.getUser()); } catch (_) { /* keep false */ }
    }
  }
  if (!confirmed) {
    throw new AIError("Puter sign-in was not completed.", "auth");
  }
  sessionAuthenticated = true;
  emitUsage({ type: "auth", authenticated: true });
  refreshPuterUsage();
  return true;
}

export function signOutPuter() {
  try {
    if (typeof window !== "undefined" && window.puter?.auth?.signOut) window.puter.auth.signOut();
  } catch (_) { /* best effort during unload */ }
  sessionAuthenticated = false;
  emitUsage({ type: "auth", authenticated: false });
}

export async function refreshPuterUsage() {
  try {
    const puter = await ensurePuter();
    const usage = puter.auth && puter.auth.getMonthlyUsage ? await puter.auth.getMonthlyUsage() : null;
    emitUsage({ type: "allowance", usage });
    return usage;
  } catch (_) { return null; }
}

export function aiUsageSnapshot() {
  return { tokens: sessionTokens, recentRates: recentRates.slice(), elapsedMs: Date.now() - sessionStartedAt };
}

export function isAIAuthenticated() {
  return Boolean(sessionAuthenticated);
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", signOutPuter, { once: true });
  window.addEventListener("beforeunload", signOutPuter, { once: true });
}

export function puterAvailable() {
  return Boolean(
    typeof window !== "undefined" &&
      window.puter &&
      window.puter.ai &&
      typeof window.puter.ai.chat === "function"
  );
}

export function resetPuterLoader() {
  loaderPromise = null;
}

/**
 * Load the Puter.js runtime once.  Resolves with `window.puter` or rejects with
 * a friendly AIError when the script is unavailable.
 */
export function ensurePuter({ timeoutMs = 15000 } = {}) {
  if (puterAvailable()) return Promise.resolve(window.puter);
  if (typeof document === "undefined") {
    return Promise.reject(
      new AIError("The AI runtime is not available in this environment.", "unavailable")
    );
  }
  if (loaderPromise) return loaderPromise;

  loaderPromise = new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const existing = document.querySelector(`script[src="${PUTER_SCRIPT_URL}"]`);
    const script = existing || document.createElement("script");

    const finish = (error) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (error) {
        loaderPromise = null;
        reject(error);
      } else if (puterAvailable()) {
        resolve(window.puter);
      } else {
        loaderPromise = null;
        reject(
          new AIError("The AI runtime could not be started.", "unavailable")
        );
      }
    };

    timer = setTimeout(() => {
      finish(new AIError("The AI runtime took too long to load.", "timeout"));
    }, timeoutMs);

    if (existing) {
      // The tag is already in the document; poll briefly in case it is still
      // loading, then fail cleanly.
      const started = Date.now();
      const poll = setInterval(() => {
        if (puterAvailable()) {
          clearInterval(poll);
          finish(null);
        } else if (Date.now() - started > timeoutMs) {
          clearInterval(poll);
          finish(new AIError("The AI runtime took too long to load.", "timeout"));
        }
      }, 150);
      return;
    }

    script.src = PUTER_SCRIPT_URL;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.addEventListener("load", () => finish(null), { once: true });
    script.addEventListener(
      "error",
      () => finish(new AIError("The AI runtime could not be loaded.", "unavailable")),
      { once: true }
    );
    document.head.appendChild(script);
  });

  return loaderPromise;
}

/* ── Response normalisation ──────────────────────────────────────────────── */

function contentToText(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (typeof content === "number" || typeof content === "boolean") return String(content);
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part.text === "string") return part.text;
        if (part && typeof part.content === "string") return part.content;
        return "";
      })
      .filter(Boolean)
      .join("\n")
      .trim();
  }
  if (typeof content === "object") {
    if (typeof content.text === "string") return content.text;
    if (typeof content.content === "string") return content.content;
    if (Array.isArray(content.content)) return contentToText(content.content);
  }
  return "";
}

/** Pull the assistant text out of the many shapes Puter/OpenAI can return. */
export function normalizeResponse(response) {
  if (response == null) return "";
  if (typeof response === "string") return response.trim();
  if (typeof response === "number" || typeof response === "boolean") {
    return String(response);
  }
  if (Array.isArray(response)) return contentToText(response);
  if (typeof response !== "object") return "";

  const candidates = [
    response.message && response.message.content,
    response.message,
    response.choices &&
      response.choices[0] &&
      response.choices[0].message &&
      response.choices[0].message.content,
    response.choices && response.choices[0] && response.choices[0].text,
    response.content,
    response.text,
    response.result,
    response.output && response.output.text,
    response.data && response.data.text,
  ];
  for (const candidate of candidates) {
    const text = contentToText(candidate);
    if (text) return text.trim();
  }
  return "";
}

/* ── Error classification ────────────────────────────────────────────────── */

export function classifyError(error) {
  if (error instanceof AIError && error.kind) return error.kind;
  const text = String(
    (error && (error.detail || error.message)) || error || ""
  ).toLowerCase();
  if (!text) return "unknown";
  if (/(not loaded|not available|undefined is not|cannot read|script)/.test(text)) {
    return "unavailable";
  }
  if (/(offline|network|failed to fetch|fetch failed|connection|dns|econn)/.test(text)) {
    return "network";
  }
  if (/(rate|429|too many|quota|limit exceeded|busy)/.test(text)) {
    return "rate_limited";
  }
  if (/(timeout|timed out|abort)/.test(text)) return "timeout";
  if (/(auth|unauthor|forbidden|permission|401|403|api key|token)/.test(text)) {
    return "auth";
  }
  if (/(model|not found|404|400|invalid request|unsupported)/.test(text)) {
    return "model_unavailable";
  }
  return "unknown";
}

export function friendlyAIError(error) {
  switch (classifyError(error)) {
    case "network":
      return "The online models are unreachable right now. AQACT will keep working with local analysis.";
    case "rate_limited":
      return "The online models are busy right now. AQACT will retry another model or use local analysis.";
    case "timeout":
      return "The online models took too long to respond. AQACT will use a backup model or local analysis.";
    case "auth":
      return "Sign in to Puter to use AI features. Local analysis remains available without signing in.";
    case "model_unavailable":
      return "That model is unavailable. AQACT will try another model.";
    case "unavailable":
      return "The online AI layer could not start. AQACT will use local analysis.";
    default:
      return "The online assistant is unavailable. AQACT will use local analysis.";
  }
}

export function modelLabel(model) {
  return MODEL_LABELS[model] || String(model || "AI");
}

/* ── Messages and transport ──────────────────────────────────────────────── */

function historyMessages(history) {
  if (!Array.isArray(history)) return [];
  return history
    .slice(-8)
    .map((entry) => {
      const role = entry && entry.role === "assistant" ? "assistant" : "user";
      const text =
        entry && typeof entry.text === "string"
          ? entry.text
          : entry && typeof entry.content === "string"
            ? entry.content
            : "";
      return text ? { role, content: text } : null;
    })
    .filter(Boolean);
}

export function buildMessages(prompt, options = {}) {
  const messages = [];
  if (options.system) messages.push({ role: "system", content: String(options.system) });
  historyMessages(options.history).forEach((message) => messages.push(message));
  if (options.image) {
    messages.push({
      role: "user",
      content: [
        { type: "text", text: String(prompt || "") },
        { type: "image_url", image_url: { url: String(options.image) } },
      ],
    });
  } else {
    messages.push({ role: "user", content: String(prompt || "") });
  }
  return messages;
}

function withTimeout(promise, timeoutMs, model) {
  if (!timeoutMs || timeoutMs <= 0 || typeof setTimeout !== "function") {
    return Promise.resolve(promise);
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(
        new AIError("The model took too long to respond.", "timeout", { model })
      );
    }, timeoutMs);
    Promise.resolve(promise).then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

async function callPuter(puter, prompt, model, options) {
  const chat = puter.ai.chat;
  const requestOptions = { model };
  if (options.temperature != null) requestOptions.temperature = options.temperature;

  const messages = buildMessages(prompt, options);
  try {
    return await chat.call(puter.ai, messages, requestOptions);
  } catch (error) {
    // Some Puter builds only accept a prompt string plus an optional image URL.
    // Retry that shape once; never retry a plain text request twice.
    if (options.image) {
      return await chat.call(puter.ai, String(prompt || ""), String(options.image), requestOptions);
    }
    if (messages.length === 1) throw error;
    return await chat.call(puter.ai, String(prompt || ""), requestOptions);
  }
}

/**
 * Ask one model and return normalised text.
 *
 * @param {string} prompt
 * @param {string} [model]
 * @param {{system?:string, history?:Array, image?:string, timeoutMs?:number,
 *          temperature?:number}} [options]
 * @returns {Promise<string>}
 */
export async function askAI(prompt, model = DEFAULT_MODEL, options = {}) {
  const selected = model || DEFAULT_MODEL;
  const puter = await ensurePuter({ timeoutMs: options.loadTimeoutMs || 15000 });
  if (puter.auth && !sessionAuthenticated) {
    throw new AIError("Sign in to Puter to start this session's AI access.", "auth");
  }
  let response;
  try {
    response = await withTimeout(
      callPuter(puter, prompt, selected, options),
      options.timeoutMs == null ? DEFAULT_TIMEOUT_MS : options.timeoutMs,
      selected
    );
  } catch (error) {
    if (error instanceof AIError) throw error;
    throw new AIError(friendlyAIError(error), classifyError(error), {
      detail: String((error && (error.message || error)) || ""),
      model: selected,
      cause: error,
    });
  }

  const text = normalizeResponse(response);
  if (!text) {
    throw new AIError("The model returned an empty answer.", "empty", {
      model: selected,
    });
  }
  const usage = tokenCount(response, { ...options, prompt, responseText: text });
  sessionTokens += usage.count;
  recentRates.push({ at: Date.now(), count: usage.count });
  recentRates = recentRates.slice(-20);
  emitUsage({ type: "request", tokens: sessionTokens, added: usage.count, reported: usage.reported,
    estimatePerMinute: sessionTokens / Math.max(1, (Date.now() - sessionStartedAt) / 60000) });
  if (typeof options.onUsage === "function") options.onUsage({ ...usage, sessionTokens });
  return text;
}

/** Runtime snapshot for the drawer badge. */
export function aiStatus() {
  const online =
    typeof navigator === "undefined" || navigator.onLine == null
      ? true
      : Boolean(navigator.onLine);
  return {
    online,
    ready: puterAvailable(),
    authenticated: sessionAuthenticated,
    layer: "puter",
  };
}
