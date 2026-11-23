"""One-off frontend migration: extract inline assets and rebuild the shell."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "templates" / "index.html"
CSS_OUT = ROOT / "static" / "css" / "app.css"
JS_OUT = ROOT / "static" / "js" / "app.js"

html = INDEX.read_text(encoding="utf-8")

# ── Extract the original stylesheet ─────────────────────────────────────────
style_match = re.search(r"<style>(.*?)</style>", html, re.S)
if not style_match:
    raise SystemExit("Could not find the inline stylesheet")
css = style_match.group(1).strip() + "\n"
CSS_OUT.parent.mkdir(parents=True, exist_ok=True)
CSS_OUT.write_text(css, encoding="utf-8")

# ── Extract the original application script ─────────────────────────────────
marker = "/* ─────────────────────────────────────────────────────────\n   Plotly guard"
js_start = html.index(marker)
js_start = html.rindex("<script>", 0, js_start) + len("<script>")
js_end = html.index("</script>", js_start)
js = html[js_start:js_end].strip() + "\n"

# Store the current figure for publication export and notify the UI layer.
js = js.replace(
    "    STATE._lastPlot = { fig, type:t, title };",
    "    STATE._lastPlot = { fig, type:t, title };\n"
    "    STATE.lastFigure = fig;\n"
    "    STATE.lastPlotType = t;\n"
    "    STATE.lastPlotTitle = title || (fig.layout && fig.layout.title && fig.layout.title.text) || '';\n"
    "    window.dispatchEvent(new CustomEvent('aqat:plot', { detail: { type:t, title:STATE.lastPlotTitle } }));",
)

# Make the shared API helper resilient to non-JSON error pages and add hints.
js = js.replace(
    """async function api(endpoint, body={}, method='POST') {
  const res = await fetch(endpoint, {
    method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}""",
    """async function api(endpoint, body={}, method='POST') {
  const res = await fetch(endpoint, {
    method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch (_) { data = { error: text.slice(0, 240) || `HTTP ${res.status}` }; }
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.code = data.code;
    err.hint = data.hint;
    err.requestId = data.request_id;
    throw err;
  }
  return data;
}""",
)

JS_OUT.write_text(js, encoding="utf-8")

# ── Extract the existing panels ─────────────────────────────────────────────
main_start = html.index('<div id="main">')
main_end = html.index("</div><!-- /#main -->") + len("</div>")
panels = html[main_start:main_end]

# ── Inject new UI sections ──────────────────────────────────────────────────
quick_start = """
  <section class="hero">
    <div class="hero-copy">
      <span class="eyebrow"><span class="pulse-dot"></span> Offline scientific workspace</span>
      <h1>Turn sensor files into defensible air-quality insight.</h1>
      <p>Upload a logger export, clean it with a provider-aware pipeline, explore trends and anomalies, and export publication-ready figures — all on this machine.</p>
      <div class="hero-actions">
        <button id="demo-btn" class="btn btn-primary btn-lg" onclick="loadDemoDataset()">Explore demo dataset</button>
        <button class="btn btn-ghost btn-lg" onclick="document.getElementById('file-input').click()">Choose files</button>
        <button class="btn btn-ghost btn-lg" onclick="openAssistant()">Ask the assistant</button>
      </div>
      <div class="hero-points">
        <span>100% local compute</span><span>Provider-aware cleaning</span><span>Publication exports</span>
      </div>
    </div>
    <div class="hero-visual" aria-hidden="true">
      <div class="hero-glow"></div>
      <div class="mini-card">
        <div class="mini-label">PM2.5 · 24 h</div>
        <div class="mini-value">18.4 <small>µg/m³</small></div>
        <div class="mini-spark"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
        <div class="mini-foot"><span class="mini-dot"></span> Within WHO guideline</div>
      </div>
      <div class="mini-card mini-card-2">
        <div class="mini-label">Assistant insight</div>
        <div class="mini-text">Morning peaks dominate; one sensor spike needs review.</div>
      </div>
    </div>
  </section>
"""
panels = panels.replace(
    '<div id="panel-upload" class="tab-panel active">',
    '<div id="panel-upload" class="tab-panel active">\n' + quick_start,
    1,
)

insights_block = """
  <div class="section-divider">Assistant insights</div>
  <div class="insight-toolbar">
    <div>
      <div class="insight-heading">What stands out in this dataset</div>
      <div class="insight-sub">Prioritised findings with simple and technical explanations.</div>
    </div>
    <button class="btn btn-ghost btn-sm" onclick="refreshInsights()" id="insights-refresh-btn">Refresh insights</button>
  </div>
  <div id="insight-grid" class="insight-grid" aria-live="polite">
    <div class="insight-skeleton"></div><div class="insight-skeleton"></div><div class="insight-skeleton"></div>
  </div>
"""
panels = panels.replace(
    '<h2 style="font-size:20px;font-weight:700;margin-bottom:20px">Data Exploration</h2>',
    '<h2 style="font-size:20px;font-weight:700;margin-bottom:20px">Data Exploration</h2>' + insights_block,
    1,
)

publication_block = """
  <div class="card publication-card" id="publication-card">
    <div class="publication-head">
      <div>
        <div class="card-title" style="margin-bottom:4px">Publication figure</div>
        <p class="hint">Compose a white-page figure with title, caption and provenance metadata. PNG and PDF are rendered locally; SVG keeps the chart vector.</p>
      </div>
      <span class="badge badge-soft">Print-ready</span>
    </div>
    <div class="pub-grid">
      <div class="form-group">
        <label class="form-label" for="pub-figure-number">Figure number</label>
        <input class="form-input" id="pub-figure-number" placeholder="e.g. 2" />
      </div>
      <div class="form-group">
        <label class="form-label" for="pub-size">Page size</label>
        <select class="form-select" id="pub-size">
          <option value="wide" selected>Wide (12 × 7 in)</option>
          <option value="a4">A4 portrait</option>
          <option value="a4-landscape">A4 landscape</option>
          <option value="a3">A3 portrait</option>
          <option value="letter">Letter portrait</option>
          <option value="square">Square (8 × 8 in)</option>
          <option value="slide">Slide (16:9)</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label" for="pub-dpi">Resolution</label>
        <select class="form-select" id="pub-dpi">
          <option value="200">200 dpi · screen</option>
          <option value="300" selected>300 dpi · print</option>
          <option value="450">450 dpi · high detail</option>
          <option value="600">600 dpi · maximum</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label" for="pub-source">Source / credit</label>
        <input class="form-input" id="pub-source" placeholder="e.g. Riverside sensor network" />
      </div>
    </div>
    <div class="form-group" style="margin-bottom:12px">
      <label class="form-label" for="pub-caption">Caption</label>
      <textarea class="form-input" id="pub-caption" rows="2" placeholder="Explain what the figure shows and the main takeaway."></textarea>
    </div>
    <div class="publication-actions">
      <button class="btn btn-primary" onclick="exportPublication('pdf')" id="pub-pdf-btn">Download PDF</button>
      <button class="btn btn-ghost" onclick="exportPublication('png')" id="pub-png-btn">Download PNG</button>
      <button class="btn btn-ghost" onclick="exportPublication('svg')" id="pub-svg-btn">Download SVG</button>
      <button class="btn btn-teal" onclick="explainLastPlot()">Explain this chart</button>
      <span id="pub-status" class="pub-status" role="status"></span>
    </div>
  </div>
"""
panels = panels.replace("  <!-- Gallery -->", publication_block + "\n  <!-- Gallery -->", 1)

# Add an assistant explain button to the live chart toolbar.
panels = panels.replace(
    """      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn btn-ghost btn-sm" onclick="savePlot()">💾 Save to Gallery</button>
      </div>""",
    """      <div class="plot-actions">
        <button class="btn btn-ghost btn-sm" onclick="savePlot()">💾 Save to Gallery</button>
        <button class="btn btn-ghost btn-sm" onclick="explainLastPlot()">Ask AI to explain</button>
      </div>""",
    1,
)

# ── New shell markup ────────────────────────────────────────────────────────
head_assets = html[html.index("<!-- Plotly:"):html.index("<style>")].strip()

NAV_ITEMS = [
    ("upload", "Upload", "upload", True),
    ("batch", "Batch Stack", "batch", False),
    ("multistation", "Multi-Station", "multistation", False),
    ("clean", "Clean", "clean", False),
    ("explore", "Explore", "explore", False),
    ("plots", "Plots", "plots", False),
    ("map", "Map", "map", False),
    ("extreme", "Extreme", "extreme", False),
    ("compliance", "Compliance", "compliance", False),
    ("machinelearning", "Machine Learning", "ml", False),
    ("modeleval", "Model Eval", "eval", False),
    ("about", "About", "about", False),
]

ICONS = {
    "upload": '<path d="M12 16V4m0 0L8 8m4-4 4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "batch": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
    "multistation": '<path d="M4 21V8l6-3v16"/><path d="M10 21V11l10-4v14"/><path d="M2 21h20"/>',
    "clean": '<path d="m3 21 9-9"/><path d="M12 12 6 6"/><path d="m14 4 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z"/>',
    "explore": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "plots": '<path d="M4 20V4"/><path d="M4 20h16"/><path d="m7 15 4-5 3 3 5-7"/>',
    "map": '<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/>',
    "extreme": '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
    "compliance": '<path d="M12 3v18"/><path d="M5 7h14"/><path d="m7 7-3 7a3 3 0 0 0 6 0L7 7Z"/><path d="m17 7-3 7a3 3 0 0 0 6 0l-3-7Z"/>',
    "ml": '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "eval": '<path d="M3 12h4l2-7 4 14 2-7h6"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
}

nav_buttons = []
for name, label, icon, active in NAV_ITEMS:
    attrs = [f'class="tab-btn{" active" if active else ""}"', f'onclick="switchTab(\'{name}\')"']
    if name != "upload":
        attrs.append(f'id="tab-{name}"')
    if name in {"clean", "explore", "plots", "map", "extreme", "compliance",
                "machinelearning", "modeleval"}:
        attrs.append("disabled")
    nav_buttons.append(
        f'        <button {" ".join(attrs)} data-tab="{name}">\n'
        f'          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[icon]}</svg>\n'
        f'          <span>{label}</span>\n'
        f'        </button>'
    )
nav_html = "\n".join(nav_buttons)

shell = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="description" content="AQACT is an offline air-quality analysis and compliance assessment workspace for sensor data."/>
<meta name="theme-color" content="#070b14"/>
<title>AQACT — Air Quality Analysis &amp; Compliance Assessment Tool</title>

{head_assets}

<link rel="stylesheet" href="/static/css/app.css"/>
<link rel="stylesheet" href="/static/css/modern.css"/>
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>

<div id="plotly-error-banner" style="display:none">
  <strong>Charts could not load.</strong> Plotly is bundled locally; restart the app and reload this page.
</div>

<div id="app-shell">
  <aside id="sidebar" aria-label="Workspace navigation">
    <div class="brand">
      <div id="logo" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 15.5c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2"/>
          <path d="M4 10.5c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2"/>
          <path d="M4 20.5c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2"/>
        </svg>
      </div>
      <div>
        <div id="app-name">AQACT</div>
        <div id="app-sub">Air quality workspace</div>
      </div>
    </div>

    <div class="nav-label">Workspace</div>
    <nav id="tabs" aria-label="Sections">
{nav_html}
    </nav>

    <div class="sidebar-foot">
      <div class="offline-pill"><span class="pulse-dot"></span> Offline · local compute</div>
      <div class="sidebar-version">v{{{{ app_version }}}} · data never leaves this machine</div>
    </div>
  </aside>

  <div class="app-body">
    <header id="topbar">
      <button class="icon-btn" id="sidebar-toggle" aria-label="Open navigation" aria-expanded="false" onclick="toggleSidebar()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>
      <div class="topbar-heading">
        <span class="topbar-kicker">Workspace</span>
        <h1 id="current-section">Upload</h1>
      </div>
      <div id="header-right">
        <button class="topbar-action" id="palette-btn" onclick="openCommandPalette()" aria-label="Open command palette (Ctrl K)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <span class="action-label">Search</span><kbd>Ctrl K</kbd>
        </button>
        <button class="topbar-action topbar-action-accent" id="assistant-btn" onclick="openAssistant()">
          <span class="ai-orb small"></span><span class="action-label">Ask AI</span>
        </button>
        <button class="topbar-action" id="undo-btn" onclick="undoLastEdit()" title="Undo the last data edit">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M4 9h9a7 7 0 0 1 0 14h-2"/></svg>
          <span class="action-label">Undo</span>
        </button>
        <div id="session-badge">
          <span class="session-dot"></span>
          Session: <span id="session-id-display"></span>
          <span id="cleaned-dot" style="display:none">· Cleaned</span>
        </div>
        <button id="theme-btn" onclick="toggleTheme()" aria-label="Toggle colour theme">🌙 Dark</button>
      </div>
    </header>

    {panels}
  </div>
</div>

<div id="sidebar-scrim" class="scrim sidebar-scrim" onclick="toggleSidebar(false)"></div>

<button id="assistant-fab" class="assistant-fab" onclick="openAssistant()" aria-label="Open the offline assistant">
  <span class="ai-orb"></span>
  <span>Ask AI</span>
</button>

<div id="assistant-scrim" class="scrim" onclick="closeAssistant()"></div>
<aside id="assistant-drawer" class="assistant-drawer" aria-hidden="true" aria-labelledby="assistant-title">
  <div class="drawer-header">
    <div class="drawer-title">
      <span class="ai-orb"></span>
      <div>
        <h2 id="assistant-title">AQACT Assistant</h2>
        <p>Offline · explains results, charts and anomalies</p>
      </div>
    </div>
    <button class="icon-btn" onclick="closeAssistant()" aria-label="Close assistant">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
    </button>
  </div>
  <div class="assistant-context" id="assistant-context">Load a dataset to get contextual answers.</div>
  <div class="assistant-messages" id="assistant-messages" role="log" aria-live="polite"></div>
  <div class="assistant-suggestions" id="assistant-suggestions"></div>
  <div class="assistant-compose">
    <form id="assistant-form">
      <textarea id="assistant-input" rows="2" placeholder="Ask about trends, anomalies, compliance, or explain a chart…" aria-label="Ask the assistant"></textarea>
      <div class="assistant-compose-actions">
        <label class="chip-btn" for="assistant-image" title="Upload a chart screenshot for offline visual analysis">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
          Chart image
        </label>
        <input type="file" id="assistant-image" accept="image/png,image/jpeg,image/webp" hidden/>
        <button type="button" class="chip-btn" id="assistant-mode" data-mode="simple" onclick="cycleAssistantMode()">Simple language</button>
        <button class="btn btn-primary btn-sm" type="submit" id="assistant-send">Send</button>
      </div>
    </form>
    <p class="assistant-disclaimer">Local, rule-based assistant. It describes evidence and caveats; it does not make regulatory decisions.</p>
  </div>
</aside>

<div id="command-palette" class="palette" hidden>
  <div class="palette-box" role="dialog" aria-modal="true" aria-label="Command palette">
    <div class="palette-input-wrap">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="palette-input" placeholder="Search actions, sections and questions…" autocomplete="off" aria-label="Search commands"/>
      <kbd>Esc</kbd>
    </div>
    <div id="palette-results" class="palette-results" role="listbox"></div>
  </div>
</div>

<div id="toast-container"></div>

<script src="/static/js/app.js"></script>
<script src="/static/js/enhancements.js"></script>
<script src="/static/js/assistant.js"></script>
</body>
</html>
"""

INDEX.write_text(shell, encoding="utf-8")
print(f"Wrote {CSS_OUT.relative_to(ROOT)} ({len(css)} chars)")
print(f"Wrote {JS_OUT.relative_to(ROOT)} ({len(js)} chars)")
print(f"Rebuilt {INDEX.relative_to(ROOT)} ({len(shell)} chars)")
