"""
AQACT - Air Quality Analysis & Compliance Assessment Tool
Upgrades: batch file processing, multi-station comparative analysis, expanded sensor types
"""
import os, io, json, glob, traceback
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

from cleaners.purpleair   import clean_purpleair
from cleaners.airnote     import clean_airnote
from cleaners.airgradient import clean_airgradient
from cleaners.clarity     import clean_clarity
from cleaners.airqo       import clean_airqo
from cleaners.airqoon     import clean_airqoon
from cleaners.airbeam     import clean_airbeam
from cleaners.sensirion   import clean_sensirion
from cleaners.atmotube    import clean_atmotube
from cleaners.pms5003     import clean_pms5003
from cleaners.generic     import clean_generic
from analysis.aqi         import compute_aqi
from analysis.extreme     import run_extreme_analysis
from analysis.compliance  import run_compliance_analysis
from analysis.machine_learning import run_machine_learning
from analysis.model_eval  import run_model_evaluation
from analysis.comparative import run_comparative_analysis
from plots.generators     import generate_plot

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "/tmp/aqtool_uploads"
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "txt"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB for batch uploads

# In-memory session store
SESSIONS = {}
# Multi-station store: { batch_id: { station_name: df, ... } }
BATCH_SESSIONS = {}


@app.route("/")
def index():
    return render_template("index.html")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def df_to_json(df: pd.DataFrame) -> dict:
    """Serialize dataframe metadata + first 200 rows for preview."""
    df2 = df.copy()
    for col in df2.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns:
        df2[col] = df2[col].astype(str)
    preview = df2.head(200).replace({np.nan: None}).to_dict(orient="records")
    return {
        "columns": list(df2.columns),
        "dtypes": {c: str(t) for c, t in df2.dtypes.items()},
        "shape": list(df2.shape),
        "preview": preview,
    }


def get_session(sid):
    if sid not in SESSIONS:
        return None, jsonify({"error": "Session not found. Please upload data first."}), 404
    return SESSIONS[sid], None, None


# ──────────────────────────────────────────────────────────────────────────────
# SINGLE FILE UPLOAD & INGEST
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
def upload_files():
    """Upload one or more files; return session_id and raw column preview."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    session_id = os.urandom(8).hex()
    raw_frames = []
    file_names = []

    for f in files:
        if f and allowed_file(f.filename):
            fname = secure_filename(f.filename)
            path = os.path.join(UPLOAD_FOLDER, f"{session_id}_{fname}")
            f.save(path)
            ext = fname.rsplit(".", 1)[1].lower()
            try:
                if ext == "csv":
                    df = pd.read_csv(path, low_memory=False)
                elif ext in ("xlsx", "xls"):
                    df = pd.read_excel(path)
                else:
                    df = pd.read_csv(path, low_memory=False)
                raw_frames.append(df)
                file_names.append(fname)
            except Exception as e:
                return jsonify({"error": f"Failed to read {fname}: {str(e)}"}), 400

    if not raw_frames:
        return jsonify({"error": "No valid files uploaded"}), 400

    combined = pd.concat(raw_frames, ignore_index=True) if len(raw_frames) > 1 else raw_frames[0]
    SESSIONS[session_id] = {
        "raw": combined.copy(),
        "cleaned": None,
        "files": file_names,
        "provider": None,
    }
    return jsonify({"session_id": session_id, "files": file_names, **df_to_json(combined)})


# ──────────────────────────────────────────────────────────────────────────────
# BATCH FILE UPLOAD (stacks daily files from same sensor/station)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/batch_upload", methods=["POST"])
def batch_upload():
    """
    Upload multiple daily files from the same sensor (e.g. PurpleAir SD card, AirBeam).
    Files are stacked into a single time-series using glob-style ordering.
    Returns a regular session_id with the combined dataset.
    """
    files = request.files.getlist("files")
    provider = (request.form.get("provider") or "").lower()
    station_name = request.form.get("station_name", "Station")

    if not files:
        return jsonify({"error": "No files provided"}), 400

    session_id = os.urandom(8).hex()
    saved_paths = []
    file_names = []

    for f in files:
        if f and allowed_file(f.filename):
            fname = secure_filename(f.filename)
            path = os.path.join(UPLOAD_FOLDER, f"{session_id}_{fname}")
            f.save(path)
            saved_paths.append(path)
            file_names.append(fname)

    if not saved_paths:
        return jsonify({"error": "No valid files uploaded"}), 400

    # Sort paths so daily files stack chronologically
    saved_paths.sort()

    frames = []
    failed = []
    for path in saved_paths:
        fname = os.path.basename(path)
        ext = fname.rsplit(".", 1)[1].lower()
        try:
            if ext == "csv":
                df = pd.read_csv(path, low_memory=False)
            elif ext in ("xlsx", "xls"):
                df = pd.read_excel(path)
            else:
                df = pd.read_csv(path, low_memory=False)
            df["_source_file"] = fname
            frames.append(df)
        except Exception as e:
            failed.append(f"{fname}: {str(e)}")

    if not frames:
        return jsonify({"error": f"Could not read any files. Errors: {'; '.join(failed)}"}), 400

    combined = pd.concat(frames, ignore_index=True)

    # ── Detect and resolve the datetime column so we can sort & dedup ─────────
    dt_candidates = ["timestamp","datetime","time","date","UTCDateTime",
                     "captured_at","created_at","Timestamp","Time","Date"]
    _dt_col = None
    for _c in dt_candidates:
        if _c in combined.columns:
            _dt_col = _c; break
    if _dt_col is None:
        # fallback: first column whose name contains "time" or "date"
        for _c in combined.columns:
            if any(k in _c.lower() for k in ("time","date")):
                _dt_col = _c; break

    if _dt_col:
        combined[_dt_col] = pd.to_datetime(combined[_dt_col], errors="coerce")
        # Sort chronologically (key requirement for same-station daily stacking)
        combined = combined.sort_values(_dt_col).reset_index(drop=True)
        # Drop exact duplicate timestamps (same station, same moment)
        combined = combined.drop_duplicates(subset=[_dt_col], keep="first")

    # Stamp the station name into the location column so downstream
    # analysis can distinguish datasets even after stacking
    if station_name:
        combined["location"] = station_name

    SESSIONS[session_id] = {
        "raw": combined.copy(),
        "cleaned": None,
        "files": file_names,
        "provider": provider,
        "batch_mode": True,
        "station_name": station_name,
        "failed_files": failed,
    }

    result = df_to_json(combined)
    result["session_id"] = session_id
    result["files"] = file_names
    result["stacked_count"] = len(frames)
    result["failed_files"] = failed
    if failed:
        result["warning"] = f"{len(failed)} file(s) could not be read."
    return jsonify(result)


# ──────────────────────────────────────────────────────────────────────────────
# MULTI-STATION BATCH UPLOAD (for comparative analysis)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/multistation_upload", methods=["POST"])
def multistation_upload():
    """
    Upload files from multiple stations/locations for comparative analysis.
    Each file (or group of files with the same station label) becomes one station.
    Form fields: files[], station_labels[] (parallel arrays), provider
    """
    files = request.files.getlist("files")
    labels = request.form.getlist("station_labels")
    provider = (request.form.get("provider") or "generic").lower()

    if not files:
        return jsonify({"error": "No files provided"}), 400

    # If labels not supplied, use filenames
    if not labels or len(labels) != len(files):
        labels = [secure_filename(f.filename).rsplit(".", 1)[0] for f in files]

    batch_id = os.urandom(8).hex()
    station_map = {}  # label -> list of DataFrames
    file_names = []

    for f, label in zip(files, labels):
        if f and allowed_file(f.filename):
            fname = secure_filename(f.filename)
            path = os.path.join(UPLOAD_FOLDER, f"{batch_id}_{fname}")
            f.save(path)
            ext = fname.rsplit(".", 1)[1].lower()
            try:
                if ext == "csv":
                    df = pd.read_csv(path, low_memory=False)
                elif ext in ("xlsx", "xls"):
                    df = pd.read_excel(path)
                else:
                    df = pd.read_csv(path, low_memory=False)
                if label not in station_map:
                    station_map[label] = []
                station_map[label].append(df)
                file_names.append(fname)
            except Exception as e:
                pass

    if not station_map:
        return jsonify({"error": "No valid files uploaded"}), 400

    # Concatenate per-station frames
    stations = {}
    for label, frames in station_map.items():
        stations[label] = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    BATCH_SESSIONS[batch_id] = {
        "stations_raw": stations,
        "stations_cleaned": {},
        "provider": provider,
        "file_names": file_names,
    }

    return jsonify({
        "batch_id": batch_id,
        "station_count": len(stations),
        "station_names": list(stations.keys()),
        "file_names": file_names,
        "provider": provider,
        "columns_by_station": {name: list(df.columns) for name, df in stations.items()},
    })


# ──────────────────────────────────────────────────────────────────────────────
# CLEAN MULTI-STATION BATCH
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/multistation_clean", methods=["POST"])
def multistation_clean():
    """Clean all stations in a batch session using the same provider."""
    body = request.json or {}
    batch_id = body.get("batch_id")
    provider = (body.get("provider") or "").lower()
    options = body.get("options", {})

    if batch_id not in BATCH_SESSIONS:
        return jsonify({"error": "Batch session not found."}), 404

    if provider not in CLEANERS:
        return jsonify({"error": f"Unknown provider '{provider}'"}), 400

    batch = BATCH_SESSIONS[batch_id]
    cleaned_stations = {}
    errors = {}

    for name, raw_df in batch["stations_raw"].items():
        try:
            # Each station's options carry its assigned name so cleaners
            # stamp the location column correctly
            station_opts = {**options, "station_name": name}
            mapped_raw = apply_column_mapping(
                raw_df.copy(), options.get("column_mapping", {})
            )
            cleaned = CLEANERS[provider](mapped_raw, station_opts)
            cleaned = preserve_categorical_columns(mapped_raw, cleaned, options)
            if "pm2.5" in cleaned.columns:
                cleaned = compute_aqi(cleaned)
            # Always stamp _station and override location with the assigned name
            cleaned["_station"] = name
            cleaned["location"] = name
            cleaned_stations[name] = cleaned
        except Exception as e:
            errors[name] = str(e)

    BATCH_SESSIONS[batch_id]["stations_cleaned"] = cleaned_stations
    BATCH_SESSIONS[batch_id]["provider"] = provider

    previews = {}
    for name, df in cleaned_stations.items():
        previews[name] = df_to_json(df)

    return jsonify({
        "status": "ok",
        "batch_id": batch_id,
        "cleaned_stations": list(cleaned_stations.keys()),
        "errors": errors,
        "previews": previews,
    })


# ──────────────────────────────────────────────────────────────────────────────
# COMPARATIVE ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/comparative", methods=["POST"])
def comparative_analysis():
    """Run statistical comparative analysis across multiple stations."""
    body = request.json or {}
    batch_id = body.get("batch_id")
    column = body.get("column", "pm2.5")
    params = body.get("params", {})

    if batch_id not in BATCH_SESSIONS:
        return jsonify({"error": "Batch session not found."}), 404

    batch = BATCH_SESSIONS[batch_id]
    stations = batch.get("stations_cleaned") or batch.get("stations_raw")
    if not stations:
        return jsonify({"error": "No cleaned station data. Run multistation_clean first."}), 400

    try:
        result = run_comparative_analysis(stations, column, params)
        return jsonify(result)
    except ValueError as e:
        # Clean user-facing error (e.g. column not found)
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ──────────────────────────────────────────────────────────────────────────────
# EXPORT MULTI-STATION (merged CSV)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/multistation_export", methods=["POST"])
def multistation_export():
    body = request.json or {}
    batch_id = body.get("batch_id")
    fmt = body.get("format", "csv")

    if batch_id not in BATCH_SESSIONS:
        return jsonify({"error": "Batch session not found."}), 404

    batch = BATCH_SESSIONS[batch_id]
    stations = batch.get("stations_cleaned") or batch.get("stations_raw")
    if not stations:
        return jsonify({"error": "No data to export."}), 400

    # Merge all stations; sort by datetime so merged file is chronological
    # Each station's `location` column already carries its assigned name
    dfs = []
    for name, df in stations.items():
        df2 = df.copy()
        # Ensure location is stamped (defensive, should already be set)
        if "location" not in df2.columns or df2["location"].isna().all():
            df2["location"] = name
        dfs.append(df2)
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
    elif fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            for name, df in stations.items():
                sheet = name[:31]  # Excel sheet name limit
                df.to_excel(writer, sheet_name=sheet, index=False)
        buf.seek(0)
        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="multistation_data.xlsx")
    return jsonify({"error": "Unsupported format"}), 400


@app.route("/api/multistation_merge", methods=["POST"])
def multistation_merge():
    """Create a regular analysis session from all cleaned station datasets."""
    body = request.json or {}
    batch_id = body.get("batch_id")
    batch = BATCH_SESSIONS.get(batch_id)
    if not batch:
        return jsonify({"error": "Batch session not found."}), 404
    stations = batch.get("stations_cleaned")
    if not stations:
        return jsonify({"error": "Clean all stations before loading the merged data."}), 400

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

    session_id = os.urandom(8).hex()
    SESSIONS[session_id] = {
        "raw": merged.copy(),
        "cleaned": merged,
        "files": batch.get("file_names", []),
        "provider": batch.get("provider"),
        "batch_mode": True,
    }
    return jsonify({"session_id": session_id, **df_to_json(merged)})


# ──────────────────────────────────────────────────────────────────────────────
# CLEANING (single session)
# ──────────────────────────────────────────────────────────────────────────────
CLEANERS = {
    "purpleair":   clean_purpleair,
    "airnote":     clean_airnote,
    "airgradient": clean_airgradient,
    "clarity":     clean_clarity,
    "airqo":       clean_airqo,
    "airqoon":     clean_airqoon,
    "airbeam":     clean_airbeam,
    "sensirion":   clean_sensirion,
    "atmotube":    clean_atmotube,
    "pms5003":     clean_pms5003,
    "generic":     clean_generic,
}


def apply_column_mapping(df, mapping):
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


def preserve_categorical_columns(source_df, cleaned_df, options):
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


@app.route("/api/clean", methods=["POST"])
def clean_data():
    body = request.json or {}
    sid      = body.get("session_id")
    provider = (body.get("provider") or "").lower()
    options  = body.get("options", {})

    sess, err, code = get_session(sid)
    if err:
        return err, code

    if provider not in CLEANERS:
        return jsonify({"error": f"Unknown provider '{provider}'"}), 400

    try:
        # Inject station name from session (set via batch upload) or from options
        if "station_name" not in options and sess.get("station_name"):
            options = {**options, "station_name": sess["station_name"]}
        raw = apply_column_mapping(sess["raw"].copy(), options.get("column_mapping", {}))
        cleaned = CLEANERS[provider](raw, options)
        cleaned = preserve_categorical_columns(raw, cleaned, options)
        if "pm2.5" in cleaned.columns:
            cleaned = compute_aqi(cleaned)
        SESSIONS[sid]["cleaned"]  = cleaned
        SESSIONS[sid]["provider"] = provider
        return jsonify({"status": "ok", **df_to_json(cleaned)})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ──────────────────────────────────────────────────────────────────────────────
# COLUMN OPERATIONS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/columns/rename", methods=["POST"])
def rename_column():
    body = request.json or {}
    sid, old, new = body.get("session_id"), body.get("old_name"), body.get("new_name")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = (sess["cleaned"] if sess["cleaned"] is not None else sess["raw"])
    if old not in df.columns:
        return jsonify({"error": f"Column '{old}' not found"}), 400
    df.rename(columns={old: new}, inplace=True)
    if sess["cleaned"] is not None:
        SESSIONS[sid]["cleaned"] = df
    else:
        SESSIONS[sid]["raw"] = df
    return jsonify({"status": "ok", **df_to_json(df)})


@app.route("/api/columns/delete", methods=["POST"])
def delete_column():
    body = request.json or {}
    sid, col = body.get("session_id"), body.get("column")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = (sess["cleaned"] if sess["cleaned"] is not None else sess["raw"])
    if col not in df.columns:
        return jsonify({"error": f"Column '{col}' not found"}), 400
    df.drop(columns=[col], inplace=True)
    if sess["cleaned"] is not None:
        SESSIONS[sid]["cleaned"] = df
    else:
        SESSIONS[sid]["raw"] = df
    return jsonify({"status": "ok", **df_to_json(df)})


@app.route("/api/columns/add", methods=["POST"])
def add_column():
    body = request.json or {}
    sid = body.get("session_id")
    col_name = body.get("column")
    expression = body.get("expression", "")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = (sess["cleaned"] if sess["cleaned"] is not None else sess["raw"])
    try:
        df[col_name] = df.eval(expression)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    if sess["cleaned"] is not None:
        SESSIONS[sid]["cleaned"] = df
    else:
        SESSIONS[sid]["raw"] = df
    return jsonify({"status": "ok", **df_to_json(df)})


# ──────────────────────────────────────────────────────────────────────────────
# TEMPORAL AGGREGATION
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/aggregate", methods=["POST"])
def aggregate():
    body = request.json or {}
    sid      = body.get("session_id")
    freq     = body.get("freq", "h")
    # Map legacy pandas freq aliases to pandas 3.x equivalents
    _freq_map = {"H": "h", "M": "ME", "A": "YE", "Y": "YE", "Q": "QE"}
    freq = _freq_map.get(freq.upper(), freq)
    agg_func = body.get("agg_func", "mean")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    if "datetime" not in df.columns:
        return jsonify({"error": "No 'datetime' column found. Clean data first."}), 400
    df2 = df.copy()
    df2["datetime"] = pd.to_datetime(df2["datetime"], errors="coerce")
    df2 = df2.set_index("datetime")
    num = df2.select_dtypes(include="number")
    agg = getattr(num.resample(freq), agg_func)()
    agg = agg.reset_index()
    SESSIONS[sid]["cleaned"] = agg
    return jsonify({"status": "ok", **df_to_json(agg)})


# ──────────────────────────────────────────────────────────────────────────────
# MISSING DATA & OUTLIERS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/interpolate", methods=["POST"])
def interpolate():
    body = request.json or {}
    sid    = body.get("session_id")
    method = body.get("method", "linear")
    limit  = body.get("limit", None)
    sess, err, code = get_session(sid)
    if err: return err, code
    df = (sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]).copy()
    num_cols = df.select_dtypes(include="number").columns.tolist()
    if method == "ffill":
        df[num_cols] = df[num_cols].ffill(limit=limit)
    elif method == "bfill":
        df[num_cols] = df[num_cols].bfill(limit=limit)
    else:
        df[num_cols] = df[num_cols].interpolate(method=method, limit=limit, limit_direction="both")
    SESSIONS[sid]["cleaned"] = df
    return jsonify({"status": "ok", **df_to_json(df)})


@app.route("/api/remove_outliers", methods=["POST"])
def remove_outliers():
    body = request.json or {}
    sid    = body.get("session_id")
    column = body.get("column")
    method = body.get("method", "iqr")
    thresh = float(body.get("threshold", 3.0))
    sess, err, code = get_session(sid)
    if err: return err, code
    df = (sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]).copy()
    if column not in df.columns:
        return jsonify({"error": f"Column '{column}' not found"}), 400
    s = df[column]
    if method == "iqr":
        Q1, Q3 = s.quantile(0.25), s.quantile(0.75)
        IQR = Q3 - Q1
        mask = (s >= Q1 - thresh * IQR) & (s <= Q3 + thresh * IQR)
    elif method == "zscore":
        z = (s - s.mean()) / s.std()
        mask = z.abs() <= thresh
    else:
        lo, hi = s.quantile(thresh / 100), s.quantile(1 - thresh / 100)
        mask = (s >= lo) & (s <= hi)
    removed = int((~mask).sum())
    df = df[mask].reset_index(drop=True)
    SESSIONS[sid]["cleaned"] = df
    return jsonify({"status": "ok", "removed_rows": removed, **df_to_json(df)})


# ──────────────────────────────────────────────────────────────────────────────
# STATISTICS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/stats", methods=["POST"])
def stats():
    body = request.json or {}
    sid = body.get("session_id")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    desc = df.describe(include="all").replace({np.nan: None})
    missing = df.isnull().sum().to_dict()
    missing_pct = (df.isnull().mean() * 100).round(2).to_dict()
    return jsonify({
        "describe": desc.to_dict(),
        "missing": missing,
        "missing_pct": missing_pct,
        "shape": list(df.shape),
    })


# ──────────────────────────────────────────────────────────────────────────────
# PLOTS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/plot", methods=["POST"])
def plot():
    body = request.json or {}
    sid      = body.get("session_id")
    plot_type = body.get("plot_type")
    params   = body.get("params", {})
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    try:
        fig_json = generate_plot(df, plot_type, params)
        return jsonify({"plot": fig_json})
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ──────────────────────────────────────────────────────────────────────────────
# MAP DATA
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/map_data", methods=["POST"])
def map_data():
    body = request.json or {}
    sid = body.get("session_id")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    needed = {"lat", "lon"}
    if not needed.issubset(df.columns):
        return jsonify({"error": "Data must have 'lat' and 'lon' columns"}), 400
    cols = list(needed)
    for extra_col in ["pm2.5", "pm10", "CO2", "O3", "NO2", "temperature", "RH", "location", "datetime"]:
        if extra_col in df.columns:
            cols.append(extra_col)
    sub = df[cols].dropna(subset=["lat", "lon"]).copy()
    for c in sub.select_dtypes(include=["datetime64[ns]"]).columns:
        sub[c] = sub[c].astype(str)
    records = sub.replace({np.nan: None}).to_dict(orient="records")
    return jsonify({"data": records})


# ──────────────────────────────────────────────────────────────────────────────
# EXTREME ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/extreme", methods=["POST"])
def extreme():
    body = request.json or {}
    sid    = body.get("session_id")
    column = body.get("column", "pm2.5")
    params = body.get("params", {})
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    try:
        result = run_extreme_analysis(df, column, params)
        return jsonify(result)
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/compliance", methods=["POST"])
def compliance():
    body = request.json or {}
    sess, err, code = get_session(body.get("session_id"))
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    try:
        result = run_compliance_analysis(df, body.get("column", "pm2.5"), body.get("params", {}))
        return jsonify(result)
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.route("/api/machine_learning", methods=["POST"])
def machine_learning():
    body = request.json or {}
    sess, err, code = get_session(body.get("session_id"))
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    try:
        result = run_machine_learning(df, body.get("target", "pm2.5"), body.get("model", "trend"), body.get("params", {}))
        return jsonify(result)
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ──────────────────────────────────────────────────────────────────────────────
# MODEL EVALUATION
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/model_eval", methods=["POST"])
def model_eval():
    body = request.json or {}
    sid     = body.get("session_id")
    obs_col = body.get("obs_col")
    mod_col = body.get("mod_col")
    params  = body.get("params", {})
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    try:
        result = run_model_evaluation(df, obs_col, mod_col, params)
        return jsonify(result)
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


# ──────────────────────────────────────────────────────────────────────────────
# EXPORT (single session)
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/api/export", methods=["POST"])
def export_data():
    body   = request.json or {}
    sid    = body.get("session_id")
    fmt    = body.get("format", "csv")
    sess, err, code = get_session(sid)
    if err: return err, code
    df = sess["cleaned"] if sess["cleaned"] is not None else sess["raw"]
    buf = io.BytesIO()
    if fmt == "csv":
        df.to_csv(buf, index=False)
        buf.seek(0)
        return send_file(buf, mimetype="text/csv", as_attachment=True, download_name="cleaned_data.csv")
    elif fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False)
        buf.seek(0)
        return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="cleaned_data.xlsx")
    else:
        return jsonify({"error": "Unsupported format"}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
