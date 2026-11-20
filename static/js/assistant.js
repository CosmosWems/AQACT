/* ==========================================================================
   AQACT assistant drawer (Puter.js edition)

   The browser owns the AI layer.  Dataset context, attachments, chart images
   and answers are prepared locally; Puter.js supplies model access without an
   API key.  When the online layer is unavailable the drawer falls back to the
   deterministic local engine without losing the conversation.

   Loaded as an ES module after app.js and enhancements.js.
   ========================================================================== */

import { askDatasetAssistant } from "./services/datasetAssistant.js";
import {
  interpretChart,
  figureSummary,
  CHART_QUESTION,
} from "./services/chartInterpreter.js";
import {
  aiStatus,
  ensurePuter,
  friendlyAIError,
  initializePuterSession,
  isAIAuthenticated,
  modelLabel,
  signInPuter,
  signOutPuter,
} from "./services/ai.js";
import {
  MODEL_REGISTRY,
  classifyTask,
  isSupportedModel,
  taskLabel,
} from "./services/router.js";
import {
  cacheGet,
  cacheSet,
  hash,
  getCachedContext,
  setCachedContext,
  invalidateContext,
  loadConversation,
  appendConversation,
  saveConversation,
  clearConversation,
  getChartExplanation,
  setChartExplanation,
} from "./services/cache.js";

const $ = (id) => document.getElementById(id);

const MODEL_STORAGE_KEY = "aqat-ai-model";
const AI_AUTH_MESSAGE =
  "Sign in to Puter to use AI features. Browsing, uploads, statistics, plots and local analysis stay available without signing in.";

const state = {
  mode: "simple",
  model: "",
  imageFile: null,
  dataFile: null,
  status: null,
  historyLoaded: null,
  suggestionsKey: null,
  open: false,
  pinned: false,
  lastFocus: null,
  busy: false,
  context: null,
  quota: null,
  limitNotice: "",
};

/* ── HTTP helpers ────────────────────────────────────────────────────────── */

async function assistantRequest(url, options) {
  const res = await fetch(url, options || {});
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    data = { error: text.slice(0, 240) || `Request failed (${res.status})` };
  }
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.code = data.code;
    err.hint = data.hint;
    err.payload = data;
    err.requestId = data.request_id;
    err.safe = Boolean(data.error); // backend messages are written for users
    throw err;
  }
  return data;
}

function sessionId() {
  return (window.STATE && window.STATE.sessionId) || null;
}

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch (_) {
    /* private mode: settings are optional */
  }
}

function storedModel() {
  const saved = storageGet(MODEL_STORAGE_KEY) || "";
  return isSupportedModel(saved) ? saved : "";
}

function selectedModel() {
  return state.model || "";
}

function setModel(model, options = {}) {
  const next = isSupportedModel(model) ? String(model) : "";
  state.model = next;
  const select = $("assistant-model");
  if (select) select.value = next;
  if (options.persist !== false) storageSet(MODEL_STORAGE_KEY, next || null);
  return next;
}

function isCompactAssistant() {
  return Boolean(window.matchMedia && window.matchMedia("(max-width: 1199px)").matches);
}

function sendQuotaBeacon(path, payload = {}, sid = sessionId()) {
  if (!sid || typeof navigator === "undefined" || !navigator.sendBeacon) return;
  try {
    const blob = new Blob([JSON.stringify({ session_id: sid, ...payload })], {
      type: "application/json",
    });
    navigator.sendBeacon(path, blob);
  } catch (_) {
    /* best-effort telemetry only */
  }
}

function messageContainer() {
  return $("assistant-messages");
}

function scrollToBottom() {
  const container = messageContainer();
  if (container) container.scrollTop = container.scrollHeight;
}

function setContext(text) {
  const el = $("assistant-context");
  if (el) el.textContent = text;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/* ── AI access and usage guard ───────────────────────────────────────────── */

function showLimitNotice(message) {
  state.limitNotice = String(message || "");
  const banner = $("ai-limit-banner");
  if (banner) {
    banner.textContent = state.limitNotice;
    banner.hidden = !state.limitNotice;
  }
}

function renderQuota(quota) {
  const summary = $("ai-quota-summary");
  const banner = $("ai-limit-banner");
  if (!summary) return;
  if (!quota || !quota.counts || !quota.limits) {
    summary.textContent = "Requests: load a dataset, then sign in to Puter to use AI.";
    if (banner && !state.limitNotice) banner.hidden = true;
    return;
  }
  const counts = quota.counts;
  const limits = quota.limits;
  let text =
    `Requests: ${counts.session || 0}/${limits.session} session · ` +
    `${counts.hour || 0}/${limits.hour} this hour · ${counts.day || 0}/${limits.day} today`;
  if (quota.blocked) text += ` · paused ${quota.retry_after_seconds || 0}s`;
  summary.textContent = text;
  if (banner) {
    if (quota.blocked) {
      banner.hidden = false;
      banner.textContent =
        state.limitNotice ||
        `AI access is paused for ${quota.retry_after_seconds || 0} seconds. You can sign in again, but model requests stay blocked until the cooldown ends.`;
    } else {
      banner.hidden = true;
    }
  }
}

async function refreshQuota() {
  const sid = sessionId();
  if (!sid) {
    state.quota = null;
    renderQuota(null);
    return null;
  }
  try {
    const data = await assistantRequest(
      `/api/assistant/quota?session_id=${encodeURIComponent(sid)}`
    );
    state.quota = data.quota || null;
    renderQuota(state.quota);
    return state.quota;
  } catch (_) {
    renderQuota(state.quota);
    return state.quota;
  }
}

async function syncQuotaSession(authenticated) {
  const sid = sessionId();
  if (!sid) return null;
  try {
    const data = await assistantRequest(
      authenticated ? "/api/assistant/quota/session" : "/api/assistant/quota/release",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      }
    );
    if (data.quota) {
      state.quota = data.quota;
      renderQuota(state.quota);
    }
    return data;
  } catch (_) {
    return null;
  }
}

async function endAISession(reason = "user", sid = sessionId()) {
  signOutPuter();
  if (!sid) return;
  try {
    const data = await assistantRequest("/api/assistant/quota/release", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid, reason }),
    });
    if (data.quota) {
      state.quota = data.quota;
      renderQuota(state.quota);
    }
  } catch (_) {
    sendQuotaBeacon("/api/assistant/quota/release", { reason }, sid);
  }
}

function isAuthFailure(error) {
  const code = String((error && (error.code || error.kind)) || "");
  const message = String((error && error.message) || error || "").toLowerCase();
  return (
    code === "auth" ||
    code === "ai_auth_required" ||
    /sign in|authentication|unauthor/i.test(message)
  );
}

async function authorizeAIAccess() {
  if (!isAIAuthenticated()) return { ok: false, message: AI_AUTH_MESSAGE };
  const sid = sessionId();
  if (!sid) {
    return { ok: false, message: "Load a dataset before using AI features." };
  }
  const quota = await refreshQuota();
  if (!quota) {
    return {
      ok: false,
      message: "AI usage could not be verified. Try again in a moment.",
    };
  }
  if (quota.blocked) {
    const message =
      state.limitNotice ||
      `AI access is paused for ${quota.retry_after_seconds || 0} seconds after a request limit was reached. Local analysis is still available.`;
    showLimitNotice(message);
    await endAISession("cooldown", sid);
    return { ok: false, message };
  }
  if (!quota.authenticated) await syncQuotaSession(true);
  return { ok: true };
}

async function ensureAIAccess() {
  if (!isAIAuthenticated()) {
    try {
      await signInPuter();
      await syncQuotaSession(true);
    } catch (error) {
      return {
        ok: false,
        message: `${friendlyAIError(error)} ${AI_AUTH_MESSAGE}`,
      };
    }
  }
  return authorizeAIAccess();
}

async function reserveAIRequest(task, model) {
  const sid = sessionId();
  if (!sid) return { ok: false, message: AI_AUTH_MESSAGE };
  try {
    const data = await assistantRequest("/api/assistant/quota/consume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sid,
        authenticated: isAIAuthenticated(),
        model: model || "",
        task: task || "chat",
      }),
    });
    state.quota = data;
    renderQuota(data);
    return { ok: true, quota: data };
  } catch (error) {
    if (error.payload) {
      state.quota = error.payload;
      renderQuota(error.payload);
    }
    if (error.code === "ai_auth_required") {
      return { ok: false, message: AI_AUTH_MESSAGE };
    }
    if (error.code === "ai_quota_exceeded" || error.code === "ai_quota_cooldown") {
      showLimitNotice(error.message);
      await endAISession(error.code, sid);
      return { ok: false, message: error.message };
    }
    return {
      ok: false,
      message: "AI usage could not be verified. Sign in again before retrying.",
    };
  }
}

/* ── Rendering ───────────────────────────────────────────────────────────── */

function showEmptyState() {
  const container = messageContainer();
  if (!container) return;
  container.innerHTML =
    '<div class="assistant-empty">' +
    '<svg class="ai-icon large"><use href="#icon-ai-head"/></svg>' +
    "<strong>Ask about your data</strong>" +
    "<p>I can summarise the dataset, explain statistics and analysis results, find trends and anomalies, " +
    "compare stations, and read a chart or figure. Try a quick action above, or type a question.</p>" +
    "</div>";
}

function makeMessage(role, label, text) {
  const message = document.createElement("div");
  message.className = `assistant-msg ${role}`;
  const tag = document.createElement("span");
  tag.className = "msg-label";
  tag.textContent = label;
  const body = document.createElement("div");
  body.textContent = text || "";
  message.append(tag, body);
  return message;
}

function appendMessage(message) {
  const container = messageContainer();
  if (!container) return;
  const empty = container.querySelector(".assistant-empty");
  if (empty) empty.remove();
  container.appendChild(message);
  scrollToBottom();
}

function appendUser(text) {
  appendMessage(makeMessage("user", "You", text));
}

function appendBot(text, label) {
  appendMessage(makeMessage("bot", label || "AQACT Assistant", text));
}

function addBulletList(parent, items, title) {
  if (!items || !items.length) return;
  if (title) {
    const heading = document.createElement("div");
    heading.className = "msg-subhead";
    heading.textContent = title;
    parent.appendChild(heading);
  }
  const list = document.createElement("ul");
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : item.text || item.title || "";
    list.appendChild(li);
  });
  parent.appendChild(list);
}

function showTyping(label) {
  const container = messageContainer();
  if (!container) return { remove() {} };
  const message = document.createElement("div");
  message.className = "assistant-msg bot typing";
  const tag = document.createElement("span");
  tag.className = "msg-label";
  tag.textContent = label || "Puter · thinking";
  const dots = document.createElement("div");
  dots.className = "typing-dots";
  dots.innerHTML = "<span></span><span></span><span></span>";
  message.append(tag, dots);
  const empty = container.querySelector(".assistant-empty");
  if (empty) empty.remove();
  container.appendChild(message);
  scrollToBottom();
  return {
    remove() {
      message.remove();
    },
  };
}

function renderSuggestions(list) {
  const container = $("assistant-suggestions");
  if (!container) return;
  container.replaceChildren();
  (list || []).slice(0, 6).forEach((text) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "suggestion-chip";
    chip.textContent = text;
    chip.addEventListener("click", () => ask(text));
    container.appendChild(chip);
  });
}

function answerLabel(payload) {
  if (payload.intent_label) return payload.intent_label;
  if (payload.used_cloud === false) return "Local analysis";
  const model = payload.modelLabel || modelLabel(payload.model);
  return model ? `Puter · ${model}` : "Puter · AI";
}

function makeBotMessage(payload, options = {}) {
  const text = payload.answer || payload.text || payload.summary || "I do not have an answer for that yet.";
  const message = makeMessage("bot", answerLabel(payload), text);
  message.classList.add(payload.used_cloud === false ? "offline" : "cloud");

  const bullets = payload.bullets || (payload.findings || []).map((item) => item.text || item.title);
  if (bullets && bullets.length) addBulletList(message, bullets);
  const caveats = payload.caveats || (payload.caveat ? [payload.caveat] : []);
  if (caveats.length) addBulletList(message, caveats.slice(0, 3), "Caveats");

  const note = payload.recoveryNote || payload.note || "";
  if (note) {
    const line = document.createElement("p");
    line.className = "msg-note";
    line.textContent = note;
    message.appendChild(line);
  }

  if (options.actions !== false && payload.used_cloud !== false && payload.question) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    if (payload.mode === "simple") {
      const technical = document.createElement("button");
      technical.type = "button";
      technical.textContent = "Technical version";
      technical.addEventListener("click", () =>
        ask(payload.question, {
          task: payload.task,
          modeOverride: "technical",
          skipHistory: true,
        })
      );
      actions.appendChild(technical);
    }
    if (payload.task === "dataset" || payload.task === "statistics") {
      const recommend = document.createElement("button");
      recommend.type = "button";
      recommend.textContent = "Recommend next steps";
      recommend.addEventListener("click", () =>
        ask("Give practical, evidence-based recommendations for this dataset.", {
          task: "research",
        })
      );
      actions.appendChild(recommend);
    }
    if (actions.childElementCount) message.appendChild(actions);
  }
  return message;
}

function persistAssistant(payload) {
  const sid = sessionId();
  if (!sid) return;
  appendConversation(sid, {
    role: "assistant",
    text: payload.answer || payload.text || "",
    label: answerLabel(payload),
    model: payload.model || "",
    modelLabel: payload.modelLabel || "",
    task: payload.task || "",
    mode: payload.mode || state.mode,
    question: payload.question || "",
    bullets: payload.bullets || [],
    caveats: payload.caveats || [],
    suggestions: payload.suggestions || [],
    recoveryNote: payload.recoveryNote || "",
    used_cloud: payload.used_cloud !== false,
    at: Date.now(),
  });
}

function appendAnswerPayload(payload, options = {}) {
  const message = makeBotMessage(payload, options);
  appendMessage(message);
  if (payload.suggestions && payload.suggestions.length) renderSuggestions(payload.suggestions);
  if (options.persist !== false) persistAssistant(payload);
  if (payload.shape && window.STATE) window.STATE.shape = payload.shape;
  return message;
}

function friendlyError(err) {
  if (err && err.safe && err.message) {
    return `${err.message}${err.hint ? ` ${err.hint}` : ""}`;
  }
  return friendlyAIError(err);
}

function addError(err) {
  appendBot(
    `I could not complete that request. ${friendlyError(err)} ` +
      "Every local analysis, chart and export still works."
  );
}

/* ── Conversation persistence ────────────────────────────────────────────── */

function renderStoredEntry(entry) {
  if (entry.role === "user") {
    appendUser(entry.text || "");
    return;
  }
  const message = makeBotMessage(
    {
      answer: entry.text || "",
      bullets: entry.bullets || [],
      caveats: entry.caveats || [],
      recoveryNote: entry.recoveryNote || "",
      model: entry.model || "",
      modelLabel: entry.modelLabel || "",
      task: entry.task || "",
      mode: entry.mode || "simple",
      question: entry.question || "",
      used_cloud: entry.used_cloud !== false,
      intent_label: entry.label || "",
    },
    { actions: false }
  );
  appendMessage(message);
}

function renderConversation() {
  const sid = sessionId();
  const container = messageContainer();
  if (container) container.replaceChildren();
  if (!sid) {
    showEmptyState();
    return;
  }
  const messages = loadConversation(sid);
  if (!messages.length) {
    showEmptyState();
    return;
  }
  messages.forEach(renderStoredEntry);
  state.historyLoaded = sid;
}

let historyPromise = null;

function loadHistory() {
  if (historyPromise) return historyPromise;
  historyPromise = loadHistoryNow().finally(() => {
    historyPromise = null;
  });
  return historyPromise;
}

async function loadHistoryNow() {
  const sid = sessionId();
  if (!sid) {
    showEmptyState();
    return;
  }
  const local = loadConversation(sid);
  if (local.length) {
    renderConversation();
    return;
  }
  if (state.historyLoaded === sid) return;
  try {
    const data = await assistantRequest(
      `/api/assistant/history?session_id=${encodeURIComponent(sid)}`
    );
    const messages = data.messages || [];
    const container = messageContainer();
    if (container) container.replaceChildren();
    if (!messages.length) {
      showEmptyState();
    } else {
      messages.forEach((entry) => {
        renderStoredEntry({
          role: entry.role === "user" ? "user" : "assistant",
          text: entry.text || entry.content || "",
          used_cloud: false,
        });
      });
      saveConversation(
        sid,
        messages.map((entry) => ({
          role: entry.role === "user" ? "user" : "assistant",
          text: entry.text || entry.content || "",
          used_cloud: false,
          at: Date.now(),
        }))
      );
    }
    state.historyLoaded = sid;
  } catch (_) {
    showEmptyState();
  }
}

function postHistory(sid, role, text) {
  if (!sid || !text) return;
  fetch("/api/assistant/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sid,
      role,
      text: String(text).slice(0, 4000),
    }),
    keepalive: true,
  }).catch(() => {
    /* continuity is best-effort; the local cache is authoritative */
  });
}

function priorHistory(sid) {
  return loadConversation(sid)
    .filter((entry) => entry && (entry.role === "user" || entry.role === "assistant"))
    .slice(-8)
    .map((entry) => ({
      role: entry.role === "assistant" ? "assistant" : "user",
      text: entry.text || "",
    }))
    .filter((entry) => entry.text);
}

/* ── Local context and attachments ───────────────────────────────────────── */

async function fetchContext(sid, force = false) {
  if (!force) {
    const cached = getCachedContext(sid, "latest");
    if (cached) return cached;
  }
  const data = await assistantRequest("/api/assistant/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sid }),
  });
  if (data && data.context) {
    setCachedContext(sid, "latest", data);
    if (data.version != null) setCachedContext(sid, data.version, data);
    state.context = data;
  }
  return data;
}

async function uploadAttachment(file) {
  const body = new FormData();
  body.append("session_id", sessionId() || "");
  body.append("data_file", file);
  const data = await assistantRequest("/api/assistant/attachment", {
    method: "POST",
    body,
  });
  return data.attachment || null;
}

/* ── Images ──────────────────────────────────────────────────────────────── */

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("The image could not be read."));
    reader.readAsDataURL(file);
  });
}

/** Downscale and re-encode an uploaded chart before it leaves the browser. */
async function prepareImage(file, maxDim = 1400, quality = 0.85) {
  if (!file) return null;
  if (typeof document === "undefined" || typeof createImageBitmap !== "function") {
    return fileToDataUrl(file);
  }
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return fileToDataUrl(file);
    context.drawImage(bitmap, 0, 0, width, height);
    if (typeof bitmap.close === "function") bitmap.close();
    return canvas.toDataURL("image/jpeg", quality);
  } catch (_) {
    return fileToDataUrl(file);
  }
}

function currentFigure() {
  const s = window.STATE || {};
  return s.lastFigure || (s._lastPlot && s._lastPlot.fig) || null;
}

async function captureChartImage() {
  const el = $("live-plot");
  if (!el || !el.data || typeof window.Plotly === "undefined") return null;
  try {
    return await window.Plotly.toImage(el, {
      format: "jpeg",
      width: 1200,
      height: 700,
      scale: 1,
    });
  } catch (_) {
    return null;
  }
}

/* ── Status and suggestions ──────────────────────────────────────────────── */

function applyStatus(status) {
  state.status = status || null;
  const runtime = aiStatus();
  const online = Boolean(runtime.online);
  const health = status || {};

  const badge = $("assistant-status");
  const blocked = Boolean(state.quota && state.quota.blocked);
  const ready =
    online && health.mode === "puter" && Boolean(runtime.ready && runtime.authenticated) && !blocked;
  if (badge) {
    badge.textContent = blocked
      ? "Puter · paused"
      : ready
        ? "Puter · signed in"
        : online && runtime.ready && !runtime.authenticated
          ? "Puter · sign in required"
          : online
            ? "Puter · local fallback"
            : "Offline · local analysis";
    badge.classList.toggle("online", ready);
    badge.classList.toggle("offline", !ready);
    badge.title = blocked
      ? state.limitNotice || "AI access is paused until the cooldown ends. Local analysis is still available."
      : ready
        ? "Puter.js runs in your browser. No API key, no server proxy. Models are routed automatically."
        : "Sign in to Puter for model answers. Deterministic local analysis still works.";
  }

  const subtitle = $("assistant-subtitle");
  if (subtitle) {
    subtitle.textContent = blocked
      ? "Limit reached · sign in after the cooldown"
      : ready
        ? `Puter AI · ${selectedModel() ? `${MODEL_REGISTRY.find((m) => m.id === selectedModel())?.label || selectedModel()}` : "auto route"}`
        : online && runtime.ready && !runtime.authenticated
          ? "Sign in for online answers"
          : online
            ? "Local analysis ready"
            : "Offline · local analysis";
  }

  const disclaimer = $("assistant-disclaimer");
  if (disclaimer) {
    disclaimer.textContent =
      "AI answers run in your browser through Puter.js from a compact local summary of your data and any attachment you add. " +
      "No API keys are required. Verify important results before publication.";
  }

  const fab = $("assistant-fab");
  if (fab) fab.setAttribute("aria-label", online ? "Open the AI assistant" : "Open the offline assistant");

  const modeLabelEl = $("app-mode-label");
  if (modeLabelEl) modeLabelEl.textContent = "Local-first · Puter AI";
  const version = $("app-mode-version");
  if (version) {
    const base = version.dataset.baseVersion || version.textContent.split("·")[0].trim();
    version.dataset.baseVersion = base;
    version.textContent = `${base} · analysis local · AI keyless`;
  }
}

async function refreshStatus() {
  try {
    const data = await assistantRequest("/api/assistant/health");
    state.status = data;
    applyStatus(data);
  } catch (_) {
    applyStatus(null);
  }
  await refreshQuota();
  applyStatus(state.status);
}

async function loadSuggestions(force) {
  const sid = sessionId();
  const key = sid || "none";
  if (!force && state.suggestionsKey === key) return;
  state.suggestionsKey = key;
  try {
    const url = `/api/assistant/suggestions${sid ? `?session_id=${encodeURIComponent(sid)}` : ""}`;
    const data = await assistantRequest(url);
    renderSuggestions(data.suggestions || []);
  } catch (_) {
    renderSuggestions([
      "What can you do?",
      "Summarise this dataset",
      "Explain this chart",
      "What is PM2.5?",
    ]);
  }
}

function resetForSession() {
  endAISession("session-reset", state.quotaSessionId || sessionId());
  state.quotaSessionId = sessionId();
  state.historyLoaded = null;
  state.suggestionsKey = null;
  state.context = null;
  state.quota = null;
  showLimitNotice("");
  invalidateContext(sessionId());
  const container = messageContainer();
  if (container) container.replaceChildren();
  showEmptyState();
  loadHistory();
  loadSuggestions(true);
  if (typeof window.updateAssistantContext === "function") window.updateAssistantContext();
}

/* ── Input state ─────────────────────────────────────────────────────────── */

function setMode(mode) {
  state.mode = mode === "technical" ? "technical" : "simple";
  const button = $("assistant-mode");
  if (button) {
    button.dataset.mode = state.mode;
    button.textContent = state.mode === "technical" ? "Technical language" : "Simple language";
    button.classList.toggle("active", state.mode === "technical");
    button.setAttribute("aria-pressed", state.mode === "technical" ? "true" : "false");
  }
}

function cycleAssistantMode() {
  setMode(state.mode === "simple" ? "technical" : "simple");
}
window.cycleAssistantMode = cycleAssistantMode;

function setFile(kind, file) {
  const isImage = kind === "image";
  const key = isImage ? "imageFile" : "dataFile";
  const inputId = isImage ? "assistant-image" : "assistant-data";
  state[key] = file || null;
  const label = document.querySelector(`label[for="${inputId}"]`);
  if (!label) return;
  let name = $(`${inputId}-name`);
  if (!name) {
    name = document.createElement("span");
    name.id = `${inputId}-name`;
    name.className = "chip-file-name";
    label.appendChild(name);
  }
  name.textContent = file ? `· ${file.name}` : "";
  label.classList.toggle("active", Boolean(file));
  label.title = file
    ? `${file.name} (${formatBytes(file.size)})`
    : isImage
      ? "Attach a chart screenshot for visual reasoning"
      : "Attach a data file for comparison or extra context";
  const input = $(inputId);
  if (!file && input) input.value = "";
}

function setImageFile(file) {
  setFile("image", file);
}

function setDataFile(file) {
  setFile("data", file);
}

function setBusy(busy) {
  state.busy = Boolean(busy);
  const button = $("assistant-send");
  if (button) {
    button.disabled = state.busy;
    button.textContent = state.busy ? "Thinking…" : "Send";
  }
  const form = $("assistant-form");
  if (form) form.classList.toggle("busy", state.busy);
}

function describeUserTurn(text, imageFile, dataFile, hasChartImage) {
  const parts = [];
  if (text) parts.push(text);
  if (imageFile) parts.push(`[chart image] ${imageFile.name}`);
  else if (hasChartImage) parts.push("[chart] current figure");
  if (dataFile) parts.push(`[data] ${dataFile.name}`);
  return parts.join("\n");
}

/* ── Asking ──────────────────────────────────────────────────────────────── */

function localFallback({ sid, question, image, mode, error, task }) {
  const reason = friendlyAIError(error);
  const call = image
    ? assistantRequest("/api/assistant/plot_image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sid,
          question,
          image,
        }),
      })
    : assistantRequest("/api/assistant/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, question, mode }),
      });
  return call.then((data) => ({
    ...data,
    task,
    mode,
    question,
    used_cloud: false,
    provider: "local",
    model: data.model || "local-rules",
    modelLabel: "Local rules",
    recoveryNote: `${reason} This answer comes from AQACT's deterministic local engine.`,
    persistedByServer: true,
  }));
}

async function ask(question, opts = {}) {
  if (state.busy) return;
  const text = String(question == null ? ($("assistant-input") || {}).value || "" : question).trim();
  const imageFile = opts.imageFile || state.imageFile;
  const dataFile = opts.dataFile || state.dataFile;
  const suppliedImage = opts.imageDataUrl || null;
  if (!text && !imageFile && !dataFile && !suppliedImage) return;

  const sid = sessionId();
  if (!sid) {
    appendUser(describeUserTurn(text, imageFile, dataFile, Boolean(suppliedImage)));
    if (/what can you do|help|capabilit/i.test(text)) {
      appendBot(
        "I am a keyless AI analyst powered by Puter.js. I can summarise a dataset, explain statistics and analysis results, " +
          "find trends and anomalies, compare stations, write report summaries, and interpret the current chart or an attached screenshot. " +
          "Open the demo dataset or upload a file to begin."
      );
    } else {
      appendBot(
        "Load a dataset first — choose “Explore demo dataset” on the Upload screen or add your own CSV/Excel file. " +
          "Then I can answer questions about the actual readings."
      );
    }
    const input = $("assistant-input");
    if (input) input.value = "";
    setImageFile(null);
    setDataFile(null);
    return;
  }

  const taskHint = opts.task || (imageFile || suppliedImage ? "chart" : undefined);
  const task = classifyTask(text, taskHint);
  const mode = opts.modeOverride || state.mode;
  const history = opts.skipHistory ? [] : priorHistory(sid);
  const preferredModel = isSupportedModel(opts.model) ? String(opts.model) : selectedModel();

  const access = await ensureAIAccess();
  if (!access.ok) {
    appendBot(access.message || AI_AUTH_MESSAGE);
    return;
  }

  const userTurn = describeUserTurn(text, imageFile, dataFile, Boolean(suppliedImage));
  appendUser(userTurn);
  appendConversation(sid, { role: "user", text: userTurn, at: Date.now() });

  const input = $("assistant-input");
  if (input) input.value = "";
  setImageFile(null);
  setDataFile(null);
  setBusy(true);
  const typing = showTyping(`Puter · ${taskLabel(task)}`);

  try {
    let image = suppliedImage;
    if (!image && imageFile) image = await prepareImage(imageFile);

    let attachment = null;
    let attachmentNote = "";
    if (dataFile) {
      try {
        attachment = await uploadAttachment(dataFile);
      } catch (error) {
        attachmentNote = `${friendlyError(error)} The answer uses the active dataset only.`;
      }
    }

    let contextPayload = null;
    try {
      contextPayload = await fetchContext(sid);
    } catch (_) {
      contextPayload = state.context;
    }
    const contextText = (contextPayload && contextPayload.context_text) || "{}";
    const figure = opts.figure || currentFigure();
    const figureText = figureSummary(figure);

    let payload = null;
    let fromCache = false;

    if (image || task === "chart") {
      const seed = `chart|${preferredModel || "auto"}|${mode}|${text || CHART_QUESTION}|${hash(image || "")}|${hash(figureText)}`;
      const cached = getChartExplanation(seed);
      if (cached) {
        payload = { ...cached, fromCache: true, recoveryNote: "Answered instantly from the local AI cache." };
        fromCache = true;
      }
      if (!payload) {
        const reservation = await reserveAIRequest(task, preferredModel);
        if (!reservation.ok) {
          const blocked = new Error(reservation.message);
          blocked.safe = true;
          throw blocked;
        }
        try {
          payload = await interpretChart({
            image,
            figure,
            figureText,
            contextText,
            question: text || CHART_QUESTION,
            mode,
            history,
            model: preferredModel || undefined,
          });
          setChartExplanation(seed, payload);
        } catch (error) {
          if (isAuthFailure(error)) throw error;
          payload = await localFallback({
            sid,
            question: text || CHART_QUESTION,
            image,
            mode,
            error,
            task: "chart",
          });
        }
      }
    } else {
      const cacheKey = `qa|${sid}|${(contextPayload && contextPayload.version) || "latest"}|${mode}|${
        preferredModel || "auto"
      }|${hash(text)}|${attachment ? hash(JSON.stringify(attachment)) : ""}`;
      const cached = cacheGet(cacheKey);
      if (cached) {
        payload = { ...cached, fromCache: true, recoveryNote: "Answered instantly from the local AI cache." };
        fromCache = true;
      }
      if (!payload) {
        const reservation = await reserveAIRequest(task, preferredModel);
        if (!reservation.ok) {
          const blocked = new Error(reservation.message);
          blocked.safe = true;
          throw blocked;
        }
        try {
          payload = await askDatasetAssistant({
            question: text,
            contextText,
            attachment,
            mode,
            task,
            history,
            model: preferredModel || undefined,
          });
          cacheSet(cacheKey, payload, 10 * 60 * 1000);
        } catch (error) {
          if (isAuthFailure(error)) throw error;
          payload = await localFallback({ sid, question: text, mode, error, task });
        }
      }
    }

    typing.remove();
    if (payload.used_cloud !== false) {
      payload.used_cloud = true;
      payload.provider = "puter";
      payload.intent_label = `${taskLabel(payload.task || task)} · ${payload.modelLabel || modelLabel(payload.model)}`;
      postHistory(sid, "user", userTurn);
      postHistory(sid, "assistant", payload.answer || "");
    }
    if (attachmentNote) {
      payload.caveats = [...(payload.caveats || []), attachmentNote].slice(0, 4);
    }
    if (fromCache) payload.used_cloud = true;
    appendAnswerPayload(payload);

    if (contextPayload && contextPayload.shape) {
      window.STATE.shape = contextPayload.shape;
      const modelText = payload.used_cloud === false ? "local engine" : payload.modelLabel || modelLabel(payload.model);
      setContext(
        `Session ${sid.slice(0, 8)} · ${Number(contextPayload.shape[0] || 0).toLocaleString()} rows × ` +
          `${Number(contextPayload.shape[1] || 0)} columns · ${modelText}`
      );
    }
  } catch (err) {
    typing.remove();
    addError(err);
  } finally {
    setBusy(false);
  }
}
window.aqatAssistantAsk = ask;

/* ── One-click actions ───────────────────────────────────────────────────── */

async function runAction(fn) {
  openAssistant();
  try {
    await loadHistory();
    const payload = await fn();
    if (payload) return payload;
  } catch (error) {
    addError(error);
  }
  return null;
}

function explainDatasetAction() {
  return runAction(async () => {
    if (!sessionId()) {
      await ask("What can you do?");
      return null;
    }
    return ask(
      "Summarise this dataset: what it contains, time range, coverage, key variables, and the most important data-quality caveats.",
      { task: "dataset" }
    );
  });
}

async function explainChartAction() {
  openAssistant();
  await loadHistory();
  if (!sessionId()) {
    await ask("What can you do?");
    return null;
  }
  const image = await captureChartImage();
  const figure = currentFigure();
  if (!image && !figure) {
    appendBot(
      "Generate a chart on the Plots screen first, or attach a chart screenshot with the “Chart image” button. " +
        "Then I can interpret it."
    );
    return null;
  }
  return ask(CHART_QUESTION, { task: "chart", imageDataUrl: image, figure });
}

function detectPatternsAction() {
  return runAction(() => {
    if (!sessionId()) return ask("What can you do?");
    return ask(
      "What trends, anomalies, outliers and correlations stand out? Rank them by importance and say how confident the evidence is.",
      { task: "statistics" }
    );
  });
}

function reportSummaryAction() {
  return runAction(() => {
    if (!sessionId()) return ask("What can you do?");
    return ask(
      "Write a concise executive summary suitable for a technical report: purpose, data coverage, key findings, limitations, and next steps.",
      { task: "research" }
    );
  });
}

function explainStatisticsAction() {
  return runAction(() => {
    if (!sessionId()) return ask("What can you do?");
    return ask(
      "Explain the most important statistics in this dataset. What do the averages, extremes, spread and missingness tell us about air quality and data quality?",
      { task: "statistics" }
    );
  });
}

/* ── Hover-revealed right assistant column ──────────────────────────────── */

let assistantHideTimer = null;

function syncAssistantLayout() {
  const drawer = $("assistant-drawer");
  const scrim = $("assistant-scrim");
  if (!drawer) return;
  drawer.classList.toggle("open", state.open);
  drawer.setAttribute("aria-hidden", state.open ? "false" : "true");
  if (scrim) scrim.classList.toggle("open", Boolean(state.open && isCompactAssistant()));
  document.body.classList.toggle("assistant-open", state.open);
}

function setAssistantOpen(open, options = {}) {
  const next = Boolean(open);
  if (next && !state.open) state.lastFocus = document.activeElement;
  state.open = next;
  state.pinned = options.pin === undefined ? next : Boolean(options.pin);
  syncAssistantLayout();
  if (next) {
    cancelAssistantHide();
    refreshStatus();
    loadSuggestions();
    loadHistory();
    if (options.focus !== false) {
      window.setTimeout(() => {
        const input = $("assistant-input");
        if (input) input.focus();
      }, 180);
    }
  } else if (!next && state.lastFocus && typeof state.lastFocus.focus === "function") {
    state.lastFocus.focus({ preventScroll: true });
  }
}

function cancelAssistantHide() {
  if (assistantHideTimer !== null) {
    window.clearTimeout(assistantHideTimer);
    assistantHideTimer = null;
  }
}

function scheduleAssistantHide() {
  if (isCompactAssistant() || state.pinned || !state.open || state.busy) return;
  const drawer = $("assistant-drawer");
  if (drawer && drawer.contains(document.activeElement)) return;
  cancelAssistantHide();
  assistantHideTimer = window.setTimeout(() => {
    assistantHideTimer = null;
    if (!state.pinned && state.open && !state.busy) setAssistantOpen(false, { pin: false, focus: false });
  }, 260);
}

/** Unpinned reveal used by the hover rail; never steals focus. */
function revealAssistant() {
  if (isCompactAssistant()) return;
  cancelAssistantHide();
  if (!state.open) setAssistantOpen(true, { pin: false, focus: false });
}
window.revealAssistant = revealAssistant;

function openAssistant(prefill) {
  cancelAssistantHide();
  setAssistantOpen(true, { pin: true });
  ensurePuter()
    .then(() => refreshStatus())
    .catch(() => refreshStatus());
  if (prefill) window.setTimeout(() => ask(prefill), 260);
}
window.openAssistant = openAssistant;

function closeAssistant() {
  cancelAssistantHide();
  setAssistantOpen(false, { pin: false });
}
window.closeAssistant = closeAssistant;

function toggleAssistant() {
  if (state.open) closeAssistant();
  else openAssistant();
}
window.toggleAssistant = toggleAssistant;

/* ── Wiring ──────────────────────────────────────────────────────────────── */

function boot() {
  window.aqatPuterAuth = {
    signIn: async () => {
      const button = $("puter-auth-action");
      try {
        if (button) button.disabled = true;
        await signInPuter();
        await syncQuotaSession(true);
        if (button) button.textContent = "Signed in · switch account";
        refreshStatus();
        toast("Puter account connected for this session.", "success");
      } catch (error) {
        toast(friendlyAIError(error), "error");
      } finally { if (button) button.disabled = false; }
    },
    signOut: () => endAISession("user"),
  };
  window.addEventListener("aqat:ai-usage", event => {
    renderAIUsage(event.detail || {});
    if (event.detail?.type === "auth") refreshStatus();
  });
  initializePuterSession().then(() => refreshStatus());

  // The panel is always hidden on load. It is revealed by hovering the edge
  // rail or by an explicit Ask AI click, never left open across page loads.
  state.open = false;
  state.pinned = false;
  syncAssistantLayout();
  window.addEventListener("resize", () => { if (!isCompactAssistant()) syncAssistantLayout(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.open) closeAssistant();
  });

  const peek = $("assistant-peek");
  const drawer = $("assistant-drawer");
  if (peek) {
    peek.addEventListener("mouseenter", revealAssistant);
    peek.addEventListener("mouseleave", scheduleAssistantHide);
    // Keyboard focus (and click) pins the panel so it cannot vanish on blur.
    peek.addEventListener("focus", () => setAssistantOpen(true, { pin: true, focus: false }));
  }
  if (drawer) {
    drawer.addEventListener("mouseenter", cancelAssistantHide);
    drawer.addEventListener("mouseleave", scheduleAssistantHide);
    drawer.addEventListener("focusin", cancelAssistantHide);
    drawer.addEventListener("focusout", scheduleAssistantHide);
  }

  setModel(storedModel(), { persist: false });
  const modelSelect = $("assistant-model");
  if (modelSelect) modelSelect.value = selectedModel();
  const thresholdInput = $("ai-token-threshold");
  if (thresholdInput) {
    thresholdInput.value = localStorage.getItem("aqat-ai-token-threshold") || "10000";
    thresholdInput.addEventListener("change", () => localStorage.setItem("aqat-ai-token-threshold", thresholdInput.value));
  }
  const form = $("assistant-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      ask();
    });
  }
  const input = $("assistant-input");
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        ask();
      }
    });
  }
  const image = $("assistant-image");
  if (image) {
    image.addEventListener("change", () => {
      setImageFile((image.files && image.files[0]) || null);
    });
  }
  const data = $("assistant-data");
  if (data) {
    data.addEventListener("change", () => {
      setDataFile((data.files && data.files[0]) || null);
    });
  }
  const modeButton = $("assistant-mode");
  if (modeButton) {
    modeButton.setAttribute("aria-pressed", "false");
    setMode("simple");
  }

  // Any data edit or new chart invalidates the compact context cache.
  window.addEventListener("aqat:session", resetForSession);
  window.addEventListener("aqat:plot", () => {
    invalidateContext(sessionId());
    state.context = null;
  });
  const previousDataUpdated = window.onDataUpdated;
  window.onDataUpdated = function (payload, message) {
    invalidateContext(sessionId());
    state.context = null;
    if (typeof previousDataUpdated === "function") {
      return previousDataUpdated.call(this, payload, message);
    }
    return undefined;
  };

  showEmptyState();
  refreshStatus();
  loadSuggestions();
  if (sessionId()) loadHistory();

  window.aqatAssistant = {
    open: openAssistant,
    close: closeAssistant,
    toggle: toggleAssistant,
    reveal: revealAssistant,
    ask,
    addAnswer: appendAnswerPayload,
    addError,
    mode: () => state.mode,
    model: () => selectedModel() || "auto",
    setModel,
    quota: () => state.quota,
    refreshQuota,
    signIn: () => window.aqatPuterAuth.signIn(),
    signOut: () => endAISession("user"),
    isOpen: () => state.open,
    setContext,
    status: () => state.status,
    refreshStatus,
    explainDataset: explainDatasetAction,
    explainStatistics: explainStatisticsAction,
    explainChart: explainChartAction,
    detectPatterns: detectPatternsAction,
    reportSummary: reportSummaryAction,
    clearConversation: () => {
      const sid = sessionId();
      if (!sid) return;
      clearConversation(sid);
      state.historyLoaded = null;
      const container = messageContainer();
      if (container) container.replaceChildren();
      showEmptyState();
    },
    conversation: () => loadConversation(sessionId()),
  };
}

let aiWarningShown = false;
function renderAIUsage(detail) {
  const summary = $("ai-usage-summary");
  const trend = $("ai-usage-trend");
  const button = $("puter-auth-action");
  if (detail.type === "request" && summary) {
    summary.textContent = `${Number(detail.tokens || 0).toLocaleString()} tokens this session${detail.reported ? " · model reported" : " · estimated"}`;
    const rate = Number(detail.estimatePerMinute || 0);
    if (trend) trend.textContent = `Estimated pace: ${rate.toFixed(0)} tokens/minute · extrapolation from this session`;
    const threshold = Math.max(1, Number($("ai-token-threshold")?.value || 10000));
    if (detail.tokens >= threshold && !aiWarningShown) {
      aiWarningShown = true;
      toast(`AI usage passed your ${threshold.toLocaleString()} token warning threshold.`, "warn", 7000);
    }
  } else if (detail.type === "allowance" && summary && detail.usage?.allowanceInfo) {
    const remaining = detail.usage.allowanceInfo.remaining;
    const allowance = $("ai-usage-allowance");
    if (allowance && remaining != null) allowance.textContent = `Puter monthly allowance remaining: ${Number(remaining).toLocaleString()} provider units`;
  } else if (detail.type === "auth" && button) {
    button.textContent = detail.authenticated ? "Signed in · switch account" : "Sign in to Puter";
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
