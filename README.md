# AQACT — Air Quality Analysis & Compliance Assessment Tool

**A local-first workspace for turning raw sensor exports into clean, defensible air-quality analysis and publication-ready figures.**

AQACT runs on your machine and keeps all analysis, charts, and exports local. The AI assistant is **keyless**: it runs in the browser through [Puter.js](https://developer.puter.com/), with automatic model routing, silent failover, and a deterministic local engine as the final fallback. No API key, `.env` file, proxy, or provider account is configured in AQACT. No telemetry is collected, and the full dataset is never uploaded — the browser sends a compact analytical summary and any attachment you explicitly add.

---

## Highlights

| Area | What you get |
|------|--------------|
| **Interface** | AdminCN single standard: solid surfaces, icon-only auto-expanding navigation, hover-revealed right AI panel, flip-card guidance, keyboard shortcuts, responsive mobile layout |
| **Data in** | CSV, XLSX, XLS, TXT; single files, daily batches, or multi-station uploads |
| **Cleaning** | Provider-aware pipelines for 11 sensor families plus generic auto-detection; optionally drop columns that remain completely empty after cleaning |
| **Exploration** | Statistics, missing-data maps, trends, correlations, diurnal/seasonal profiles, AQI |
| **Analysis** | Extreme-value statistics, WHO/EPA compliance screening, machine learning, model evaluation, station comparison |
| **AI assistant** | Keyless Puter.js layer with automatic routing across Claude, GPT-4o, Gemini, DeepSeek and Qwen; explicit model selection, sign-in and request quotas, one-click dataset/chart interpretation, silent failover, deterministic local fallback, and local conversation caching |
| **Publication** | High-resolution PNG, vector SVG, and print-ready PDF with title, caption, source, and figure number |
| **Reliability** | Session versioning, undo, result caching, JSON-safe responses, clear error messages with hints |

---

## Latest changes (2026-11-23)

- **Fixed** — AI pre-suggested common queries are now capped at **4** everywhere (local engine, assistant APIs, and the browser chip renderer), leaving more room for the response view and keeping the chips inside the panel.
- **Fixed** — the follow-up question chips have been removed entirely from the assistant panel. The response output now uses the full remaining panel height while keeping its message padding, fonts, and bubble sizing unchanged. The surrounding usage, quick-action and compose controls are compacted, the composer starts at two rows, and cache-busted asset URLs make the change visible immediately so roughly 7–10 response lines are visible before scrolling.
- **Changed** — the side navigation's expanded panel is narrower (**210px** instead of 268px) and its labels, section heading, brand subtitle, and footer text are smaller. Navigation icons remain **17px** and all section handlers are unchanged.
- **Fixed** — the Clean page Data Preview starts **collapsed to five rows** with an arrow/button to expand the full 150-row preview.
- **Changed** — typography now uses **Inter** for UI/body text, **IBM Plex Mono** with tabular numerals for IDs and tables, and **Lora** for editorial headings; all typefaces are bundled locally.
- **Changed** — the Clean workspace now reads Data Preview → provider/cleaning options → provider column mapping → four collapsed tool cards → Export → Cleaned Data Preview.
- **Added** — a system-aware dark theme with a topbar toggle and persisted `aqat-theme` preference.

See [`CHANGELOG.md`](CHANGELOG.md) for the full dated record.

---

## Quick Start

### Recommended

```bash
python run.py
```

`run.py` checks your Python version, installs missing Python packages and Leaflet 1.9.4 plus its heat plugin from npm when needed, prepares the Plotly, Leaflet, and MapLibre browser assets, generates the demo dataset if needed, starts the server, and opens your browser at <http://localhost:5000>. The page also has CDN fallbacks for external assets.

### Manual install

```bash
python -m pip install -r requirements.txt
python app.py
```

Python 3.8+ is required. Tested with Python 3.13.

### Try it without your own data

Click **Explore demo dataset** on the Upload screen. The demo contains 2,160 hourly readings from two synthetic stations, a pollution episode, missing values, and a full set of air-quality, meteorological, and AQI columns. It is a teaching dataset, not measurement data.

---

## Interface Tour

| Section | Purpose |
|---------|---------|
| **Upload** | Drag in one or more sensor exports and choose a provider; the data preview opens at the top of the Clean page |
| **Batch Stack** | Combine daily files from one deployment into a continuous time series |
| **Multi-Station** | Clean and compare several locations side by side |
| **Clean** | Data preview first (five rows collapsed, arrow to expand), then full-width provider options and column mapping, followed by collapsed side-by-side cards for Column Manager, Temporal Aggregation, Fill Missing Values, and Remove Outliers |
| **Explore** | Simple view by default with a flip to the technical view of the same statistics, missing-data diagnostics, prioritised insights, dataset Q&A |
| **Plots** | Expanded data-to-viz library: distribution, correlation, relationship, evolution, composition, ranking, geospatial, and scientific analytics charts alongside the original time-series, wind, polar, and compliance views; saved gallery and publication export |
| **Map** | Topographic (OpenTopoMap) keyless default, OpenFreeMap vector styles, satellite/streets raster styles, and a local offline-grid fallback through Leaflet 1.9.4 with MapLibre GL |
| **Extreme** | Return levels, block maxima, exceedance statistics |
| **Compliance** | WHO and EPA threshold screening |
| **Machine Learning** | Train and inspect predictive models |
| **Model Eval** | Compare observed and modelled columns with error metrics |
| **About** | Interactive system-architecture flip card, purpose/workflow/scope/responsible-use notes, and developer credits |

### Visual system and navigation

AdminCN (the free shadcn/Tailwind dashboard at `preview.themewagon.com/admincn/dashboard/orders/`) supplies the colour and surface system. `static/css/celestial.css` is the only application stylesheet linked by the page: `#ffffff` canvas and cards, `#fafafa` sidebar, `#e5e5e5` borders, a neutral `#171717` primary, the AdminCN chart accents (`#f05100`, `#009588`, `#104e64`, `#fcbb00`, `#f99c00`), and 10px card radii in the light default. Typography uses three screen-first families: Inter at 14px/1.55 with tabular numerals for UI and body copy, Lora for editorial headings and figures, and IBM Plex Mono with wide tracking for labels, IDs, tickers, and data tables. There is no blur, translucency, or glass. Legacy stylesheets remain on disk only as unlinked references.

A dark theme is available from the topbar colour-theme control. On first visit the page follows your system `prefers-color-scheme` setting, defaulting to light when no preference is reported; an explicit choice is remembered in `localStorage` as `aqat-theme`. Charts are relaid out with theme-aware Plotly colours, and all other analysis, map, export, and AI behaviour is unchanged.

The workspace rail is icon-only by default. Hovering or keyboard-focusing it smoothly reveals the full section labels and hierarchy without a floating panel or heavy motion. On small screens the rail becomes a conventional slide-out menu. The assistant panel is hidden on load: hover the slim right-edge **Ask AI** rail (or focus it with the keyboard) to preview it, or click that rail, the topbar **Ask AI** button, or the floating button to pin it open. Moving the pointer away from an unpinned panel closes it again; the close button always hides it. When open it occupies its own column rather than covering the workspace, and its conversation area is wide and tall so answers stay readable with less scrolling. Upload, Explore, and About use click-to-flip cards so forms, drop zones, and links stay usable; each face keeps the same underlying data and handlers. Explore opens on the **Simple View** and flips to the Technical View. Reduced-motion preferences disable flips and transitions.

The AQACT brand mark next to the application name uses the Flaticon air-pollution icon (`air-pollution_8382834`, bundled locally), and the assistant uses a human-head AI icon throughout.

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl` + `K` | Open the command palette |
| `?` | Open the command palette |
| `ArrowUp` / `ArrowDown` | Move through command results |
| `Enter` | Run the selected command |
| `Esc` | Close the palette, assistant, or mobile navigation |

### Workspace restore

The active session, dataset shape, column metadata, and current section are cached locally. Reloading the page restores the workspace automatically. If the server has restarted and the session has expired, AQACT starts clean instead of showing an error. Location and station labels are optional; multi-station files use their filenames when no label is entered.

---

## AI Assistant

The assistant is a hover-revealed right panel and a unified, keyless chat interface. Puter.js is loaded in the browser and models are called directly from the page; the Flask server never contacts an AI provider and stores no credentials. AI features require a completed Puter sign-in: an unauthenticated request immediately opens the sign-in flow and is blocked until the browser confirms the account. Browsing, uploads, statistics, plots, exports, and local analysis remain available without signing in. AQACT itself needs no API key, `.env` file, proxy, or provider account. If Puter is unavailable, rate-limited, or offline, the assistant falls back to the deterministic local engine without affecting any analysis or export.

The **Model** selector defaults to **Auto · route by task** so the routing contract below is preserved. Choosing GPT-4o, Claude, Gemini, DeepSeek, or Qwen makes that model the first attempt; if it fails, the canonical failover chain resumes from the top. The choice is remembered locally.

### Request limits and cooldown

The server keeps a small in-memory AI request ledger keyed by AQACT session. It records requests per page session, UTC hour, and UTC day; it never stores credentials or calls a provider. Defaults are 40 requests per session, 120 per hour, and 500 per day. Crossing any threshold immediately signs the AI session out, shows a clear limit banner, logs an `AI quota sign-out` warning, and starts a 900-second cooldown. Signing in again resets only the session counter; hourly/daily counters and the active cooldown persist, so the limit cannot be bypassed from the page. The sidebar always shows current usage. Local analysis stays available during a cooldown.

Configure limits with environment variables (no secrets are involved):

| Variable | Default | Meaning |
|----------|---------|---------|
| `AQAT_AI_SESSION_LIMIT` | `40` | Requests per browser page session |
| `AQAT_AI_HOUR_LIMIT` | `120` | Requests per UTC hour |
| `AQAT_AI_DAY_LIMIT` | `500` | Requests per UTC day |
| `AQAT_AI_COOLDOWN_SECONDS` | `900` | Lockout after a threshold sign-out |
| `AQAT_AI_TOKEN_WARNING` | `10000` | Token warning in the usage panel |

It can:

- Summarise the dataset, columns, time range, sampling interval, and quality grade.
- Report averages, extremes, percentiles, missing values, and data-quality issues.
- Identify trends, anomalies, correlations, diurnal/seasonal patterns, and station differences.
- Screen for WHO/EPA guideline exceedances.
- Explain an analysis result in **simple** or **technical** language.
- Interpret the current chart, or reason about an attached chart screenshot.
- Compare the active dataset with an attached CSV/Excel/JSON reference file.
- Generate prioritised insights, recommendations, and report summaries.
- Answer glossary questions such as “What is PM2.5?”.

### One-click actions

The panel exposes four quick actions that work with a loaded dataset:

| Action | What it does |
|--------|--------------|
| **Explain dataset** | Summarises coverage, variables, quality and caveats |
| **Explain chart** | Captures the rendered chart, reads it visually, and connects it to the dataset |
| **Find anomalies** | Ranks trends, outliers, gaps and correlations by importance |
| **Report summary** | Drafts an executive summary with findings, limits and next steps |

### Automatic routing and failover

The router picks the best model for the task:

| Task | Preferred model |
|------|-----------------|
| Statistical reasoning | Claude |
| Dataset analysis | DeepSeek |
| Research writing | Claude |
| Chart interpretation | Gemini |
| General chat | Qwen |
| Fast UI help | Qwen |

With **Auto** selected, every request tries the task’s preferred model first, then walks the canonical chain:

```text
Claude → GPT-4o → Gemini → DeepSeek → Qwen
```

An explicitly selected model is tried first, then the same canonical chain. Failures are retried silently. Provider error text is never shown; the sidebar keeps the conversation and shows a friendly recovery note such as “Answered with GPT-4o as a backup model to keep you moving.” If every online model fails, the deterministic local engine answers and is labelled **Local analysis**. Analytics, charts, exports, dataset processing and the local Q&A engine never depend on the network.

The routing table, model registry, and fallback chain are mirrored in `assistant/status.py` and `static/js/services/router.js`; `/api/assistant/health` reports them, plus quota configuration, for diagnostics.

### What leaves the machine

The browser sends the question plus any image or data file the user explicitly attaches. Before that:

1. The server builds a compact analytical pack — schema, summary statistics, correlations, anomalies, exceedance counts, station comparisons, downsampled series, and a small head/tail preview — and returns it from `/api/assistant/context`.
2. Attachments are parsed in memory by `/api/assistant/attachment`, bounded (default 8 MB, 5,000 rows, 60 columns), and reduced to a compact summary. Raw files are never written to disk.
3. Chart images are downscaled and re-encoded to JPEG in the browser before they reach Puter.
4. Only that reduced context is sent to the model. The full dataset is never forwarded.

The browser caches the compact context, attachment reductions, chart explanations, insights and conversation in `localStorage`. Cached answers are reused for a short window and invalidated when the session or data changes. A page reload restores the conversation even though the model call happened client-side.

### Privacy and scientific limits

The assistant explains the evidence it is given. It does not perform causal inference, does not make regulatory or medical decisions, and does not claim to read exact tick values from pixels. Treat attachment contents and dataset text as untrusted data. Verify important results against reference instruments and local standards before publication.

---

## Publication Figures

Open **Plots**, build a chart, then use the **Publication** card.

1. Add an optional figure number, caption, source, page size, and DPI.
2. Click **Suggest caption** to draft a caption from the chart metadata.
3. Export **PNG**, **SVG**, or **PDF**.

Charts are rendered in the browser and composed locally on the server with Pillow. No server-side Plotly, Kaleido, browser automation, or network access is required. PDF and PNG pages include the figure, title, caption, source line, and figure number. SVG exports preserve vector chart content with a text metadata wrapper.

If the underlying data changes after a chart is drawn, AQACT marks the figure as stale and refuses to export it until you regenerate the chart.

---

## Workflow Guide

### Single dataset

1. **Upload** → add a CSV/Excel file.
2. **Clean** → pick the provider and cleaning options, then run the pipeline.
3. **Explore** → review statistics, missing values, and prioritised insights.
4. **Plots** → build charts and export publication figures.
5. **Map** → inspect spatial patterns.
6. **Extreme / Compliance** → run screening and extreme-value analysis.
7. **Machine Learning / Model Eval** → train and evaluate models.

### Daily file batch

1. Open **Batch Stack**.
2. Choose the provider and station name.
3. Add all daily files and click **Stack & Load**.
4. Continue in **Clean** as usual.

### Multi-station comparison

1. Open **Multi-Station**.
2. Choose the provider and add one file per station.
3. Label each station, upload, then clean all stations.
4. Select the pollutant, time resolution, and standards.
5. Run the comparative analysis.

---

## Supported Sensors

| Provider | PM | Gas | Met | GPS | CO2 | VOC | NC |
|----------|:--:|:---:|:---:|:---:|:---:|:---:|:--:|
| PurpleAir | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Airnote | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirGradient | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Clarity | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirQo | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirQoon | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirBeam | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Sensirion SEN5x | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Sensirion SPS30 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Atmotube Pro | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| PMS5003/7003 | ✅ | ❌ | ✅* | ✅ | ❌ | ❌ | ✅ |
| Generic auto-detect | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

\* PMS5003 met data requires an attached DHT22 or BME280 logged to the same CSV.

---

## Project Structure

```
AQAT_v2_fixed4/
├── run.py                     ← Start here: python run.py
├── app.py                     ← Flask application factory and API routes
├── requirements.txt
├── templates/
│   └── index.html             ← Single-page application shell
├── static/
│   ├── css/celestial.css      ← Only linked stylesheet: AdminCN standard
│   ├── fonts/inter-latin.woff2 ← Bundled Inter sans face for UI/body copy
│   ├── fonts/lora-*.woff2 ← Bundled Lora serif faces for editorial headings
│   ├── fonts/ibm-plex-mono-*.woff2 ← Bundled IBM Plex Mono faces for codes and tables
│   ├── air-pollution-8382834.png ← Flaticon brand mark beside AQACT
│   ├── css/app.css            ← Unlinked legacy reference
│   ├── css/modern.css         ← Unlinked legacy reference
│   ├── css/components.css     ← Unlinked legacy reference
│   ├── css/atelier.css        ← Unlinked legacy reference
│   ├── js/app.js              ← Original analytics and chart logic
│   ├── js/enhancements.js     ← Navigation, insights, export, palette
│   ├── js/assistant.js        ← Hover-revealed panel, auth/quota, model selector, local cache
│   └── js/services/
│       ├── ai.js              ← Puter.js loader, transport, response normalisation
│       ├── router.js          ← Task classification, model table, fallback order
│       ├── fallback.js        ← Silent retry chain and friendly recovery notes
│       ├── cache.js           ← localStorage TTL/LRU cache for context and chat
│       ├── datasetAssistant.js ← Prompts and structured dataset answers
│       └── chartInterpreter.js ← Chart-image prompts and figure reduction
├── core/
│   ├── cache.py               ← TTL + LRU result cache
│   ├── dataio.py              ← Upload ingestion and table reading
│   ├── errors.py              ← JSON error handlers and ApiError
│   ├── serialization.py       ← JSON-safe numpy/pandas conversion
│   └── sessions.py            ← Thread-safe sessions, versions, undo
├── assistant/
│   ├── context.py             ← Compact dataset/attachment context
│   ├── engine.py              ← Intent detection and dataset Q&A
│   ├── insights.py            ← Prioritised insights and plot guides
│   ├── profiling.py           ← Dataset profiling and quality scoring
│   ├── quota.py               ← In-memory AI request ledger and cooldown
│   ├── status.py              ← Puter routing/fallback metadata for health
│   └── vision.py              ← Offline chart-image inspection
├── api/
│   ├── assistant_api.py       ← Local context, attachments, history, fallback Q&A
│   └── export_api.py          ← /api/export/*
├── analysis/                  ← AQI, extreme, compliance, ML, comparison
├── cleaners/                  ← Provider-specific cleaning pipelines
├── plots/generators.py        ← Plotly figure builders
├── sample_data/
│   ├── make_demo.py           ← Deterministic demo generator
│   └── demo_air_quality.csv
├── tests/
│   ├── test_smoke.py          ← Offline API smoke tests
│   ├── test_js_services.py    ← Runs the Node service tests when Node is present
│   └── js/ai_services.test.mjs ← Routing, failover, prompts, cache tests
└── backups/                   ← Original files preserved during the upgrade
```

---

## API Overview

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | Version, cache, session and offline status |
| `POST` | `/api/demo` | Load the bundled demo dataset |
| `GET` | `/api/session` | Session summary, columns, dtypes, preview |
| `POST` | `/api/undo` | Restore the previous data version |
| `GET` | `/api/profile` | Dataset profile and quality score |
| `GET` | `/api/insights` | Prioritised findings |
| `GET/POST` | `/api/assistant/suggestions` | Context-aware question suggestions |
| `GET/POST` | `/api/assistant/context` | Compact dataset pack for the browser AI layer |
| `POST` | `/api/assistant/attachment` | Parse and reduce an attached reference file |
| `GET/POST` | `/api/assistant/history` | Read or append session chat history |
| `POST` | `/api/assistant/chat` | Local fallback: question + optional chart image + optional data file |
| `POST` | `/api/assistant/ask` | Backwards-compatible local JSON question answering |
| `POST` | `/api/assistant/explain` | Deterministic analysis/chart explanation (captions) |
| `POST` | `/api/assistant/plot_image` | Backwards-compatible local chart-image inspection |
| `GET` | `/api/assistant/health` | Keyless Puter routing, fallback chain, limits, and quota config |
| `GET` | `/api/assistant/quota` | Current AI usage counters and configurable limits |
| `POST` | `/api/assistant/quota/session` | Record a browser Puter sign-in for an AQACT session |
| `POST` | `/api/assistant/quota/release` | End an AI session without clearing rolling counters |
| `POST` | `/api/assistant/quota/signout` | Acknowledge a threshold-driven sign-out |
| `POST` | `/api/assistant/quota/consume` | Authoritative pre-flight reservation before a model call |
| `GET` | `/api/export/capabilities` | Supported formats and limits |
| `POST` | `/api/export/figure` | Compose PNG, PDF, or SVG publication output |

All errors return JSON with `error`, `code`, `hint`, and `request_id` where available.

---

## Testing

```bash
python -m unittest discover -s tests -v
```

The Python suite covers health, the demo dataset, session rehydration, profiling, insights, rename/undo, assistant Q&A, compact context, attachment reduction, history persistence, plot explanation, PNG/PDF/SVG export, the AdminCN single-standard shell, hover-revealed AI panel, keyless map regressions, and the AI auth/quota guard. Everything runs offline and does not bind a network port.

The browser services have their own Node tests (Node 18+):

```bash
node --test tests/js/ai_services.test.mjs
```

They verify task routing, the model registry and validation, explicit-model-first failover, history preservation during failover, friendly error messages (no provider text), the sign-in-required auth message, response normalisation, structured prompt/answer parsing, chart reduction, and the local TTL/LRU cache. `tests/test_js_services.py` runs them automatically when `node` is on `PATH` and skips otherwise.

To verify Puter.js in a real browser, load a dataset, open the assistant, and confirm `/api/assistant/health` reports `mode: "puter"` and `requires_key: false`. Sending a question without signing in should open Puter sign-in and block the request; after signing in, the quota summary should increment. If the browser cannot reach `js.puter.com`, the assistant should fall back to **Local analysis** without breaking any other feature.

---

## Scientific Notes and Limitations

- AQACT is an analysis workspace, not a regulatory monitor. Always validate against reference instruments and your local authority’s methods.
- Low-cost sensors are sensitive to humidity, temperature, ageing, and firmware changes. The cleaners apply common corrections, but they cannot replace calibration.
- The bundled demo data is synthetic and must not be used for compliance decisions.
- WHO/EPA screening uses configurable annual, 24-hour, and 8-hour reference values. Check the selected standards before reporting.
- The assistant explains the numbers it is given. It does not perform causal inference and does not read exact values from screenshots. Online answers can be wrong or overconfident; the caveats are part of the answer, not decoration.
- Puter.js is optional. If the script is blocked, the browser is offline, every model fails, or a request times out, AQACT falls back to the local engine and labels the answer as **Local analysis**. Analytics and exports are unaffected.
- Time series can be averaged to hourly, daily, weekly or monthly values and summarized as means, medians, percentiles or record counts. OpenAir-inspired polar surfaces use smoothed, count-weighted wind-vector bins; annulus plots show pollutant concentrations by wind direction across hour, weekday, season or year. These are transparent approximations for exploratory analysis, not substitutes for OpenAir GAM fits or its full uncertainty modeling.
- The expanded chart library adds ridgeline, density, ECDF, beeswarm, pairplot, correlogram, hexbin, connected scatter, streamgraph, rolling trend, seasonal decomposition, treemap, sunburst, waffle, ordered bar, lollipop, Cleveland dot, spatial density/cluster maps, binned heatmaps, temporal geospatial facets, uncertainty bands, bootstrap confidence intervals, anomaly detection, residual diagnostics, forecast verification, and sensor QC. New generators are SVG-capable, bounded for large datasets, and use the same local `/api/plot` pipeline as the original charts.
- The map uses Leaflet 1.9.4, the Leaflet heat plugin, and MapLibre GL through keyless OpenFreeMap vector styles. The default basemap is Topographic (OpenTopoMap); satellite, streets and the OpenFreeMap vector styles remain selectable, with a local offline-grid fallback when tiles cannot load. `run.py` prepares the bundled browser assets and the page has CDN fallbacks. No map API key is required.
- Very large uploads are bounded by `AQAT_MAX_UPLOAD_MB` (default 500 MB). Sessions expire after 12 hours by default.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Charts do not appear | Ensure `static/plotly-2.35.2.min.js` exists, or allow the CDN fallback to load once |
| Map is blank | Check that the browser can load Leaflet 1.9.4, MapLibre GL, and the Topographic/OpenFreeMap tiles. If tiles are blocked, choose **Offline grid** from the Basemap selector; restart `python run.py` to install/copy local assets. |
| “Demo dataset is not installed” | Run `python sample_data/make_demo.py` or restart `python run.py` |
| PDF export fails | Ensure Pillow is installed: `python -m pip install Pillow` |
| Session disappears after a server restart | In-memory sessions are intentionally ephemeral; reload the data or use the demo |
| Assistant badge says “Offline · local analysis” | Allow `https://js.puter.com` in your browser, ad-blocker, or network policy. Local analysis still works. |
| Puter asks you to sign in | AI features require Puter sign-in. AQACT needs no key, `.env` file, or provider configuration. |
| “AI access is paused” after a limit | A session/hour/day threshold was reached. The AI session was signed out for the cooldown (`AQAT_AI_COOLDOWN_SECONDS`, default 900). Re-sign-in is allowed after the pause; local analysis works immediately. |
| Assistant says a backup model answered | Expected failover. The preferred or explicitly selected model was busy; the conversation and context were preserved. |
| Cross-origin assistant request returns 403 | Same-origin is the default; add the UI host to `AQAT_ALLOWED_ORIGINS` only when it is genuinely separate from the API host |
| Port already in use | PowerShell: `$env:PORT=5001; python run.py`. macOS/Linux: `PORT=5001 python run.py` |

---

## License and Credits

AQACT builds on the original Climate-Air-Health analysis platform and its provider cleaners. The upgraded interface, offline assistant, insight engine, publication exporter, session store, and test suite are included in this repository. Use and adapt it for research, teaching, and operational screening with appropriate scientific review.
