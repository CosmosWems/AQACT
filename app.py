"""
AQACT — Air Quality Analysis & Compliance Assessment Tool
========================================================
Flask application factory and HTTP API.

The scientific code lives in ``analysis/``, ``cleaners/`` and ``plots/``.  This
module wires those modules to a small, thread-safe session store and keeps every
response JSON-safe.  It is intentionally free of UI logic.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
from flask import Flask, g, jsonify, render_template, request, send_file
from flask_cors import CORS

from analysis.aqi import compute_aqi
from analysis.comparative import run_comparative_analysis
from analysis.compliance import run_compliance_analysis
from analysis.extreme import run_extreme_analysis
from analysis.machine_learning import run_machine_learning
from analysis.model_eval import run_model_evaluation
from api import register as register_blueprints
from assistant.insights import dataset_insights
from assistant.profiling import dataset_profile
from assistant.quota import configure_from_env as configure_ai_quota
from assistant.status import public_status as assistant_status
from cleaners.airbeam import clean_airbeam
from cleaners.airgradient import clean_airgradient
from cleaners.airnote import clean_airnote
from cleaners.airqo import clean_airqo
from cleaners.airqoon import clean_airqoon
from cleaners.atmotube import clean_atmotube
from cleaners.clarity import clean_clarity
from cleaners.generic import clean_generic
from cleaners.pms5003 import clean_pms5003
from cleaners.purpleair import clean_purpleair
from cleaners.sensirion import clean_sensirion
from core.cache import CACHE, cache_key
from core.dataio import ingest_uploads, read_table, remove_quietly, save_upload
from core.errors import ApiError, register_error_handlers
from core.serialization import df_to_payload, json_safe, records_preview
from core.sessions import STORE
from plots.generators import PLOT_MAP, generate_plot

log = logging.getLogger("aqat")

APP_VERSION = "2.0.0"
STARTED_AT = time.time()

CLEANERS = {
    "purpleair": clean_purpleair,
    "airnote": clean_airnote,
    "airgradient": clean_airgradient,
    "clarity": clean_clarity,
    "airqo": clean_airqo,
    "airqoon": clean_airqoon,
    "airbeam": clean_airbeam,
    "sensirion": clean_sensirion,
    "atmotube": clean_atmotube,
    "pms5003": clean_pms5003,
    "generic": clean_generic,
}

DEMO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sample_data", "demo_air_quality.csv")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _active(sid: Optional[str]) -> pd.DataFrame:
    return STORE.active_frame(sid)


def _store_active(sid: str, frame: pd.DataFrame, label: str) -> None:
    session = STORE.require(sid)
    if session.get("cleaned") is not None:
        STORE.set_cleaned(sid, frame, note=label)
    else:
        STORE.set_raw(sid, frame, note=label)


def _session_summary(session: dict) -> dict:
    frame = session["cleaned"] if session.get("cleaned") is not None else session["raw"]
    last_analysis = session.get("last_analysis") or {}
    last_plot = session.get("last_plot") or {}
    return {
        "session_id": session["id"],
        "files": list(session.get("files") or []),
        "provider": session.get("provider"),
        "cleaned": session.get("cleaned") is not None,
        "shape": [int(frame.shape[0]), int(frame.shape[1])],
        # Enough metadata for the browser to rehydrate a workspace after a
        # reload without shipping the whole dataset back over the wire.
        "columns": [str(c) for c in frame.columns],
        "dtypes": {str(c): str(t) for c, t in frame.dtypes.items()},
        "preview": records_preview(frame, 20),
        "version": int(session.get("version", 0)),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "last_action": session.get("last_action"),
        "undo_available": len(session.get("history") or []) > 0,
        "undo_labels": STORE.history_labels(session["id"]),
        "last_analysis": {
            "type": last_analysis.get("type"),
            "params": last_analysis.get("params") or {},
            "at": last_analysis.get("at"),
        } if last_analysis else None,
        "last_plot": {
            "plot_type": last_plot.get("plot_type"),
            "params": last_plot.get("params") or {},
            "at": last_plot.get("at"),
        } if last_plot else None,
        "meta": session.get("meta") or {},
    }


def _analysis_response(sid: str, kind: str, runner, params: Optional[dict] = None):
    STORE.require(sid)
    frame = _active(sid)
    try:
        result = runner(frame)
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError(str(exc), 400, code=f"{kind}_invalid",
                       hint="Check that the selected columns contain enough numeric readings.") from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("%s analysis failed", kind)
        raise ApiError(f"The {kind.replace('_', ' ')} analysis could not be completed.",
                       500, code=f"{kind}_failed",
                       hint="Try a different column or reduce the date range, then run it again.",
                       details=str(exc)) from exc
    payload = json_safe(result)
    STORE.record_analysis(sid, kind, payload, params or {})
    return jsonify(payload)


def _payload_for(sid: str, frame: pd.DataFrame, **extra: Any) -> dict:
    return {"session_id": sid, **df_to_payload(frame), **extra}


def apply_column_mapping(df: pd.DataFrame, mapping) -> pd.DataFrame:
    """Rename user-selected source columns to canonical names for any provider."""
    if not isinstance(mapping, dict):
        return df
    target_names = {"pm1": "pm1.0"}
    renames = {}
    for target, source in mapping.items():
        canonical = target_names.get(target, target)
        if source in df.columns and source != canonical:
            if canonical in df.columns:
                df[canonical] = df[source]
            else:
                renames[source] = canonical
    return df.rename(columns=renames)


def preserve_categorical_columns(source_df: pd.DataFrame, cleaned_df: pd.DataFrame,
                                 options: dict) -> pd.DataFrame:
    """Carry source category labels through cleaners that resample numeric data."""
    dt_col = next((c for c in ["datetime", "timestamp", "time", "date_time",
                               "created_at", "date", "recorded_at", "obs_time"]
                   if c in source_df.columns), None)
    if dt_col is None:
        dt_col = next((c for c in source_df.columns
                       if any(token in c.lower() for token in ("time", "date"))), None)
    if dt_col is None or "datetime" not in cleaned_df.columns:
        return cleaned_df

    def is_category(series):
        dtype = series.dtype
        return (pd.api.types.is_object_dtype(dtype)
                or isinstance(dtype, pd.CategoricalDtype)
                or pd.api.types.is_bool_dtype(dtype))

    category_cols = [c for c in source_df.columns
                     if c != dt_col and c not in cleaned_df.columns
                     and is_category(source_df[c])]
    if not category_cols:
        return cleaned_df

    source = source_df[[dt_col, *category_cols]].copy()
    source["_aqat_merge_time"] = pd.to_datetime(
        source[dt_col], errors="coerce", utc=True
    ).dt.tz_convert(None)
    cleaned = cleaned_df.copy()
    cleaned["_aqat_merge_time"] = pd.to_datetime(
        cleaned["datetime"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    if options.get("resample_hourly", True):
        source["_aqat_merge_time"] = source["_aqat_merge_time"].dt.floor("h")
        cleaned["_aqat_merge_time"] = cleaned["_aqat_merge_time"].dt.floor("h")
    labels = source.dropna(subset=["_aqat_merge_time"])
    labels = labels.groupby("_aqat_merge_time", as_index=False)[category_cols].first()
    cleaned = cleaned.merge(labels, on="_aqat_merge_time", how="left")
    return cleaned.drop(columns="_aqat_merge_time")


def _ensure_datetime_and_aqi(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for candidate in ("datetime", "timestamp", "time", "Date", "date"):
        if candidate in frame.columns:
            frame["datetime"] = pd.to_datetime(frame[candidate], errors="coerce")
            break
    if "datetime" in frame.columns:
        frame = frame.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    if "pm2.5" in frame.columns:
        try:
            frame = compute_aqi(frame)
        except Exception:  # noqa: BLE001 - AQI is a convenience column
            log.warning("Could not compute AQI during ingestion", exc_info=True)
    return frame


# ──────────────────────────────────────────────────────────────────────────────
# Application factory
# ──────────────────────────────────────────────────────────────────────────────
def create_app(config: Optional[dict] = None) -> Flask:
    # AI runs in the browser through Puter.js.  The server needs no provider
    # credentials, API keys, or outbound AI network access.
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Same-origin by default.  A deployment that serves the UI and API from
    # different hosts can opt in with AQAT_ALLOWED_ORIGINS (comma separated).
    allowed_origins = [
        item.strip().rstrip("/")
        for item in os.environ.get("AQAT_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    ]
    if allowed_origins:
        CORS(
            app,
            resources={r"/api/*": {"origins": allowed_origins}},
            supports_credentials=False,
        )

    # AI usage thresholds are configuration only; no provider key or credential
    # is ever read by the server.  The browser calls the quota guard before a
    # Puter.js model request.
    configure_ai_quota()

    max_mb = int(os.environ.get("AQAT_MAX_UPLOAD_MB", "500"))
    app.config.update(
        MAX_CONTENT_LENGTH=max_mb * 1024 * 1024,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=bool(os.environ.get("AQAT_DEBUG")),
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    if config:
        app.config.update(config)

    register_error_handlers(app)
    register_blueprints(app)

    @app.before_request
    def _assign_request_id():
        g.aqat_request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.aqat_request_id = g.aqat_request_id

    @app.after_request
    def _harden_and_compress(response):
        response.headers.setdefault("X-Request-ID", getattr(g, "aqat_request_id", ""))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        # OSM tile servers require a Referer on cross-origin requests. The
        # same-origin policy suppresses it for map tiles and triggers HTTP 403.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), microphone=(), camera=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https:; "
            "style-src 'self' 'unsafe-inline' https:; img-src 'self' data: blob: https:; "
            "connect-src 'self' https://*.puter.com https:; font-src 'self' data:; "
            "frame-src 'self' https://*.puter.com https://puter.com; "
            "worker-src 'self' blob:; frame-ancestors 'self'",
        )
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            response.headers.setdefault("Vary", "Accept-Encoding")

        try:
            accepts_gzip = "gzip" in (request.headers.get("Accept-Encoding") or "").lower()
            compressible = (response.mimetype or "").startswith(("text/", "application/json",
                                                                  "application/javascript",
                                                                  "image/svg"))
            if (accepts_gzip and compressible and not response.direct_passthrough
                    and response.status_code < 300 and response.content_length
                    and response.content_length > 1400
                    and not response.headers.get("Content-Encoding")):
                response.set_data(gzip.compress(response.get_data(), compresslevel=5))
                response.headers["Content-Encoding"] = "gzip"
                response.headers["Content-Length"] = str(len(response.get_data()))
        except Exception:  # noqa: BLE001 - compression must never break a response
            log.debug("gzip skipped", exc_info=True)
        return response

    # ── Pages ──────────────────────────────────────────────────────────────
    @app.get("/")
    def index():
        return render_template("index.html", app_version=APP_VERSION)

    @app.get("/api/health")
    def health():
        cache_stats = CACHE.stats()
        ai_status = assistant_status()
        return jsonify({
            "ok": True,
            "app": "AQACT",
            "version": APP_VERSION,
            # Analytics, charts, and exports run locally.  The browser AI
            # layer is optional and needs no server-side network access.
            "offline": True,
            "network_required": False,
            "assistant": ai_status,
            "uptime_seconds": round(time.time() - STARTED_AT, 1),
            "sessions": STORE.stats(),
            "cache": cache_stats,
            "plot_types": sorted(PLOT_MAP),
            "time_utc": datetime.now(timezone.utc).isoformat(),
        })

    # ── Upload: single / merged files ──────────────────────────────────────
    @app.post("/api/upload")
    def upload_files():
        files = request.files.getlist("files")
        if not files:
            raise ApiError("No files were provided.", 400, code="no_files",
                           hint="Select at least one CSV or Excel file.")
        frames, names, warnings = ingest_uploads(files, uuid.uuid4().hex[:8])
        if not frames:
            raise ApiError("None of the uploaded files could be read.", 400,
                           code="no_valid_files",
                           hint="Check the file format and try again.",
                           details="; ".join(warnings[-4:]) or None)
        combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        session = STORE.create(combined, files=names, warnings=warnings)
        return jsonify(_payload_for(session["id"], combined, files=names, warnings=warnings))

    # ── Upload: batch stack from one station ───────────────────────────────
    @app.post("/api/batch_upload")
    def batch_upload():
        files = request.files.getlist("files")
        provider = (request.form.get("provider") or "").lower()
        station_name = (request.form.get("station_name") or "").strip()
        if not files:
            raise ApiError("No files were provided.", 400, code="no_files",
                           hint="Select the daily files from the deployment.")

        frames, names, warnings = ingest_uploads(files, uuid.uuid4().hex[:8])
        if not frames:
            raise ApiError("None of the uploaded files could be read.", 400,
                           code="no_valid_files", hint="Check the file format and try again.",
                           details="; ".join(warnings[-4:]) or None)

        for frame, name in zip(frames, names):
            frame["_source_file"] = name
        combined = pd.concat(frames, ignore_index=True)

        dt_col = next((c for c in ["timestamp", "datetime", "time", "date", "UTCDateTime",
                                   "captured_at", "created_at", "Timestamp", "Time", "Date"]
                       if c in combined.columns), None)
        if dt_col is None:
            dt_col = next((c for c in combined.columns
                           if any(k in c.lower() for k in ("time", "date"))), None)
        if dt_col:
            combined[dt_col] = pd.to_datetime(combined[dt_col], errors="coerce")
            combined = combined.sort_values(dt_col).reset_index(drop=True)
            combined = combined.drop_duplicates(subset=[dt_col], keep="first")

        if station_name:
            combined["location"] = station_name

        session = STORE.create(combined, files=names, provider=provider or None,
                               batch_mode=True, station_name=station_name,
                               warnings=warnings)
        payload = _payload_for(session["id"], combined, files=names,
                               stacked_count=len(frames), failed_files=[],
                               warnings=warnings)
        payload["station_name"] = station_name
        payload["provider"] = provider
        return jsonify(payload)

    # ── Upload: multi-station ──────────────────────────────────────────────
    @app.post("/api/multistation_upload")
    def multistation_upload():
        files = request.files.getlist("files")
        labels = request.form.getlist("station_labels")
        provider = (request.form.get("provider") or "generic").lower()
        if not files:
            raise ApiError("No files were provided.", 400, code="no_files",
                           hint="Select at least one file per station.")

        labels = list(labels[:len(files)]) + [""] * max(0, len(files) - len(labels))

        station_map: dict[str, list[pd.DataFrame]] = {}
        file_names: list[str] = []
        warnings: list[str] = []
        prefix = uuid.uuid4().hex[:8]
        for file, label in zip(files, labels):
            if not file or not file.filename:
                continue
            path, safe = save_upload(file, prefix)
            try:
                frame = read_table(path, safe)
                station_label = str(label or "").strip() or os.path.splitext(safe)[0]
                station_map.setdefault(station_label, []).append(frame)
                file_names.append(safe)
            except ApiError as exc:
                warnings.append(f"{safe}: {exc.message}")
            finally:
                remove_quietly(path)

        if not station_map:
            raise ApiError("None of the uploaded station files could be read.", 400,
                           code="no_valid_station_files",
                           hint="Check the file format and station labels.",
                           details="; ".join(warnings[-4:]) or None)

        stations = {
            label: (pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0])
            for label, frames in station_map.items()
        }
        batch = STORE.create_batch(stations, provider=provider, file_names=file_names)
        return jsonify({
            "batch_id": batch["id"],
            "station_count": len(stations),
            "station_names": list(stations.keys()),
            "file_names": file_names,
            "provider": provider,
            "warnings": warnings,
            "columns_by_station": {name: [str(c) for c in df.columns]
                                   for name, df in stations.items()},
        })

    # ── Multi-station cleaning ─────────────────────────────────────────────
    @app.post("/api/multistation_clean")
    def multistation_clean():
        body = request.get_json(silent=True) or {}
        batch = STORE.require_batch(body.get("batch_id"))
        provider = (body.get("provider") or "").lower()
        options = body.get("options", {}) or {}
        if provider not in CLEANERS:
            raise ApiError(f"Unknown provider '{provider}'.", 400, code="unknown_provider",
                           hint="Choose one of: " + ", ".join(sorted(CLEANERS)) + ".")

        cleaned_stations: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for name, raw_df in batch["stations_raw"].items():
            try:
                station_opts = {**options, "station_name": name}
                mapped = apply_column_mapping(raw_df.copy(), options.get("column_mapping", {}))
                cleaned = CLEANERS[provider](mapped, station_opts)
                cleaned = preserve_categorical_columns(mapped, cleaned, options)
                if "pm2.5" in cleaned.columns:
                    cleaned = compute_aqi(cleaned)
                cleaned["_station"] = name
                cleaned["location"] = name
                cleaned_stations[name] = cleaned
            except Exception as exc:  # noqa: BLE001
                log.exception("Station %s failed to clean", name)
                errors[name] = str(exc)

        if not cleaned_stations:
            raise ApiError("No station could be cleaned.", 400, code="station_clean_failed",
                           hint="Check the selected provider and column mapping.",
                           details=errors)
        STORE.set_batch_cleaned(batch["id"], cleaned_stations, provider)
        return jsonify({
            "status": "ok",
            "batch_id": batch["id"],
            "cleaned_stations": list(cleaned_stations.keys()),
            "errors": errors,
            "previews": {name: df_to_payload(df) for name, df in cleaned_stations.items()},
        })

    @app.post("/api/comparative")
    def comparative_analysis():
        body = request.get_json(silent=True) or {}
        batch = STORE.require_batch(body.get("batch_id"))
        column = body.get("column", "pm2.5")
        params = body.get("params", {}) or {}
        stations = batch.get("stations_cleaned") or batch.get("stations_raw")
        if not stations:
            raise ApiError("No station data is available.", 400, code="no_station_data",
                           hint="Upload and clean station files first.")
        try:
            result = run_comparative_analysis(stations, column, params)
        except ValueError as exc:
            raise ApiError(str(exc), 400, code="comparative_invalid",
                           hint="Choose a numeric column that exists in the station datasets.") from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("Comparative analysis failed")
            raise ApiError("The comparative analysis could not be completed.", 500,
                           code="comparative_failed",
                           hint="Check that each station has enough overlapping readings.",
                           details=str(exc)) from exc
        payload = json_safe(result)
        STORE.record_batch_analysis(batch["id"], "comparative", payload, {"column": column, **params})
        return jsonify(payload)

    @app.post("/api/multistation_export")
    def multistation_export():
        body = request.get_json(silent=True) or {}
        batch = STORE.require_batch(body.get("batch_id"))
        fmt = body.get("format", "csv")
        stations = batch.get("stations_cleaned") or batch.get("stations_raw")
        if not stations:
            raise ApiError("No data is available to export.", 400, code="no_data",
                           hint="Upload station files first.")
        dfs = []
        for name, df in stations.items():
            frame = df.copy()
            if "location" not in frame.columns or frame["location"].isna().all():
                frame["location"] = name
            dfs.append(frame)
        merged = pd.concat(dfs, ignore_index=True)
        if "datetime" in merged.columns:
            merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce")
            merged = merged.sort_values(["datetime", "location"]).reset_index(drop=True)
        buf = io.BytesIO()
        if fmt == "csv":
            merged.to_csv(buf, index=False)
            buf.seek(0)
            return send_file(buf, mimetype="text/csv", as_attachment=True,
                             download_name="multistation_data.csv")
        if fmt == "xlsx":
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                for name, df in stations.items():
                    df.to_excel(writer, sheet_name=str(name)[:31], index=False)
            buf.seek(0)
            return send_file(
                buf,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True, download_name="multistation_data.xlsx",
            )
        raise ApiError(f"Unsupported export format '{fmt}'.", 400,
                       code="unsupported_export_format", hint="Choose CSV or XLSX.")

    @app.post("/api/multistation_merge")
    def multistation_merge():
        body = request.get_json(silent=True) or {}
        batch = STORE.require_batch(body.get("batch_id"))
        stations = batch.get("stations_cleaned")
        if not stations:
            raise ApiError("Clean all stations before loading merged data.", 400,
                           code="stations_not_cleaned",
                           hint="Run 'Clean all stations', then try again.")
        frames = []
        for name, df in stations.items():
            frame = df.copy()
            if "location" not in frame.columns or frame["location"].isna().all():
                frame["location"] = name
            frames.append(frame)
        merged = pd.concat(frames, ignore_index=True)
        if "datetime" in merged.columns:
            merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce")
            merged = merged.sort_values(["datetime", "location"]).reset_index(drop=True)
        session = STORE.create(merged, files=batch.get("file_names", []),
                               provider=batch.get("provider"), batch_mode=True,
                               merged_from=batch["id"])
        STORE.set_cleaned(session["id"], merged, note="multistation merge")
        return jsonify(_payload_for(session["id"], merged))

    # ── Single-session cleaning ────────────────────────────────────────────
    @app.post("/api/clean")
    def clean_data():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        provider = (body.get("provider") or "").lower()
        options = dict(body.get("options", {}) or {})
        session = STORE.require(sid)
        if provider not in CLEANERS:
            raise ApiError(f"Unknown provider '{provider}'.", 400, code="unknown_provider",
                           hint="Choose one of: " + ", ".join(sorted(CLEANERS)) + ".")
        options["location"] = str(options.get("location") or "").strip()
        options["station_name"] = str(options.get("station_name") or "").strip()
        try:
            if session.get("cleaned") is not None:
                STORE.snapshot(sid, "clean")
            raw = apply_column_mapping(session["raw"].copy(), options.get("column_mapping", {}))
            cleaned = CLEANERS[provider](raw, options)
            cleaned = preserve_categorical_columns(raw, cleaned, options)
            # Keep the confirmed site and station identity as separate fields.
            if options["location"]:
                cleaned["location"] = options["location"]
            if options["station_name"]:
                cleaned["station_name"] = options["station_name"]
            if "pm2.5" in cleaned.columns:
                cleaned = compute_aqi(cleaned)
            removed_columns = []
            if options.get("drop_empty_columns"):
                empty_columns = [column for column in cleaned.columns
                                 if cleaned[column].isna().all()]
                removed_columns = [str(column) for column in empty_columns]
                if empty_columns:
                    STORE.set_cleaned(sid, cleaned, provider=provider, note="clean")
                    STORE.snapshot(sid, "drop-empty-columns-after-clean")
                    cleaned = cleaned.drop(columns=empty_columns)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Cleaning failed")
            raise ApiError("The dataset could not be cleaned with this provider.", 500,
                           code="clean_failed",
                           hint="Try the Generic provider, or check the column mapping.",
                           details=str(exc)) from exc
        STORE.set_cleaned(sid, cleaned, provider=provider, note="clean")
        return jsonify({"status": "ok", "removed_columns": removed_columns,
                        **_payload_for(sid, cleaned)})

    # ── Column operations ──────────────────────────────────────────────────
    @app.post("/api/columns/rename")
    def rename_column():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        old = body.get("old_name")
        new = str(body.get("new_name") or "").strip()
        STORE.require(sid)
        frame = _active(sid).copy()
        if old not in frame.columns:
            raise ApiError(f"Column '{old}' was not found.", 400, code="column_not_found",
                           hint="Refresh the column list and try again.")
        if not new:
            raise ApiError("Enter a new column name.", 400, code="empty_column_name")
        if new in frame.columns and new != old:
            raise ApiError(f"A column named '{new}' already exists.", 400, code="duplicate_column",
                           hint="Choose a different name.")
        STORE.snapshot(sid, f"rename:{old}")
        frame.rename(columns={old: new}, inplace=True)
        _store_active(sid, frame, "rename-column")
        return jsonify({"status": "ok", **_payload_for(sid, frame)})

    @app.post("/api/columns/delete")
    def delete_column():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        col = body.get("column")
        STORE.require(sid)
        frame = _active(sid).copy()
        if col not in frame.columns:
            raise ApiError(f"Column '{col}' was not found.", 400, code="column_not_found")
        STORE.snapshot(sid, f"delete:{col}")
        frame.drop(columns=[col], inplace=True)
        _store_active(sid, frame, "delete-column")
        return jsonify({"status": "ok", **_payload_for(sid, frame)})

    @app.post("/api/columns/drop_empty")
    def drop_empty_columns():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        STORE.require(sid)
        frame = _active(sid).copy()
        empty_columns = [c for c in frame.columns if frame[c].isna().all()]
        if not empty_columns:
            return jsonify({"status": "ok", "removed_columns": [], **_payload_for(sid, frame)})
        removed_columns = [str(column) for column in empty_columns]
        STORE.snapshot(sid, "drop-fully-missing-columns")
        frame.drop(columns=empty_columns, inplace=True)
        _store_active(sid, frame, "drop-empty-columns")
        return jsonify({"status": "ok", "removed_columns": removed_columns,
                        **_payload_for(sid, frame)})

    @app.post("/api/columns/add")
    def add_column():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        name = str(body.get("column") or "").strip()
        expression = str(body.get("expression") or "").strip()
        STORE.require(sid)
        frame = _active(sid).copy()
        if not name:
            raise ApiError("Enter a name for the new column.", 400, code="empty_column_name")
        if not expression:
            raise ApiError("Enter an expression for the new column.", 400, code="empty_expression",
                           hint="For example: pm2.5 / pm10")
        STORE.snapshot(sid, f"add:{name}")
        try:
            frame[name] = frame.eval(expression)
        except Exception as exc:  # noqa: BLE001
            raise ApiError("The expression could not be evaluated.", 400,
                           code="bad_expression",
                           hint="Use column names and arithmetic operators, e.g. pm2.5 * 2.",
                           details=str(exc)) from exc
        _store_active(sid, frame, "add-column")
        return jsonify({"status": "ok", **_payload_for(sid, frame)})

    # ── Aggregation / missing data / outliers ──────────────────────────────
    @app.post("/api/aggregate")
    def aggregate():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        freq = str(body.get("freq") or "h")
        freq_map = {"H": "h", "M": "ME", "A": "YE", "Y": "YE", "Q": "QE"}
        freq = freq_map.get(freq.upper(), freq)
        agg_func = body.get("agg_func", "mean")
        if agg_func not in {"mean", "median", "sum", "min", "max"}:
            raise ApiError(f"Unsupported aggregation '{agg_func}'.", 400,
                           code="bad_aggregation", hint="Use mean, median, sum, min or max.")
        frame = _active(sid).copy()
        if "datetime" not in frame.columns:
            raise ApiError("No 'datetime' column was found.", 400, code="missing_datetime",
                           hint="Clean the data first so a datetime column is created.")
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        numeric = frame.set_index("datetime").select_dtypes(include="number")
        try:
            aggregated = getattr(numeric.resample(freq), agg_func)().reset_index()
        except Exception as exc:  # noqa: BLE001
            raise ApiError("The dataset could not be aggregated.", 400,
                           code="aggregation_failed", details=str(exc)) from exc
        STORE.snapshot(sid, f"aggregate:{freq}:{agg_func}")
        _store_active(sid, aggregated, "aggregate")
        return jsonify({"status": "ok", **_payload_for(sid, aggregated)})

    @app.post("/api/interpolate")
    def interpolate():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        method = body.get("method", "linear")
        limit = body.get("limit")
        frame = _active(sid).copy()
        num_cols = frame.select_dtypes(include="number").columns.tolist()
        if not num_cols:
            raise ApiError("There are no numeric columns to fill.", 400, code="no_numeric_columns")
        try:
            if method == "ffill":
                frame[num_cols] = frame[num_cols].ffill(limit=limit)
            elif method == "bfill":
                frame[num_cols] = frame[num_cols].bfill(limit=limit)
            else:
                frame[num_cols] = frame[num_cols].interpolate(
                    method=method, limit=limit, limit_direction="both")
        except Exception as exc:  # noqa: BLE001
            raise ApiError("Missing values could not be filled.", 400,
                           code="interpolate_failed", details=str(exc)) from exc
        STORE.snapshot(sid, f"interpolate:{method}")
        _store_active(sid, frame, "interpolate")
        return jsonify({"status": "ok", **_payload_for(sid, frame)})

    @app.post("/api/remove_outliers")
    def remove_outliers():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        column = body.get("column")
        method = body.get("method", "iqr")
        try:
            thresh = float(body.get("threshold", 3.0))
        except (TypeError, ValueError) as exc:
            raise ApiError("The outlier threshold must be a number.", 400,
                           code="bad_threshold") from exc
        frame = _active(sid).copy()
        if column not in frame.columns:
            raise ApiError(f"Column '{column}' was not found.", 400, code="column_not_found")
        series = pd.to_numeric(frame[column], errors="coerce")
        if method == "iqr":
            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            iqr = q3 - q1
            mask = (series >= q1 - thresh * iqr) & (series <= q3 + thresh * iqr)
        elif method == "zscore":
            std = series.std()
            z = (series - series.mean()) / (std if std else 1)
            mask = z.abs() <= thresh
        else:
            lo, hi = series.quantile(thresh / 100), series.quantile(1 - thresh / 100)
            mask = (series >= lo) & (series <= hi)
        mask = mask.fillna(True)
        removed = int((~mask).sum())
        filtered = frame[mask].reset_index(drop=True)
        STORE.snapshot(sid, f"outliers:{column}:{method}")
        _store_active(sid, filtered, "remove-outliers")
        return jsonify({"status": "ok", "removed_rows": removed, **_payload_for(sid, filtered)})

    # ── Statistics, profile and insights ───────────────────────────────────
    @app.post("/api/stats")
    def stats():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        session = STORE.require(sid)
        version = session.get("version", 0)
        key = cache_key("stats", sid, version)
        def build():
            frame = _active(sid)
            desc = frame.describe(include="all").replace({np.nan: None})
            missing = frame.isnull().sum().to_dict()
            return {
                "describe": json_safe(desc.to_dict()),
                "missing": {str(k): int(v) for k, v in missing.items()},
                "missing_pct": {str(k): round(float(v) * 100 / max(1, len(frame)), 2)
                                for k, v in missing.items()},
                "shape": [int(frame.shape[0]), int(frame.shape[1])],
            }
        return jsonify(CACHE.get_or_set(key, build))

    @app.route("/api/profile", methods=["GET", "POST"])
    def profile():
        payload = request.get_json(silent=True) or {}
        sid = payload.get("session_id") or request.args.get("session_id")
        session = STORE.require(sid)
        key = cache_key("profile", sid, session.get("version", 0))
        body = CACHE.get_or_set(key, lambda: dataset_profile(_active(sid)))
        return jsonify(json_safe(body))

    @app.route("/api/insights", methods=["GET", "POST"])
    def insights():
        payload = request.get_json(silent=True) or {}
        sid = payload.get("session_id") or request.args.get("session_id")
        session = STORE.require(sid)
        key = cache_key("insights", sid, session.get("version", 0))
        body = CACHE.get_or_set(
            key,
            lambda: dataset_insights(_active(sid),
                                     profile=CACHE.get_or_set(
                                         cache_key("profile", sid, session.get("version", 0)),
                                         lambda: dataset_profile(_active(sid)))),
        )
        return jsonify(json_safe({"insights": body, "shape": list(_active(sid).shape)}))

    # ── Plots ──────────────────────────────────────────────────────────────
    @app.post("/api/plot")
    def plot():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        plot_type = body.get("plot_type")
        params = body.get("params", {}) or {}
        session = STORE.require(sid)
        if plot_type not in PLOT_MAP:
            raise ApiError(f"Unknown plot type '{plot_type}'.", 400, code="unknown_plot",
                           hint="Choose one of: " + ", ".join(sorted(PLOT_MAP)) + ".")
        key = cache_key("plot", sid, session.get("version", 0), plot_type, params)
        def build():
            return generate_plot(_active(sid), plot_type, params)
        try:
            fig_json = CACHE.get_or_set(key, build)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Plot generation failed")
            raise ApiError(f"The '{plot_type}' chart could not be generated.", 500,
                           code="plot_failed",
                           hint="Choose a different column or plot type.",
                           details=str(exc)) from exc
        title = params.get("title")
        try:
            figure = json.loads(fig_json)
            layout = figure.get("layout") or {}
            title = title or (layout.get("title") or {}).get("text")
        except (TypeError, ValueError):
            pass
        meta = {
            "plot_type": plot_type,
            "title": title,
            "column": params.get("column"),
            "columns": params.get("columns") or [],
            "params": params,
        }
        STORE.record_plot(sid, plot_type, params, meta)
        return jsonify({"plot": fig_json, "meta": meta})

    # ── Map data ───────────────────────────────────────────────────────────
    @app.post("/api/map_data")
    def map_data():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        frame = _active(sid)
        if not {"lat", "lon"}.issubset(frame.columns):
            raise ApiError("The dataset has no 'lat' and 'lon' columns.", 400,
                           code="missing_coordinates",
                           hint="Use a sensor file that includes GPS coordinates, or add lat/lon columns.")
        cols = ["lat", "lon"]
        for extra in ["pm2.5", "pm10", "CO2", "O3", "NO2", "temperature", "RH",
                      "location", "datetime", "AQI"]:
            if extra in frame.columns:
                cols.append(extra)
        sub = frame[cols].dropna(subset=["lat", "lon"]).copy()
        return jsonify({"data": json_safe(sub.to_dict(orient="records")),
                        "count": int(len(sub))})

    # ── Scientific analyses ────────────────────────────────────────────────
    @app.post("/api/extreme")
    def extreme():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        column = body.get("column", "pm2.5")
        params = body.get("params", {}) or {}
        return _analysis_response(
            sid, "extreme",
            lambda df: run_extreme_analysis(df, column, params),
            {"column": column, **params},
        )

    @app.post("/api/compliance")
    def compliance():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        column = body.get("column", "pm2.5")
        params = body.get("params", {}) or {}
        return _analysis_response(
            sid, "compliance",
            lambda df: run_compliance_analysis(df, column, params),
            {"column": column, **params},
        )

    @app.post("/api/machine_learning")
    def machine_learning():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        target = body.get("target", "pm2.5")
        model = body.get("model", "trend")
        params = body.get("params", {}) or {}
        return _analysis_response(
            sid, "machine_learning",
            lambda df: run_machine_learning(df, target, model, params),
            {"target": target, "model": model, **params},
        )

    @app.post("/api/model_eval")
    def model_eval():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        obs_col = body.get("obs_col")
        mod_col = body.get("mod_col")
        params = body.get("params", {}) or {}
        return _analysis_response(
            sid, "model_eval",
            lambda df: run_model_evaluation(df, obs_col, mod_col, params),
            {"obs_col": obs_col, "mod_col": mod_col, **params},
        )

    # ── Session utilities ──────────────────────────────────────────────────
    @app.post("/api/reset")
    def reset_application_state():
        STORE.clear()
        CACHE.clear()
        return jsonify({"status": "ok"})

    @app.get("/api/session")
    def session_info():
        sid = request.args.get("session_id")
        session = STORE.require(sid)
        return jsonify(_session_summary(session))

    @app.post("/api/undo")
    def undo():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        STORE.require(sid)
        if not STORE.history_labels(sid):
            raise ApiError("There is nothing to undo.", 400, code="nothing_to_undo",
                           hint="Undo is available after renaming, deleting, aggregating or filtering.")
        frame = STORE.undo(sid)
        if frame is None:
            raise ApiError("The previous version could not be restored.", 500, code="undo_failed")
        return jsonify({"status": "ok", **_payload_for(sid, frame),
                        "undo_labels": STORE.history_labels(sid)})

    @app.route("/api/demo", methods=["GET", "POST"])
    def demo():
        if not os.path.exists(DEMO_PATH):
            raise ApiError("The demo dataset is not installed.", 404, code="demo_missing",
                           hint="Run 'python sample_data/make_demo.py' to generate it.")
        try:
            frame = pd.read_csv(DEMO_PATH)
        except Exception as exc:  # noqa: BLE001
            raise ApiError("The demo dataset could not be loaded.", 500,
                           code="demo_read_error", details=str(exc)) from exc
        frame = _ensure_datetime_and_aqi(frame)
        session = STORE.create(frame, files=["demo_air_quality.csv"],
                               provider="demo", demo=True,
                               source="Synthetic teaching dataset")
        STORE.set_cleaned(session["id"], frame, provider="demo", note="demo")
        return jsonify(_payload_for(session["id"], frame,
                                    demo=True,
                                    session=_session_summary(STORE.require(session["id"]))))

    # ── Data export ────────────────────────────────────────────────────────
    @app.post("/api/export")
    def export_data():
        body = request.get_json(silent=True) or {}
        sid = body.get("session_id")
        fmt = str(body.get("format", "csv")).lower()
        frame = _active(sid)
        buf = io.BytesIO()
        if fmt == "csv":
            frame.to_csv(buf, index=False)
            buf.seek(0)
            return send_file(buf, mimetype="text/csv", as_attachment=True,
                             download_name="cleaned_data.csv")
        if fmt == "xlsx":
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                frame.to_excel(writer, index=False)
            buf.seek(0)
            return send_file(
                buf,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True, download_name="cleaned_data.xlsx",
            )
        raise ApiError(f"Unsupported export format '{fmt}'.", 400,
                       code="unsupported_export_format", hint="Choose CSV or XLSX.")

    return app


# WSGI entry point used by ``run.py`` and production servers.
app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
