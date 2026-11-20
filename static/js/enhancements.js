/* ==========================================================================
   AQACT interface enhancements
   Command palette, insights, undo, demo onboarding and
   publication export.  Loaded after app.js so it can wrap a few existing
   functions without changing the original analytics code.
   ========================================================================== */
(function () {
  "use strict";

  const ONBOARD_KEY = "aqat-onboarded";
  const SESSION_KEY = "aqat-session-v1";
  const $ = (id) => document.getElementById(id);
  const SECTIONS = {
    upload: "Upload",
    batch: "Batch Stack",
    multistation: "Multi-Station",
    clean: "Clean",
    explore: "Explore",
    plots: "Plots",
    map: "Map",
    extreme: "Extreme",
    compliance: "Compliance",
    machinelearning: "Machine Learning",
    modeleval: "Model Eval",
    about: "About",
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function requestJson(url, options) {
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
      err.requestId = data.request_id;
      throw err;
    }
    return data;
  }
  window.requestJson = requestJson;

  function toast(message, type, duration) {
    const container = $("toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = `toast toast-${type || "success"}`;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = type === "error" ? "!" : type === "warn" ? "!" : "✓";
    const body = document.createElement("span");
    body.className = "toast-body";
    body.textContent = message;
    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    close.textContent = "×";
    close.addEventListener("click", () => el.remove());
    el.append(icon, body, close);
    container.appendChild(el);
    window.setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      window.setTimeout(() => el.remove(), 320);
    }, duration || 4200);
  }
  // app.js calls the global toast identifier; route it through the richer version.
  window.toast = toast;

  /* ── Navigation ───────────────────────────────────────────────────────── */
  const originalSwitchTab = window.switchTab;
  window.switchTab = function (name, opts) {
    if (typeof originalSwitchTab === "function") originalSwitchTab(name);
    const label = SECTIONS[name] || String(name || "").replace(/_/g, " ");
    document.querySelectorAll("#tabs .tab-btn").forEach((btn) => {
      const active = btn.dataset.tab === name;
      btn.classList.toggle("active", active);
      if (active) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });
    const heading = $("current-section");
    if (heading) heading.textContent = label;
    document.title = `${label} · AQACT`;
    const main = $("main");
    if (main && (!opts || !opts.keepScroll)) main.scrollTo({ top: 0, behavior: "smooth" });
    toggleSidebar(false);
    try {
      localStorage.setItem("aqat-tab", name);
    } catch (_) {
      /* storage can be unavailable in private windows */
    }
    if (name === "explore") refreshInsights();
    if (name === "plots") refreshPublicationStatus();
  };

  function toggleSidebar(force) {
    const body = document.body;
    const open = force === undefined ? !body.classList.contains("sidebar-open") : Boolean(force);
    body.classList.toggle("sidebar-open", open);
    const button = $("sidebar-toggle");
    if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const first = document.querySelector("#tabs .tab-btn:not(:disabled)");
      if (first) first.focus({ preventScroll: true });
    }
  }
  window.toggleSidebar = toggleSidebar;

  window.addEventListener("resize", () => {
    if (window.innerWidth > 760) toggleSidebar(false);
  });

  /* ── Session helpers ──────────────────────────────────────────────────── */
  function sessionId() {
    return (window.STATE && window.STATE.sessionId) || null;
  }

  function persistSession(data) {
    if (!data || !data.session_id) return;
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify({
        session_id: data.session_id,
        columns: data.columns || [],
        shape: data.shape || [0, 0],
        dtypes: data.dtypes || {},
        preview: (data.preview || []).slice(0, 20),
        cleaned: data.cleaned !== false,
        provider: data.provider || null,
        saved_at: Date.now(),
      }));
    } catch (_) {
      /* storage can be unavailable in private windows */
    }
  }

  function clearPersistedSession() {
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch (_) {
      /* ignore */
    }
  }

  function currentSessionPayload() {
    if (!window.STATE || !window.STATE.sessionId) return null;
    return {
      session_id: window.STATE.sessionId,
      columns: window.STATE.columns,
      shape: window.STATE.shape,
      dtypes: window.STATE.dtypes,
      preview: window.STATE._previewRows,
      cleaned: window.STATE.isCleaned,
    };
  }

  function clearFigureState() {
    if (!window.STATE) return;
    window.STATE.lastFigure = null;
    window.STATE._lastPlot = null;
    window.STATE.lastPlotType = null;
    window.STATE.lastPlotTitle = "";
    window.STATE.figureStale = false;
  }

  function applySessionPayload(data, message) {
    if (!data) return;
    window.STATE.sessionId = data.session_id;
    window.STATE.columns = data.columns || [];
    window.STATE.shape = data.shape || [0, 0];
    window.STATE.dtypes = data.dtypes || {};
    window.STATE._previewRows = data.preview || [];
    window.STATE.isCleaned = data.cleaned === undefined ? true : Boolean(data.cleaned);

    const badge = $("session-badge");
    if (badge) badge.style.display = "flex";
    const display = $("session-id-display");
    if (display) display.textContent = data.session_id || "";
    const cleaned = $("cleaned-dot");
    if (cleaned) cleaned.style.display = window.STATE.isCleaned ? "inline" : "none";

    if (typeof window.enableTabs === "function") window.enableTabs();
    if (typeof window.refreshColumnDropdowns === "function") window.refreshColumnDropdowns();

    const rows = Number((data.shape || [0, 0])[0]) || 0;
    const cols = Number((data.shape || [0, 0])[1]) || 0;
    const rowBadge = $("preview-rows-badge");
    if (rowBadge) rowBadge.textContent = `${rows.toLocaleString()} rows`;
    const colBadge = $("preview-cols-badge");
    if (colBadge) colBadge.textContent = `${cols} columns`;
    if (data.columns && data.preview && typeof window.buildTable === "function") {
      window.buildTable("preview-table", data.columns, data.preview);
      const preview = $("upload-preview");
      if (preview) preview.style.display = "block";
    }

    clearFigureState();
    persistSession({
      session_id: data.session_id,
      columns: window.STATE.columns,
      shape: window.STATE.shape,
      dtypes: window.STATE.dtypes,
      preview: window.STATE._previewRows,
      cleaned: window.STATE.isCleaned,
      provider: data.provider,
    });
    window.dispatchEvent(new CustomEvent("aqat:session", { detail: { sessionId: data.session_id, data } }));
    if (message) toast(message, "success");
    refreshInsights();
    updateAssistantContext();
  }

  // Wrap the existing post-edit hook so insights and export state stay honest.
  const originalOnDataUpdated = window.onDataUpdated;
  window.onDataUpdated = function (data, message) {
    if (typeof originalOnDataUpdated === "function") originalOnDataUpdated(data, message);
    if (window.STATE) window.STATE.figureStale = true;
    persistSession(currentSessionPayload());
    refreshInsights();
    updateAssistantContext();
    const status = $("pub-status");
    if (status) {
      status.textContent = "Data changed — regenerate the chart before exporting.";
      status.className = "pub-status";
    }
  };

  async function restoreSession() {
    if (sessionId()) return;
    let stored = null;
    try {
      stored = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    } catch (_) {
      stored = null;
    }
    if (!stored || !stored.session_id) return;
    try {
      const info = await requestJson(`/api/session?session_id=${encodeURIComponent(stored.session_id)}`);
      if (sessionId()) return; // the user started another session while we waited
      applySessionPayload({
        session_id: info.session_id,
        columns: (info.columns && info.columns.length) ? info.columns : stored.columns,
        shape: info.shape || stored.shape,
        dtypes: info.dtypes || stored.dtypes || {},
        preview: (info.preview && info.preview.length) ? info.preview : stored.preview,
        cleaned: info.cleaned,
        provider: info.provider,
      });
      let tab = null;
      try {
        tab = localStorage.getItem("aqat-tab");
      } catch (_) {
        tab = null;
      }
      if (tab && tab !== "upload" && document.getElementById(`panel-${tab}`)) {
        window.switchTab(tab, { keepScroll: true });
      }
      toast(`Restored session ${String(info.session_id).slice(0, 8)}.`, "success", 3200);
    } catch (_) {
      // The server was restarted or the session expired; start clean.
      clearPersistedSession();
    }
  }
  window.restoreSession = restoreSession;

  async function updateAssistantContext() {
    const el = $("assistant-context");
    if (!el) return;
    const sid = sessionId();
    if (!sid) {
      el.textContent = "Load a dataset to get contextual answers.";
      return;
    }
    const shape = (window.STATE && window.STATE.shape) || [0, 0];
    el.textContent = `Session ${sid.slice(0, 8)} · ${Number(shape[0] || 0).toLocaleString()} rows × ${Number(shape[1] || 0)} columns`;
    try {
      const info = await requestJson(`/api/session?session_id=${encodeURIComponent(sid)}`);
      if (sessionId() !== sid) return;
      const bits = [
        `Session ${sid.slice(0, 8)}`,
        `${Number(info.shape?.[0] || 0).toLocaleString()} rows × ${Number(info.shape?.[1] || 0)} columns`,
        info.cleaned ? "cleaned" : "raw",
      ];
      if (info.provider) bits.push(info.provider);
      el.textContent = bits.join(" · ");
    } catch (_) {
      /* the shape summary above is still useful */
    }
  }
  window.updateAssistantContext = updateAssistantContext;

  /* ── Demo dataset ─────────────────────────────────────────────────────── */
  async function loadDemoDataset() {
    const button = $("demo-btn");
    if (button && button.disabled) return;
    if (button && typeof window.setLoading === "function") window.setLoading("demo-btn", true);
    try {
      const data = await requestJson("/api/demo", { method: "POST" });
      applySessionPayload(data);
      const rows = Number(data.shape?.[0] || 0);
      toast(`Demo dataset loaded — ${rows.toLocaleString()} readings across two stations.`, "success", 5200);
      window.switchTab("explore");
    } catch (err) {
      toast(`Demo dataset could not load: ${err.message}`, "error", 6000);
    } finally {
      if (button && typeof window.setLoading === "function") window.setLoading("demo-btn", false);
    }
  }
  window.loadDemoDataset = loadDemoDataset;

  /* ── Undo ─────────────────────────────────────────────────────────────── */
  async function undoLastEdit() {
    const sid = sessionId();
    if (!sid) {
      toast("Load a dataset before using undo.", "warn");
      return;
    }
    const button = $("undo-btn");
    if (button) button.disabled = true;
    try {
      const data = await requestJson("/api/undo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      });
      const label = (data.undo_labels && data.undo_labels[0]) || "the last edit";
      if (typeof window.onDataUpdated === "function") {
        // Pass no message: onDataUpdated would add its own toast and this
        // function already reports the undo in plain language.
        window.onDataUpdated(data, "");
      }
      toast(`Restored the data from before “${label}”.`, "success");
      window.switchTab("explore");
    } catch (err) {
      toast(err.message, err.code === "nothing_to_undo" ? "warn" : "error");
    } finally {
      if (button) button.disabled = false;
    }
  }
  window.undoLastEdit = undoLastEdit;

  /* ── Insights ─────────────────────────────────────────────────────────── */
  let insightToken = 0;

  function insightCard(item) {
    const card = document.createElement("article");
    card.className = `insight-card flip-card severity-${item.severity || "info"}`;
    const inner = document.createElement("div");
    inner.className = "flip-card-inner";

    function makeFace(faceName, label, textValue) {
      const face = document.createElement("section");
      face.className = `flip-face insight-face ${faceName}`;
      face.setAttribute("aria-label", label);

      const top = document.createElement("div");
      top.className = "insight-top";
      const kind = document.createElement("span");
      kind.className = "insight-kind";
      kind.textContent = String(item.kind || "insight").replace(/_/g, " ");
      const severity = document.createElement("span");
      severity.className = "insight-severity";
      severity.textContent = item.severity || "info";
      top.append(kind, severity);

      const title = document.createElement("h3");
      title.className = "insight-title";
      title.textContent = item.title || "Insight";
      const text = document.createElement("p");
      text.className = "insight-text";
      text.textContent = textValue || item.simple || item.technical || "";

      const actions = document.createElement("div");
      actions.className = "insight-actions";
      const flip = document.createElement("button");
      flip.type = "button";
      flip.className = "flip-action insight-flip-action";
      flip.dataset.flipLabel = item.title || "insight";
      flip.textContent = faceName === "front" ? "Technical" : "Simple";
      flip.setAttribute("aria-pressed", "false");
      flip.addEventListener("click", () => {
        card.classList.toggle("is-flipped");
        window.initFlipCards && window.initFlipCards();
      });
      const ask = document.createElement("button");
      ask.type = "button";
      ask.textContent = "Ask AI";
      ask.addEventListener("click", () => {
        if (window.aqatAssistant) {
          window.aqatAssistant.open();
          window.aqatAssistant.ask(`Explain this insight: ${item.title}`);
        }
      });
      actions.append(flip, ask);
      face.append(top, title, text, actions);
      return face;
    }

    inner.append(
      makeFace("front", "Simple explanation", item.simple || item.technical),
      makeFace("back", "Technical explanation", item.technical || item.simple)
    );
    card.append(inner);
    window.initFlipCards && window.initFlipCards();
    return card;
  }

  async function refreshInsights() {
    const grid = $("insight-grid");
    if (!grid) return;
    const sid = sessionId();
    const token = ++insightToken;
    if (!sid) {
      grid.innerHTML = '<div class="insight-empty">Open the demo dataset or upload a file to see prioritised, evidence-backed insights.</div>';
      return;
    }
    grid.innerHTML = '<div class="insight-skeleton"></div><div class="insight-skeleton"></div><div class="insight-skeleton"></div>';
    const button = $("insights-refresh-btn");
    if (button) button.disabled = true;
    try {
      const data = await requestJson(`/api/insights?session_id=${encodeURIComponent(sid)}`);
      if (token !== insightToken) return;
      const items = data.insights || [];
      if (!items.length) {
        grid.innerHTML = '<div class="insight-empty">No notable patterns were detected yet. Try cleaning the data or adding more readings.</div>';
        return;
      }
      grid.replaceChildren(...items.map(insightCard));
      window.initFlipCards && window.initFlipCards();
    } catch (err) {
      if (token !== insightToken) return;
      grid.innerHTML = `<div class="insight-empty">Insights are unavailable right now: ${escapeHtml(err.message)}</div>`;
    } finally {
      if (button) button.disabled = false;
    }
  }
  window.refreshInsights = refreshInsights;

  /* ── Publication export ───────────────────────────────────────────────── */
  function publicationStatus(message, state) {
    const el = $("pub-status");
    if (!el) return;
    el.textContent = message || "";
    el.className = `pub-status${state ? ` ${state}` : ""}`;
  }

  function refreshPublicationStatus() {
    if (!window.STATE || !window.STATE.lastFigure) return;
    if (window.STATE.figureStale) {
      publicationStatus("Data changed — regenerate the chart before exporting.");
    } else if (!publicationStatusHasText()) {
      publicationStatus("Chart ready. Add a caption, then export.");
    }
  }

  function publicationStatusHasText() {
    const el = $("pub-status");
    return Boolean(el && el.textContent.trim());
  }

  async function ensureExportElement() {
    if (typeof Plotly === "undefined") throw new Error("Plotly is not available. Reload the page and try again.");
    const live = $("live-plot");
    if (live && live.data && !window.STATE.figureStale) return { el: live, temp: false };
    if (!window.STATE.lastFigure) return null;
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:-20000px;top:0;width:1200px;height:650px;";
    document.body.appendChild(host);
    await new Promise((resolve, reject) => {
      window.plotlyReady(() => {
        Plotly.newPlot(
          host,
          window.STATE.lastFigure.data,
          Object.assign({}, window.STATE.lastFigure.layout, { width: 1200, height: 650, autosize: false }),
          { displayModeBar: false, staticPlot: true }
        ).then(resolve).catch(reject);
      });
    });
    return { el: host, temp: true };
  }

  function svgFromDataUrl(url) {
    if (!url) return "";
    const comma = url.indexOf(",");
    if (comma < 0) return url;
    const meta = url.slice(0, comma);
    const payload = url.slice(comma + 1);
    if (/;base64/i.test(meta)) {
      const binary = atob(payload);
      const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    }
    return decodeURIComponent(payload);
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function safeFilename(value, fallback) {
    return String(value || fallback || "aqact_figure")
      .replace(/[^\w\-. ]+/g, "")
      .trim()
      .replace(/\s+/g, "_")
      .slice(0, 70) || fallback || "aqact_figure";
  }

  async function exportPublication(format) {
    const fmt = String(format || "pdf").toLowerCase();
    if (!sessionId()) {
      toast("Load a dataset and generate a plot first.", "warn");
      return;
    }
    if (!window.STATE || !window.STATE.lastFigure) {
      toast("Generate a plot before exporting a publication figure.", "warn");
      return;
    }
    if (window.STATE.figureStale) {
      toast("The data changed since this chart was drawn. Regenerate the plot first.", "warn");
      return;
    }
    const dpi = Number(($("pub-dpi") && $("pub-dpi").value) || 300);
    const size = ($("pub-size") && $("pub-size").value) || "wide";
    const title = (($("plot-title") && $("plot-title").value.trim()) || window.STATE.lastPlotTitle || "Air quality analysis");
    const caption = ($("pub-caption") && $("pub-caption").value.trim()) || "";
    const source = ($("pub-source") && $("pub-source").value.trim()) || "";
    const figureNumber = ($("pub-figure-number") && $("pub-figure-number").value.trim()) || "";
    const button = $(`pub-${fmt}-btn`);
    if (button) button.disabled = true;
    let temp = null;
    try {
      publicationStatus(`Rendering ${fmt.toUpperCase()}…`);
      const target = await ensureExportElement();
      if (!target) throw new Error("No rendered chart is available to export.");
      temp = target.temp ? target.el : null;
      const scale = Math.max(1, Math.min(4, dpi / 96));
      const imageOptions = { format: fmt === "svg" ? "svg" : "png", width: 1200, height: 650, scale: fmt === "svg" ? 1 : scale };
      const imageUrl = await Plotly.toImage(target.el, imageOptions);
      const payload = {
        format: fmt,
        title,
        caption,
        source,
        figure_number: figureNumber,
        size,
        dpi,
        filename: safeFilename(title, "aqact_figure"),
        figure: window.STATE.lastFigure,
      };
      if (fmt === "svg") payload.svg = svgFromDataUrl(imageUrl);
      else payload.image = imageUrl;

      const res = await fetch("/api/export/figure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = {};
        try {
          detail = await res.json();
        } catch (_) {
          /* non-JSON error */
        }
        throw new Error(detail.error || `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const ext = fmt === "pdf" ? "pdf" : fmt === "svg" ? "svg" : "png";
      const stamp = new Date().toISOString().slice(0, 10);
      downloadBlob(blob, `${safeFilename(title, "aqact_figure")}_${stamp}.${ext}`);
      publicationStatus(`Saved ${fmt.toUpperCase()} · ${Math.max(1, Math.round(blob.size / 1024))} KB`, "ok");
      toast(`Publication ${fmt.toUpperCase()} downloaded.`, "success");
    } catch (err) {
      publicationStatus(err.message, "error");
      toast(err.message, "error", 6000);
    } finally {
      if (temp && temp.parentNode) temp.remove();
      if (button) button.disabled = false;
    }
  }
  window.exportPublication = exportPublication;

  async function suggestCaption() {
    const sid = sessionId();
    if (!sid) {
      toast("Load a dataset and generate a plot first.", "warn");
      return;
    }
    if (!window.STATE || !window.STATE.lastFigure) {
      toast("Generate a plot before asking for a caption.", "warn");
      return;
    }
    const button = $("pub-caption-btn");
    if (button) button.disabled = true;
    try {
      publicationStatus("Drafting a caption from the chart metadata…");
      const data = await requestJson("/api/assistant/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, kind: "plot", mode: "simple" }),
      });
      const caption = [data.caption || data.summary || data.simple, data.caveat ? `Note: ${data.caveat}` : ""]
        .filter(Boolean)
        .join(" ");
      const field = $("pub-caption");
      if (field) field.value = caption.slice(0, 1200);
      publicationStatus("Caption drafted. Edit it before exporting.", "ok");
    } catch (err) {
      publicationStatus(err.message, "error");
      toast(err.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }
  window.suggestCaption = suggestCaption;

  async function explainLastPlot() {
    const sid = sessionId();
    if (!sid) {
      toast("Load a dataset and generate a plot first.", "warn");
      return;
    }
    if (!window.aqatAssistant) return;
    // One-click path: capture the rendered chart, route it to Gemini through
    // Puter.js, and fail over safely.  Publication captions stay on /explain,
    // which is deterministic and reproducible.
    if (typeof window.aqatAssistant.explainChart === "function") {
      return window.aqatAssistant.explainChart();
    }
    window.aqatAssistant.open();
    window.aqatAssistant.ask(
      "Explain the current chart, the pattern it shows, and what a reader should look for.",
      { task: "explain_plot" }
    );
  }
  window.explainLastPlot = explainLastPlot;

  /* ── Command palette ──────────────────────────────────────────────────── */
  const palette = $("command-palette");
  const paletteInput = $("palette-input");
  const paletteResults = $("palette-results");
  let paletteItems = [];
  let paletteIndex = 0;

  function paletteCommands() {
    const commands = Object.entries(SECTIONS).map(([key, label]) => ({
      label,
      hint: "Go to section",
      icon: "→",
      keywords: `navigate ${key}`,
      run: () => window.switchTab(key),
    }));
    commands.push(
      { label: "Load demo dataset", hint: "Open the bundled two-station teaching dataset", icon: "▶", keywords: "demo sample example", run: loadDemoDataset },
      { label: "Ask the assistant", hint: "Explain results, trends and charts", icon: "✦", keywords: "ai help question", run: () => window.aqatAssistant && window.aqatAssistant.open() },
      { label: "Explain the current chart", hint: "Open the assistant with a chart interpretation", icon: "◔", keywords: "plot figure chart", run: explainLastPlot },
      { label: "Refresh insights", hint: "Recompute prioritised dataset findings", icon: "◈", keywords: "insight analysis", run: refreshInsights },
      { label: "Undo last data edit", hint: "Restore the previous data version", icon: "↶", keywords: "revert history", run: undoLastEdit },
      { label: "Download publication PDF", hint: "Compose the current chart as a print-ready PDF", icon: "↓", keywords: "export figure print", run: () => exportPublication("pdf") },
      { label: "Download publication PNG", hint: "High-resolution image of the current chart", icon: "↓", keywords: "export figure image", run: () => exportPublication("png") },
      { label: "Download publication SVG", hint: "Vector figure with title and caption", icon: "↓", keywords: "export figure vector", run: () => exportPublication("svg") }
    );
    if (sessionId()) {
      commands.push(
        { label: "Summarise this dataset", hint: "Ask the assistant for an overview", icon: "✦", keywords: "summary overview", run: () => askAssistant("Summarise this dataset") },
        { label: "Find trends and anomalies", hint: "Ask the assistant what changed", icon: "✦", keywords: "trend anomaly pattern", run: () => askAssistant("What trends and anomalies stand out?") },
        { label: "Check guideline exceedances", hint: "Ask about WHO and EPA screening", icon: "✦", keywords: "compliance who epa", run: () => askAssistant("Are there any guideline exceedances?") },
        { label: "Compare stations", hint: "Ask which station is cleaner", icon: "✦", keywords: "station compare", run: () => askAssistant("Compare the stations in this dataset.") }
      );
    }
    return commands;
  }

  function askAssistant(question) {
    if (!window.aqatAssistant) return;
    window.aqatAssistant.open();
    window.aqatAssistant.ask(question);
  }

  function renderPalette(query) {
    const term = String(query || "").trim().toLowerCase();
    paletteItems = paletteCommands().filter((item) => {
      if (!term) return true;
      return `${item.label} ${item.hint} ${item.keywords}`.toLowerCase().includes(term);
    });
    paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
    if (!paletteItems.length) {
      paletteResults.innerHTML = '<div class="palette-empty">No matching actions. Try “demo”, “plot”, “trend” or “compliance”.</div>';
      return;
    }
    paletteResults.replaceChildren(
      ...paletteItems.map((item, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `palette-item${index === paletteIndex ? " active" : ""}`;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", index === paletteIndex ? "true" : "false");
        const icon = document.createElement("span");
        icon.className = "pi-icon";
        icon.textContent = item.icon;
        const text = document.createElement("span");
        text.className = "pi-text";
        const label = document.createElement("span");
        label.textContent = item.label;
        const hint = document.createElement("span");
        hint.className = "pi-hint";
        hint.textContent = item.hint;
        text.append(label, hint);
        button.append(icon, text);
        // Use mousemove, not mouseenter: re-rendering the list under a
        // stationary pointer can fire mouseenter and hijack the keyboard
        // selection. mousemove only fires on genuine pointer movement.
        button.addEventListener("mousemove", () => {
          if (paletteIndex === index) return;
          paletteIndex = index;
          document.querySelectorAll(".palette-item").forEach((el, i) => {
            el.classList.toggle("active", i === index);
            el.setAttribute("aria-selected", i === index ? "true" : "false");
          });
        });
        button.addEventListener("click", () => runPaletteItem(index));
        return button;
      })
    );
  }

  function runPaletteItem(index) {
    const item = paletteItems[index];
    if (!item) return;
    closeCommandPalette();
    window.setTimeout(() => item.run(), 30);
  }

  function openCommandPalette() {
    if (!palette) return;
    palette.hidden = false;
    paletteInput.value = "";
    paletteIndex = 0;
    renderPalette("");
    window.setTimeout(() => paletteInput.focus(), 20);
  }
  window.openCommandPalette = openCommandPalette;

  function closeCommandPalette() {
    if (!palette) return;
    palette.hidden = true;
  }

  if (paletteInput) {
    paletteInput.addEventListener("input", () => {
      paletteIndex = 0;
      renderPalette(paletteInput.value);
    });
    paletteInput.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        paletteIndex = Math.min(paletteIndex + 1, paletteItems.length - 1);
        renderPalette(paletteInput.value);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        paletteIndex = Math.max(paletteIndex - 1, 0);
        renderPalette(paletteInput.value);
      } else if (event.key === "Enter") {
        event.preventDefault();
        runPaletteItem(paletteIndex);
      }
    });
  }
  if (palette) {
    palette.addEventListener("click", (event) => {
      if (event.target === palette) closeCommandPalette();
    });
  }

  /* ── Keyboard shortcuts and global listeners ──────────────────────────── */
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (palette && !palette.hidden) closeCommandPalette();
      else openCommandPalette();
      return;
    }
    if (event.key === "Escape") {
      if (palette && !palette.hidden) closeCommandPalette();
      else if (window.aqatAssistant && window.aqatAssistant.isOpen()) window.aqatAssistant.close();
      return;
    }
    if (!typing && event.key === "?" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      openCommandPalette();
    }
  });

  window.addEventListener("aqat:plot", (event) => {
    if (window.STATE) window.STATE.figureStale = false;
    const detail = (event && event.detail) || {};
    if (detail.title && window.STATE) window.STATE.lastPlotTitle = detail.title;
    publicationStatus("Chart ready. Add a caption, then export.");
    const caption = $("pub-caption");
    if (caption && !caption.value.trim()) {
      const rows = (window.STATE && window.STATE.shape && window.STATE.shape[0]) || 0;
      caption.value = `${detail.title || "Air quality chart"} generated from ${Number(rows).toLocaleString()} readings in AQACT.`;
    }
  });

  // Detect session changes made by the original upload/clean code.
  const sessionDisplay = $("session-id-display");
  if (sessionDisplay) {
    let last = sessionDisplay.textContent;
    new MutationObserver(() => {
      if (sessionDisplay.textContent === last) return;
      last = sessionDisplay.textContent;
      if (last) {
        clearFigureState();
        persistSession(currentSessionPayload());
        refreshInsights();
        updateAssistantContext();
        window.dispatchEvent(new CustomEvent("aqat:session", { detail: { sessionId: last } }));
      } else {
        clearPersistedSession();
      }
    }).observe(sessionDisplay, { childList: true, characterData: true, subtree: true });
  }

  /* ── First-run onboarding ─────────────────────────────────────────────── */
  function firstRunHint() {
    if (sessionId()) return;
    try {
      if (localStorage.getItem(SESSION_KEY)) return; // a restore may be in flight
    } catch (_) {
      /* ignore */
    }
    let seen = false;
    try {
      seen = sessionStorage.getItem(ONBOARD_KEY) === "1";
    } catch (_) {
      /* ignore */
    }
    if (seen) return;
    try {
      sessionStorage.setItem(ONBOARD_KEY, "1");
    } catch (_) {
      /* ignore */
    }
    window.setTimeout(() => {
      if (!sessionId()) {
        toast("New here? Choose “Explore demo dataset” to see every feature with real-shaped data.", "warn", 7000);
      }
    }, 1600);
  }

  /* ── Boot ─────────────────────────────────────────────────────────────── */
  function boot() {
    updateAssistantContext();
    refreshInsights();
    const heading = $("current-section");
    if (heading) heading.textContent = SECTIONS.upload;
    firstRunHint();
    restoreSession();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
