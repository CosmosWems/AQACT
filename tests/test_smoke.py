"""Fast, offline smoke tests for the AQACT backend.

Run from the project root:

    python -m unittest discover -s tests -v

The tests use Flask's test client, so they never bind a network port and never
touch a remote service.
"""

from __future__ import annotations

import base64
import io
import json
import time
import unittest
from pathlib import Path

from PIL import Image

from app import create_app
from assistant.quota import DEFAULT_LIMITS, clear as clear_ai_quota, configure as configure_ai_quota


def _png_data_url(width: int = 160, height: int = 90) -> str:
    image = Image.new("RGB", (width, height), (18, 32, 56))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


class AqactApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app({"TESTING": True})
        cls.client = cls.app.test_client()

    def _demo_session(self) -> str:
        response = self.client.post("/api/demo")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["session_id"])
        return body["session_id"]

    # ── Health and demo ────────────────────────────────────────────────────
    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["offline"])
        self.assertFalse(body["network_required"])

    def test_assistant_health_is_keyless(self) -> None:
        response = self.client.get("/api/assistant/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["mode"], "puter")
        self.assertEqual(body["provider"], "puter")
        self.assertFalse(body["requires_key"])
        self.assertTrue(body["vision"])
        self.assertEqual(body["routing"]["chart"], "gemini")
        self.assertEqual(body["routing"]["statistics"], "claude")
        self.assertEqual(body["fallback"], ["claude", "gpt-4o", "gemini", "deepseek-chat", "qwen"])

    def test_map_uses_keyless_tiles_with_offline_fallback(self) -> None:
        """Topographic is the keyless default and the local grid is the fallback."""
        root = Path(__file__).resolve().parents[1]
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        run_py = (root / "run.py").read_text(encoding="utf-8")
        lower = app_js.lower()
        # Topographic is the default; every network style stays keyless.
        self.assertIn("mapStyle:'topographic'", app_js)
        self.assertIn("MAP_BASEMAPS[name] ? name : 'topographic'", app_js)
        self.assertIn('<option value="topographic" selected>Topographic</option>', index_html)
        self.assertIn('<span id="map-basemap-text">Topographic</span>', index_html)
        self.assertIn("https://tiles.openfreemap.org/styles/liberty", app_js)
        self.assertIn("https://tiles.openfreemap.org/styles/dark", app_js)
        self.assertIn("https://tiles.openfreemap.org/styles/positron", app_js)
        self.assertIn("L.maplibreGL", app_js)
        self.assertIn("World_Imagery/MapServer/tile", app_js)
        self.assertIn("tile.opentopomap.org", app_js)
        self.assertIn("World_Street_Map/MapServer/tile", app_js)
        self.assertIn("offline: { label: 'Offline grid'", app_js)
        for needle in ("api_key", "apikey", "access_token", "token=", "subscription-key"):
            self.assertNotIn(needle, lower, f"map styles must stay keyless ({needle})")
        # The local canvas grid is the fallback when tiles cannot load.
        self.assertIn("useOfflineBasemap", app_js)
        self.assertIn("navigator.onLine", app_js)
        self.assertIn("createOfflineGridLayer", app_js)
        # Leaflet loads asynchronously, so the layer must be built lazily.
        self.assertNotIn("const OfflineGridLayer = L.Layer.extend", app_js)
        for provider in ("tile.openstreetmap.org", "basemaps.cartocdn.com"):
            self.assertNotIn(provider, app_js)
        # MapLibre assets are bundled locally and loaded with CDN fallbacks.
        self.assertIn("maplibre-gl-5.24.0.js", index_html)
        self.assertIn("leaflet-maplibre-gl-0.1.4.js", index_html)
        self.assertIn("maplibre-gl-5.24.0.js", run_py)
        for label in ("OpenFreeMap", "Satellite", "Topographic", "Offline grid"):
            self.assertIn(label, index_html)

    def test_theme_switching_keeps_offline_basemap_regression(self) -> None:
        """The dark theme must not disturb the keyless map layer lifecycle."""
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        enhancements = (root / "static" / "js" / "enhancements.js").read_text(encoding="utf-8")
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        for needle in ("aqat-theme", "prefers-color-scheme", "data-theme", "id=\"theme-toggle\""):
            self.assertIn(needle, index_html)
        for needle in ("applyTheme", "toggleTheme", "aqat-theme", "matchMedia", "window.rethemePlots"):
            self.assertIn(needle, enhancements)
        for needle in ("data-theme", "rethemePlots", "Plotly.relayout"):
            self.assertIn(needle, app_js)
        self.assertIn("mapBaseLayer", app_js)
        self.assertIn(".redraw()", app_js)
        self.assertIn("useOfflineBasemap", app_js)

    def test_demo_dataset(self) -> None:
        response = self.client.post("/api/demo")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertGreater(body["shape"][0], 100)
        self.assertGreater(body["shape"][1], 5)
        self.assertIn("pm2.5", body["columns"])

    # ── Session, profile and insights ─────────────────────────────────────
    def test_session_summary_is_rehydratable(self) -> None:
        sid = self._demo_session()
        response = self.client.get(f"/api/session?session_id={sid}")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["session_id"], sid)
        self.assertIn("columns", body)
        self.assertIn("dtypes", body)
        self.assertTrue(body["preview"])
        self.assertTrue(body["cleaned"])

    def test_profile_and_insights(self) -> None:
        sid = self._demo_session()
        profile = self.client.get(f"/api/profile?session_id={sid}")
        self.assertEqual(profile.status_code, 200)
        profile_body = profile.get_json()
        self.assertEqual(profile_body["shape"][0], 2160)

        insights = self.client.get(f"/api/insights?session_id={sid}")
        self.assertEqual(insights.status_code, 200)
        items = insights.get_json()["insights"]
        self.assertIsInstance(items, list)
        self.assertTrue(items)
        self.assertTrue(any("simple" in item and "technical" in item for item in items))

    def test_unknown_session_returns_json_error(self) -> None:
        response = self.client.get("/api/session?session_id=does-not-exist")
        self.assertEqual(response.status_code, 404)
        body = response.get_json()
        self.assertIn("error", body)

    # ── Editing and undo ──────────────────────────────────────────────────
    def test_rename_and_undo(self) -> None:
        sid = self._demo_session()
        renamed = self.client.post("/api/columns/rename", json={
            "session_id": sid,
            "old_name": "VOC",
            "new_name": "VOC_renamed",
        })
        self.assertEqual(renamed.status_code, 200, renamed.get_data(as_text=True))
        self.assertIn("VOC_renamed", renamed.get_json()["columns"])

        undone = self.client.post("/api/undo", json={"session_id": sid})
        self.assertEqual(undone.status_code, 200, undone.get_data(as_text=True))
        self.assertIn("VOC", undone.get_json()["columns"])

    # ── Assistant ─────────────────────────────────────────────────────────
    def test_assistant_suggestions_and_ask(self) -> None:
        sid = self._demo_session()
        suggestions = self.client.get(f"/api/assistant/suggestions?session_id={sid}")
        self.assertEqual(suggestions.status_code, 200)
        suggestion_list = suggestions.get_json()["suggestions"]
        self.assertTrue(suggestion_list)
        self.assertLessEqual(len(suggestion_list), 4, "suggestion API must stay compact")

        answer = self.client.post("/api/assistant/ask", json={
            "session_id": sid,
            "question": "Summarise this dataset",
            "mode": "simple",
        })
        self.assertEqual(answer.status_code, 200, answer.get_data(as_text=True))
        body = answer.get_json()
        self.assertTrue(body.get("answer"))
        self.assertIn("bullets", body)

    def test_assistant_context_is_local_and_compact(self) -> None:
        sid = self._demo_session()
        response = self.client.post("/api/assistant/context", json={"session_id": sid})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["shape"][0], 2160)
        self.assertIn("context", body)
        self.assertIn("context_text", body)
        self.assertEqual(body["context"]["dataset"]["shape"][0], 2160)
        self.assertTrue(body["suggestions"])
        self.assertLessEqual(len(body["context_text"]), body["limits"]["context_chars"] + 32)

    def test_assistant_attachment_is_reduced(self) -> None:
        sid = self._demo_session()
        csv_bytes = b"time,pm2.5,station\n2024-01-01 00:00,12.5,A\n2024-01-01 01:00,18.0,A\n"
        response = self.client.post(
            "/api/assistant/attachment",
            data={
                "session_id": sid,
                "data_file": (io.BytesIO(csv_bytes), "reference.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        attachment = response.get_json()["attachment"]
        self.assertEqual(attachment["kind"], "table")
        self.assertEqual(attachment["shape"], [2, 3])
        self.assertTrue(attachment["columns"])

    def test_assistant_history_round_trip(self) -> None:
        sid = self._demo_session()
        posted = self.client.post("/api/assistant/history", json={
            "session_id": sid,
            "role": "user",
            "text": "Persist this turn.",
        })
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        history = self.client.get(f"/api/assistant/history?session_id={sid}")
        self.assertEqual(history.status_code, 200)
        messages = history.get_json()["messages"]
        self.assertTrue(any(entry.get("text") == "Persist this turn." for entry in messages))

    def test_assistant_chat_uses_local_engine(self) -> None:
        sid = self._demo_session()
        response = self.client.post("/api/assistant/chat", json={
            "session_id": sid,
            "question": "Summarise this dataset",
            "mode": "simple",
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["answer"])
        self.assertFalse(body["used_cloud"])
        self.assertEqual(body["provider"], "local")

    def test_assistant_plot_image_local_vision(self) -> None:
        sid = self._demo_session()
        response = self.client.post("/api/assistant/plot_image", json={
            "session_id": sid,
            "question": "Describe this chart",
            "image": _png_data_url(),
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["answer"])
        self.assertFalse(body["used_cloud"])
        self.assertIn("image", body)

    def test_assistant_explain_plot(self) -> None:
        sid = self._demo_session()
        plot = self.client.post("/api/plot", json={
            "session_id": sid,
            "plot_type": "timeseries",
            "params": {"columns": ["pm2.5"]},
        })
        self.assertEqual(plot.status_code, 200, plot.get_data(as_text=True))

        explain = self.client.post("/api/assistant/explain", json={
            "session_id": sid,
            "kind": "plot",
            "mode": "simple",
        })
        self.assertEqual(explain.status_code, 200, explain.get_data(as_text=True))
        body = explain.get_json()
        self.assertTrue(body.get("simple") or body.get("summary") or body.get("caption"))

    def test_time_series_charts_use_svg_traces(self) -> None:
        """Line and area charts must not depend on WebGL.

        Plotly's scattergl traces fail in browsers without WebGL, which made
        the interactive time-series chart appear blank.  Both chart types now
        share one generator and emit ordinary SVG scatter traces.
        """
        sid = self._demo_session()
        for plot_type in ("timeseries", "area"):
            response = self.client.post("/api/plot", json={
                "session_id": sid,
                "plot_type": plot_type,
                "params": {"columns": ["pm2.5", "pm10"]},
            })
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            figure = json.loads(response.get_json()["plot"])
            self.assertTrue(figure["data"])
            self.assertTrue(
                all(trace["type"] == "scatter" for trace in figure["data"]),
                f"{plot_type} should use SVG scatter traces, not WebGL",
            )

    # ── Publication export ────────────────────────────────────────────────
    def test_export_capabilities(self) -> None:
        response = self.client.get("/api/export/capabilities")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(set(body["formats"]), {"png", "pdf", "svg"})
        self.assertFalse(body["server_side_plotly"])

    def test_export_png_pdf_svg(self) -> None:
        image = _png_data_url()
        for fmt, mime in (("png", "image/png"), ("pdf", "application/pdf"), ("svg", "image/svg+xml")):
            response = self.client.post("/api/export/figure", json={
                "format": fmt,
                "image": image,
                "title": "Smoke test figure",
                "caption": "Generated by the automated smoke tests.",
                "source": "AQACT test suite",
                "figure_number": "1",
                "size": "wide",
                "dpi": 150,
            })
            self.assertEqual(response.status_code, 200,
                             f"{fmt} export returned {response.status_code}")
            self.assertIn(mime, response.headers.get("Content-Type", ""))
            self.assertGreater(len(response.data), 100)

    # ── Celestial standard, workflow hooks and AI guard ──────────────────
    def test_celestial_single_standard_preserves_workflow_hooks(self) -> None:
        """The Celestial shell must keep every behaviour hook intact."""
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        celestial_css = (root / "static" / "css" / "celestial.css").read_text(encoding="utf-8")
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        assistant_js = (root / "static" / "js" / "assistant.js").read_text(encoding="utf-8")

        self.assertIn('/static/css/celestial.css', index_html)
        self.assertIn('/static/css/celestial.css?v=2.0.1', index_html)
        self.assertIn('/static/js/assistant.js?v=2.0.1', index_html)
        for legacy in ("app.css", "modern.css", "components.css", "atelier.css"):
            self.assertNotIn(f'/static/css/{legacy}', index_html)
        for needed in (
            'id="theme-toggle"', "toggleTheme()", "aqat-theme",
            "prefers-color-scheme", 'id="theme-toggle-label"',
        ):
            self.assertIn(needed, index_html)
        self.assertNotIn("theme", assistant_js.lower())

        # The follow-up question strip is gone; response output keeps the full
        # remaining panel height instead of sharing it with suggestion chips.
        self.assertNotIn('id="assistant-suggestions"', index_html)
        self.assertNotIn("renderSuggestions", assistant_js)
        self.assertNotIn("loadSuggestions", assistant_js)
        self.assertNotIn("suggestion-chip", celestial_css)
        self.assertNotIn("assistant-suggestions", celestial_css)
        self.assertIn("With the question strip removed", celestial_css)
        self.assertIn("7-10 response lines", celestial_css)
        self.assertIn('id="assistant-input" rows="2"', index_html)

        for token in (
            "--primary: #171717",
            "--secondary: #f5f5f5",
            "--chart-1: #f05100",
            "background: #fafafa",
            "#sidebar:hover",
            "#sidebar:focus-within",
            "--sidebar-width: 210px",
            "#sidebar #tabs .tab-btn",
            "#sidebar .nav-label { font-size: 9px",
            ".flip-card",
            ".simple-view",
            "#assistant-peek",
            "@font-face",
            '"Inter"',
            '"Lora"',
            '"IBM Plex Mono"',
            "--font-sans:",
            "--font-mono:",
            "--font-serif:",
            "font: 14px/1.55 var(--font-sans)",
            "font-variant-numeric: tabular-nums",
            "--assistant-width: 380px",
            "--assistant-width: 420px",
            "min(390px, 100vw)",
            '[data-theme="dark"]',
            "color-scheme: dark",
            ".clean-tool-grid",
            ".clean-tool-summary",
            ".clean-tool-arrow",
            ".preview-header",
            "nth-child(n+6)",
        ):
            self.assertIn(token, celestial_css)
        lowered = celestial_css.lower()
        self.assertNotIn("geist", lowered)
        for forbidden in (
            "--sidebar-width: 255px",
            "width: 268px",
            "backdrop-filter",
            "blur(",
            "rgba(",
            "transparent",
            "--glass",
            ".hero-glow",
            "body::before",
            "gradient(",
        ):
            self.assertNotIn(forbidden, lowered)

        # Persistent right assistant with an explicit model selector and quota UI.
        for hook in (
            'id="app-shell"',
            'id="sidebar"',
            'id="tabs"',
            'id="topbar"',
            'id="main"',
            'id="assistant-drawer"',
            'id="assistant-scrim"',
            'id="assistant-fab"',
            'id="assistant-btn"',
            'id="assistant-messages"',
            'id="assistant-form"',
            'id="assistant-input"',
            'id="assistant-send"',
            'id="puter-auth-action"',
            'id="ai-usage-panel"',
            'id="assistant-model"',
            'id="ai-limit-banner"',
            'id="ai-quota-summary"',
        ):
            self.assertIn(hook, index_html)
        for model in ("gpt-4o", "claude", "gemini", "deepseek-chat", "qwen"):
            self.assertIn(f'value="{model}"', index_html)
        self.assertIn('class="assistant-drawer"', index_html)
        self.assertIn('id="assistant-peek"', index_html)
        self.assertIn('id="icon-ai-head"', index_html)
        self.assertIn('/static/air-pollution-8382834.png', index_html)
        self.assertLess(index_html.index('id="app-shell"'), index_html.index('id="assistant-drawer"'))
        self.assertLess(index_html.index('id="assistant-drawer"'), index_html.index('id="sidebar-scrim"'))
        for hook in ("revealAssistant", "scheduleAssistantHide", "state.pinned"):
            self.assertIn(hook, assistant_js)

        for function in ("openAssistant()", "closeAssistant()", "toggleAssistant()",
                         "switchTab('upload')", "switchTab('plots')", "loadDemoDataset()",
                         "uploadFiles()", "renderSimpleExplore", "toggleFlip(this)",
                         "toggleDataPreview()", "window.toggleDataPreview"):
            self.assertIn(function, index_html + app_js + assistant_js)
        for hook in ("upload-flip", "explore-flip", "about-flip", "file-input",
                     "file-items", "upload-progress", "progress-fill",
                     "explore-content", "explore-shape-grid", "missing-grid",
                     "columns-grid", "columns-list", "desc-table",
                     "explore-simple-content"):
            self.assertIn(f'id="{hook}"', index_html)
        self.assertIn("This platform is not for sale in any form.", index_html)
        self.assertIn("Data Input", index_html)
        self.assertIn("Visualization", index_html)

        simple_at = index_html.index('id="explore-simple-content"')
        technical_at = index_html.index('id="explore-content"')
        self.assertLess(simple_at, technical_at)
        self.assertIn("Simple View", index_html[simple_at - 600:simple_at])

        # Upload preview now belongs at the top of the Clean workspace, followed
        # by two full-width panels and four collapsed side-by-side tools.
        clean_at = index_html.index('id="panel-clean"')
        next_panel_at = index_html.index('id="panel-explore"')
        preview_at = index_html.index('id="upload-preview"')
        provider_at = index_html.index("Provider &amp; Cleaning Options")
        mapping_at = index_html.index('id="airnote-mapping-card"')
        tools_at = index_html.index('class="clean-tool-grid"')
        export_at = index_html.index('id="export-card"')
        cleaned_at = index_html.index('id="cleaned-preview"')
        self.assertLess(clean_at, preview_at)
        self.assertLess(preview_at, provider_at)
        self.assertLess(provider_at, mapping_at)
        self.assertLess(mapping_at, tools_at)
        self.assertLess(tools_at, export_at)
        self.assertLess(export_at, cleaned_at)
        self.assertLess(cleaned_at, next_panel_at)
        for hook in (
            "preview-rows-badge", "preview-cols-badge", "preview-table",
            "clean-provider", "airnote-mapping-grid", "clean-btn",
            "drop-empty-cols-btn", "rename-old", "rename-new", "delete-col",
            "add-col-name", "add-col-expr", "agg-freq", "agg-func",
            "interp-method", "interp-limit", "out-col", "out-method", "out-thresh",
        ):
            self.assertIn(f'id="{hook}"', index_html)
        for card_id in (
            "clean-tool-column-manager", "clean-tool-temporal-aggregation",
            "clean-tool-fill-missing", "clean-tool-remove-outliers",
        ):
            self.assertIn(f'<details class="card clean-tool-card" id="{card_id}">', index_html)
        self.assertEqual(index_html.count('class="clean-tool-arrow"'), 4)

        # Data Preview starts collapsed to five rows; the header arrow expands it.
        self.assertIn(
            '<button type="button" class="preview-header" onclick="toggleDataPreview()" '
            'aria-expanded="false" aria-controls="preview-table-wrap">',
            index_html,
        )
        self.assertIn('id="preview-table-wrap"', index_html)
        self.assertIn('id="preview-toggle-label"', index_html)
        self.assertIn('class="preview-arrow"', index_html)
        self.assertIn("panel.classList.toggle('expanded'", app_js)

    def test_assistant_health_preserves_routing_contract(self) -> None:
        """Health keeps the exact routing strings and exposes quota config."""
        response = self.client.get("/api/assistant/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["mode"], "puter")
        self.assertEqual(data["provider"], "puter")
        self.assertFalse(data["requires_key"])
        self.assertTrue(data["vision"])
        self.assertEqual(data["routing"], {
            "statistics": "claude",
            "dataset": "deepseek-chat",
            "research": "claude",
            "chart": "gemini",
            "chat": "qwen",
            "fast": "qwen",
        })
        self.assertEqual(data["fallback"], ["claude", "gpt-4o", "gemini", "deepseek-chat", "qwen"])
        self.assertEqual(data["quota"]["session"], DEFAULT_LIMITS["session"])
        self.assertEqual(data["quota"]["hour"], DEFAULT_LIMITS["hour"])
        self.assertEqual(data["quota"]["day"], DEFAULT_LIMITS["day"])

    def test_ai_quota_guard_blocks_unsigned_and_exceeded_requests(self) -> None:
        """A sign-in is mandatory; limits force a sign-out and a cooldown."""
        sid = self._demo_session()
        configure_ai_quota({"session": 1, "hour": 5, "day": 10, "cooldown_seconds": 2})
        clear_ai_quota()
        try:
            unsigned = self.client.post("/api/assistant/quota/consume", json={
                "session_id": sid,
                "authenticated": False,
            })
            self.assertEqual(unsigned.status_code, 401)
            self.assertEqual(unsigned.get_json()["code"], "ai_auth_required")

            started = self.client.post("/api/assistant/quota/session", json={"session_id": sid})
            self.assertEqual(started.status_code, 200)
            first = self.client.post("/api/assistant/quota/consume", json={
                "session_id": sid,
                "authenticated": True,
                "model": "claude",
                "task": "statistics",
            })
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.get_json()["counts"]["session"], 1)

            exceeded = self.client.post("/api/assistant/quota/consume", json={
                "session_id": sid,
                "authenticated": True,
            })
            self.assertEqual(exceeded.status_code, 429)
            self.assertEqual(exceeded.get_json()["code"], "ai_quota_exceeded")

            blocked_state = self.client.get(
                f"/api/assistant/quota?session_id={sid}"
            ).get_json()["quota"]
            self.assertFalse(blocked_state["authenticated"])
            self.assertTrue(blocked_state["blocked"])
            self.assertEqual(blocked_state["last_limit"], "session")

            # Re-sign-in is allowed, but the rolling cooldown cannot be bypassed.
            self.client.post("/api/assistant/quota/session", json={"session_id": sid})
            cooled = self.client.post("/api/assistant/quota/consume", json={
                "session_id": sid,
                "authenticated": True,
            })
            self.assertEqual(cooled.status_code, 429)
            self.assertEqual(cooled.get_json()["code"], "ai_quota_cooldown")

            time.sleep(2.1)
            self.client.post("/api/assistant/quota/session", json={"session_id": sid})
            recovered = self.client.post("/api/assistant/quota/consume", json={
                "session_id": sid,
                "authenticated": True,
            })
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(recovered.get_json()["counts"]["hour"], 3)
        finally:
            clear_ai_quota()
            configure_ai_quota(DEFAULT_LIMITS)

    def test_expanded_chart_library_is_wired_end_to_end(self) -> None:
        """Every new chart is exposed in the UI and returns a non-WebGL figure."""
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        generators = (root / "plots" / "generators.py").read_text(encoding="utf-8")

        new_keys = (
            "ridgeline", "density", "ecdf", "beeswarm", "pairplot", "correlogram",
            "hexbin", "connected_scatter", "streamgraph", "rolling_trend",
            "seasonal_decomposition", "treemap", "sunburst", "waffle", "ordered_bar",
            "lollipop", "cleveland_dot", "density_map", "cluster_map", "heatmap",
            "temporal_geospatial", "uncertainty_bands", "confidence_intervals",
            "anomaly_detection", "residual_diagnostics", "forecast_verification",
            "sensor_qc",
        )
        for key in new_keys:
            self.assertIn(f'value="{key}"', index_html, f"{key} missing from plot selector")
            self.assertIn(f"'{key}'", app_js, f"{key} missing from frontend dispatch")
            self.assertIn(f'"{key}":', generators, f"{key} missing from PLOT_MAP")
        for control in ("plot-ctrl-advanced", "plot-adv-group", "plot-adv-value",
                        "plot-lat-col", "plot-lon-col", "plot-adv-period",
                        "plot-adv-window", "plot-adv-method", "plot-adv-threshold",
                        "plot-adv-clusters", "plot-adv-lags", "plot-adv-topn",
                        "plot-adv-stat"):
            self.assertIn(f'id="{control}"', index_html)

        sid = self._demo_session()
        params_by_key = {
            "ridgeline": {"columns": ["pm2.5", "pm10", "temperature"]},
            "density": {"columns": ["pm2.5", "pm10"], "group_by": "location"},
            "ecdf": {"columns": ["pm2.5", "pm10"]},
            "beeswarm": {"column": "pm2.5", "group_by": "location"},
            "pairplot": {"columns": ["pm2.5", "pm10", "temperature", "RH"]},
            "correlogram": {"columns": ["pm2.5"], "lags": 12},
            "hexbin": {"x_col": "pm2.5", "y_col": "pm10", "bins": 20},
            "connected_scatter": {"x_col": "datetime", "y_col": "pm2.5", "group_by": "location"},
            "streamgraph": {"columns": ["pm2.5", "pm10"]},
            "rolling_trend": {"columns": ["pm2.5"], "window": 12},
            "seasonal_decomposition": {"column": "pm2.5", "period": 24},
            "treemap": {"columns": ["location"], "values": "pm2.5"},
            "sunburst": {"columns": ["location"], "values": "pm2.5"},
            "waffle": {"group_by": "location", "top_n": 8},
            "ordered_bar": {"column": "pm2.5", "group_by": "location"},
            "lollipop": {"column": "pm2.5", "group_by": "location"},
            "cleveland_dot": {"column": "pm2.5", "group_by": "location"},
            "density_map": {"column": "pm2.5", "bins": 20},
            "cluster_map": {"column": "pm2.5", "clusters": 3},
            "heatmap": {"column": "pm2.5", "bins": 12},
            "temporal_geospatial": {"column": "pm2.5", "period": "hour"},
            "uncertainty_bands": {"column": "pm2.5", "window": 12},
            "confidence_intervals": {"column": "pm2.5", "group_by": "location"},
            "anomaly_detection": {"column": "pm2.5", "method": "rolling", "threshold": 3},
            "residual_diagnostics": {"column": "pm2.5", "window": 12},
            "forecast_verification": {"observed_col": "pm2.5"},
            "sensor_qc": {"columns": ["pm2.5", "pm10"]},
        }
        webgl_types = {"scattergl", "barpolar", "densitymapbox", "scattermapbox", "scattergeo"}
        for key, params in params_by_key.items():
            response = self.client.post("/api/plot", json={
                "session_id": sid,
                "plot_type": key,
                "params": params,
            })
            self.assertEqual(response.status_code, 200,
                             f"{key}: {response.get_data(as_text=True)}")
            figure = json.loads(response.get_json()["plot"])
            self.assertTrue(figure["data"], f"{key} produced no traces")
            self.assertFalse(
                any(trace.get("type") in webgl_types for trace in figure["data"]),
                f"{key} must use SVG-capable Plotly traces",
            )


if __name__ == "__main__":
    unittest.main()
