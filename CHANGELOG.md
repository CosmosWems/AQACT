# Changelog

All notable changes to AQACT are documented here. Dates use `YYYY-MM-DD`.

## [2026-11-23]

### Fixed

- AI pre-suggested common queries are capped at **4** across the deterministic engine, `/api/assistant/suggestions`, `/api/assistant/context`, and the browser chip renderer. The no-session fallback already contained four prompts. This frees vertical space for the response view and keeps the suggestion row inside the assistant window.
- Removed the follow-up question chips from the assistant panel entirely, including the initial prompts and per-answer suggestions. The response output now fills the full remaining panel height; message padding, fonts, and bubble sizing are unchanged.
- Compacted the surrounding AI usage, quick-action and composer controls and reduced the composer to two rows so about 7–10 response lines remain visible before scrolling. Cache-busted `celestial.css` and `assistant.js` with `?v=2.0.1` so the updated panel loads immediately instead of from browser cache.
- The Clean page Data Preview now starts **collapsed** after upload and shows only **five** data rows. A header arrow/button toggles the full 150-row preview and reports its state through `aria-expanded`; the preview resets to collapsed on each new upload.

### Changed

- Reduced the side navigation expanded panel from **268px** to **210px** (`--sidebar-width`) for both light and dark themes, including the mobile slide-out rail.
- Reduced side navigation font sizes: section labels, tab labels, brand subtitle, status pill, and version text are smaller. Nav icons remain **17px**, and the collapsed icon-only rail, hover/focus reveal, and all section handlers are unchanged.
- Standardised typography on three screen-first families: **Inter** at 14px/1.55 with tabular numerals for UI/body copy, **IBM Plex Mono** for codes, IDs, labels, and data tables, and **Lora** for editorial headings. Inter and Lora are bundled locally; `static/css/celestial.css` remains the only linked application stylesheet.
- Reordered the Clean workspace to: Data Preview → full-width Provider & Cleaning Options → full-width Provider Column Mapping → four collapsed side-by-side tool cards (Column Manager, Temporal Aggregation, Fill Missing Values, Remove Outliers) → Export → Cleaned Data Preview.

### Added

- System-aware dark theme with a topbar toggle, `localStorage` key `aqat-theme`, and theme-aware Plotly relayout. Light mode remains the AdminCN-styled default.
- This changelog, plus a matching **Latest changes (2026-11-23)** section in `README.md`.

### Preserved

- Puter.js remains the only AI layer: no provider keys, `.env` credentials, proxy, or provider accounts. Browsing and analysis remain auth-free; AI still requires Puter sign-in.
- Exact model routing (`statistics→claude`, `dataset→deepseek-chat`, `research→claude`, `chart→gemini`, `chat/fast→qwen`) and fallback order (`claude`, `gpt-4o`, `gemini`, `deepseek-chat`, `qwen`).
- Quota counters, cooldown, threshold sign-out logging, and re-sign-in behaviour are unchanged.
- Keyless OpenFreeMap and offline-grid map stack, with Topographic as the default basemap.
- All chart types, flips, transitions, animations, visualizations, APIs, and data-handling workflows.
