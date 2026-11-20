"""Dataset profiling used by the UI and the offline assistant.

All functions are pure Pandas/NumPy and run locally.  They are deliberately
defensive: sensor exports frequently contain empty columns, duplicated
timestamps, non-numeric sentinels, and very short deployments.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from analysis.extreme import EPA_STANDARDS, WHO_STANDARDS

# ── units & aliases ───────────────────────────────────────────────────────────
UNIT_MAP = {
    "pm1": "µg/m³", "pm1.0": "µg/m³", "pm2.5": "µg/m³", "pm4.0": "µg/m³",
    "pm10": "µg/m³", "pm100": "µg/m³",
    "no2": "µg/m³", "nox": "µg/m³", "o3": "µg/m³", "so2": "µg/m³", "nh3": "µg/m³",
    "co": "mg/m³", "co2": "ppm", "voc": "index", "voc_index": "index", "nox_index": "index",
    "temperature": "°C", "temp": "°C", "rh": "%", "humidity": "%",
    "pressure": "hPa", "windspeed": "m/s", "wind_speed": "m/s",
    "wind_direction": "°", "wind_direction_deg": "°", "rainfall": "mm",
    "aqi": "index", "lat": "°", "lon": "°", "latitude": "°", "longitude": "°",
}

COLUMN_ALIASES = {
    "pm25": "pm2.5", "pm2_5": "pm2.5", "pm2-5": "pm2.5", "fine": "pm2.5",
    "pm1": "pm1.0", "pm4": "pm4.0",
    "carbon dioxide": "co2", "co₂": "co2",
    "temperature": "temperature", "temp": "temperature", "air temperature": "temperature",
    "humidity": "rh", "relative humidity": "rh", "rh": "rh",
    "nitrogen dioxide": "no2", "ozone": "o3", "sulphur dioxide": "so2",
    "sulfur dioxide": "so2", "carbon monoxide": "co",
    "volatile organic compounds": "voc", "tvoc": "voc",
    "wind speed": "windspeed", "wind direction": "wind_direction",
    "air quality index": "aqi",
}

PRIMARY_ORDER = [
    "pm2.5", "pm25", "pm10", "no2", "nox", "o3", "so2", "co", "co2", "voc",
    "aqi", "temperature", "rh",
]

# Plausibility bounds used only for a data-quality heuristic, not for deletion.
PLAUSIBLE_BOUNDS = {
    "pm1.0": (0, 2000), "pm2.5": (0, 2000), "pm4.0": (0, 3000), "pm10": (0, 5000),
    "no2": (0, 5000), "nox": (0, 5000), "o3": (0, 2000), "so2": (0, 5000),
    "co": (0, 1000), "co2": (0, 100000), "voc": (0, 600), "voc_index": (0, 600),
    "temperature": (-90, 70), "rh": (0, 100), "pressure": (300, 1200),
    "windspeed": (0, 120), "wind_direction": (0, 360), "windspeed_deg": (0, 360),
    "aqi": (0, 1000), "lat": (-90, 90), "lon": (-180, 180),
}


def normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def unit_for(column: str) -> str:
    key = str(column).strip().lower()
    return UNIT_MAP.get(key, UNIT_MAP.get(key.replace("_", ""), ""))


def find_datetime_column(df: pd.DataFrame) -> Optional[str]:
    if df is None or df.empty:
        return None
    preferred = ["datetime", "timestamp", "time", "date", "date_time", "utcdatetime",
                 "captured_at", "created_at", "recorded_at", "obs_time", "start_time"]
    lower = {str(c).lower(): c for c in df.columns}
    for name in preferred:
        if name in lower:
            return lower[name]
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return str(col)
    for col in df.columns:
        label = str(col).lower()
        if "time" in label or "date" in label:
            sample = df[col].dropna().head(80)
            if sample.empty:
                continue
            parsed = pd.to_datetime(sample, errors="coerce")
            if parsed.notna().mean() >= 0.7:
                return str(col)
    return None


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.select_dtypes(include="number").columns]


def resolve_column(df: pd.DataFrame, query: str) -> Optional[str]:
    if df is None or not query:
        return None
    text = str(query).strip()
    exact = {str(c).lower(): str(c) for c in df.columns}
    if text.lower() in exact:
        return exact[text.lower()]

    norm_query = normalise_name(text)
    aliased = COLUMN_ALIASES.get(text.lower()) or COLUMN_ALIASES.get(norm_query)
    if aliased:
        for col in df.columns:
            if normalise_name(col) == normalise_name(aliased):
                return str(col)

    best = None
    best_score = 0.0
    for col in df.columns:
        norm_col = normalise_name(col)
        if not norm_col:
            continue
        if norm_col == norm_query:
            return str(col)
        if norm_query and (norm_query in norm_col or norm_col in norm_query):
            score = min(len(norm_query), len(norm_col)) / max(len(norm_query), len(norm_col))
            if score > best_score:
                best, best_score = str(col), score
    return best if best_score >= 0.5 else None


def find_columns_in_text(df: pd.DataFrame, text: str) -> list[str]:
    """Return all dataframe columns referenced in a free-text question."""
    low = str(text).lower()
    found: list[str] = []
    for col in df.columns:
        label = str(col)
        n = normalise_name(label)
        if label.lower() in low or (len(n) >= 3 and n in normalise_name(low)):
            found.append(label)
    for phrase, canonical in COLUMN_ALIASES.items():
        if phrase in low:
            match = resolve_column(df, canonical)
            if match and match not in found:
                found.append(match)
    return found


def primary_column(df: pd.DataFrame) -> Optional[str]:
    cols = list(df.columns)
    for candidate in PRIMARY_ORDER:
        match = resolve_column(df, candidate)
        if match and pd.api.types.is_numeric_dtype(df[match]):
            return match
    for col in df.select_dtypes(include="number").columns:
        if normalise_name(col) not in {"lat", "lon", "year", "month", "day", "hour", "minute"}:
            return str(col)
    return None


def get_series(df: pd.DataFrame, column: str, *, with_time: bool = False):
    series = pd.to_numeric(df[column], errors="coerce")
    if with_time:
        dt_col = find_datetime_column(df)
        if dt_col:
            times = pd.to_datetime(df[dt_col], errors="coerce")
            mask = series.notna() & times.notna()
            return series[mask], pd.Series(times[mask].values, index=series[mask].index)
    return series, None


def _safe_round(value: Any, digits: int = 4) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _trend(series: pd.Series, times: Optional[pd.Series]) -> dict:
    clean = series.dropna()
    if len(clean) < 4:
        return {"direction": "insufficient data", "slope_per_day": None,
                "p_value": None, "change_pct_per_year": None}
    if times is not None and len(times) == len(series):
        aligned = times.loc[clean.index]
        x = (pd.to_datetime(aligned) - pd.to_datetime(aligned).iloc[0]).dt.total_seconds().to_numpy() / 86400.0
    else:
        x = np.arange(len(clean), dtype=float)
    y = clean.to_numpy(dtype=float)
    if len(np.unique(y)) < 2 or len(np.unique(x)) < 2:
        return {"direction": "stable", "slope_per_day": 0.0, "p_value": None,
                "change_pct_per_year": 0.0}
    try:
        from scipy import stats

        slope, intercept, r_value, p_value, _ = stats.linregress(x, y)
    except Exception:  # noqa: BLE001
        slope = float(np.polyfit(x, y, 1)[0])
        p_value = None
        r_value = None
    mean = float(clean.mean())
    per_year = (slope * 365.0 / mean * 100.0) if mean not in (0, np.nan) else None
    direction = "stable"
    if p_value is not None and p_value < 0.05:
        direction = "increasing" if slope > 0 else "decreasing"
    elif p_value is None and abs(slope) > 1e-9:
        direction = "increasing" if slope > 0 else "decreasing"
    return {
        "direction": direction,
        "slope_per_day": _safe_round(slope, 6),
        "p_value": _safe_round(p_value, 6),
        "r_squared": _safe_round(r_value ** 2, 4) if r_value is not None else None,
        "change_pct_per_year": _safe_round(per_year, 2),
    }


def series_summary(series: pd.Series, times: Optional[pd.Series] = None) -> dict:
    values = pd.to_numeric(series, errors="coerce").dropna()
    n = int(len(values))
    if n == 0:
        return {"n": 0, "valid": 0, "empty": True}
    mean = float(values.mean())
    std = float(values.std()) if n > 1 else 0.0
    summary = {
        "n": n,
        "valid": n,
        "missing": int(len(series) - n),
        "mean": _safe_round(mean),
        "median": _safe_round(values.median()),
        "std": _safe_round(std),
        "min": _safe_round(values.min()),
        "max": _safe_round(values.max()),
        "p5": _safe_round(np.percentile(values, 5)),
        "p25": _safe_round(np.percentile(values, 25)),
        "p75": _safe_round(np.percentile(values, 75)),
        "p95": _safe_round(np.percentile(values, 95)),
        "p99": _safe_round(np.percentile(values, 99)),
        "cv_pct": _safe_round(std / mean * 100, 2) if mean else None,
        "skewness": _safe_round(values.skew(), 3) if n >= 3 else None,
        "kurtosis": _safe_round(values.kurtosis(), 3) if n >= 4 else None,
        "trend": _trend(values, times),
        "first": _safe_round(values.iloc[0]),
        "last": _safe_round(values.iloc[-1]),
    }
    if times is not None and len(times) >= n:
        summary["first_time"] = str(pd.to_datetime(times.iloc[0]))
        summary["last_time"] = str(pd.to_datetime(times.iloc[-1]))
    return summary


def dataset_profile(df: pd.DataFrame, *, include_correlations: bool = True,
                    numeric_limit: int = 24) -> dict:
    """Return a compact, descriptive profile of the active dataframe."""
    n_rows, n_cols = int(df.shape[0]), int(df.shape[1])
    dt_col = find_datetime_column(df)
    date_start = date_end = None
    duration_days = None
    sampling_interval_seconds = None
    duplicates = None
    coverage_pct = None

    if dt_col:
        times = pd.to_datetime(df[dt_col], errors="coerce")
        valid_times = times.dropna()
        if len(valid_times):
            date_start = str(valid_times.min())
            date_end = str(valid_times.max())
            duration_days = _safe_round((valid_times.max() - valid_times.min()).total_seconds() / 86400, 2)
            diffs = valid_times.sort_values().diff().dt.total_seconds().dropna()
            diffs = diffs[diffs > 0]
            if len(diffs):
                sampling_interval_seconds = _safe_round(diffs.median(), 2)
                if sampling_interval_seconds and duration_days is not None:
                    expected = duration_days * 86400 / sampling_interval_seconds + 1
                    coverage_pct = _safe_round(min(100.0, len(valid_times) / expected * 100), 1)
            duplicates = int(valid_times.duplicated().sum())

    numeric = numeric_columns(df)
    missing_counts = df.isna().sum()
    missing_pct = (missing_counts / max(1, n_rows) * 100).round(2)
    missing_top = [
        {"column": str(c), "count": int(missing_counts[c]), "pct": float(missing_pct[c])}
        for c in missing_counts.sort_values(ascending=False).index[:12]
        if missing_counts[c] > 0
    ]

    primary = primary_column(df)
    primary_info = None
    numeric_summaries = {}
    if primary:
        series, times = get_series(df, primary, with_time=True)
        primary_info = {
            "column": primary,
            "unit": unit_for(primary),
            "summary": series_summary(series, times),
        }
    for col in numeric[:numeric_limit]:
        if col == dt_col:
            continue
        series, times = get_series(df, col, with_time=True)
        numeric_summaries[col] = series_summary(series, times)

    quality = quality_score(df, dt_col=dt_col, numeric=numeric, missing_pct=missing_pct,
                            duplicates=duplicates, coverage_pct=coverage_pct)

    profile = {
        "shape": [n_rows, n_cols],
        "memory_mb": _safe_round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
        "datetime_column": dt_col,
        "date_start": date_start,
        "date_end": date_end,
        "duration_days": duration_days,
        "sampling_interval_seconds": sampling_interval_seconds,
        "sampling_interval_label": human_interval(sampling_interval_seconds),
        "coverage_pct": coverage_pct,
        "duplicate_timestamps": duplicates,
        "missing_total": int(missing_counts.sum()),
        "missing_pct": _safe_round(missing_counts.sum() / max(1, n_rows * max(1, n_cols)) * 100, 2),
        "missing_top": missing_top,
        "columns": [
            {
                "name": str(c),
                "dtype": str(df[c].dtype),
                "kind": _kind(df[c]),
                "unit": unit_for(c),
                "missing_pct": float(missing_pct[c]),
                "unique": int(df[c].nunique(dropna=True)),
            }
            for c in df.columns
        ],
        "numeric_columns": numeric,
        "primary": primary_info,
        "numeric_summaries": numeric_summaries,
        "quality": quality,
        "aqi": aqi_stats(df),
        "stations": station_stats(df, primary),
        "groups": group_stats(df, primary),
        "anomalies": anomaly_stats(df, primary),
        "exceedances": exceedance_stats(df, primary),
    }
    if include_correlations:
        profile["correlations"] = top_correlations(df, numeric)
    return profile


def _kind(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    return "text"


def human_interval(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "irregular / unknown"
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def quality_score(df: pd.DataFrame, *, dt_col=None, numeric=None, missing_pct=None,
                  duplicates=None, coverage_pct=None) -> dict:
    n_rows = max(1, len(df))
    numeric = numeric if numeric is not None else numeric_columns(df)
    total_cells = max(1, df.shape[0] * max(1, df.shape[1]))
    completeness = 1.0 - float(df.isna().sum().sum()) / total_cells

    # Plausibility of recognised sensor columns.
    checks = []
    for col in df.columns:
        key = str(col).lower().replace(" ", "_")
        bounds = PLAUSIBLE_BOUNDS.get(key)
        if not bounds:
            continue
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        if values.empty:
            continue
        low, high = bounds
        checks.append(float(((values >= low) & (values <= high)).mean()))
    validity = float(np.mean(checks)) if checks else 1.0

    if dt_col:
        temporal = (coverage_pct if coverage_pct is not None else 1.0) / 100.0
        duplicate_quality = 1.0 - (float(duplicates or 0) / n_rows)
        components = {
            "completeness": completeness,
            "temporal_coverage": temporal,
            "validity": validity,
            "uniqueness": duplicate_quality,
        }
        weights = {"completeness": 0.40, "temporal_coverage": 0.20, "validity": 0.25, "uniqueness": 0.15}
    else:
        components = {"completeness": completeness, "validity": validity}
        weights = {"completeness": 0.60, "validity": 0.40}

    score = sum(components[k] * weights[k] for k in components) * 100
    score = max(0, min(100, round(score, 1)))
    grade = "A" if score >= 90 else "B" if score >= 78 else "C" if score >= 65 else "D" if score >= 50 else "E"
    issues = []
    if completeness < 0.9:
        issues.append(f"{round((1 - completeness) * 100, 1)}% of all cells are missing.")
    if validity < 0.95:
        issues.append("Some sensor values fall outside plausible physical ranges.")
    if dt_col and duplicates:
        issues.append(f"{duplicates} duplicated timestamps were detected.")
    if dt_col and coverage_pct is not None and coverage_pct < 80:
        issues.append(f"Timestamp coverage is about {coverage_pct:.0f}% of the expected record count.")
    if not issues:
        issues.append("No major structural quality issue detected.")
    return {
        "score": score,
        "grade": grade,
        "components": {k: round(v * 100, 1) for k, v in components.items()},
        "issues": issues,
    }


def group_stats(df: pd.DataFrame, column: Optional[str]) -> dict:
    if not column or column not in df.columns:
        return {}
    dt_col = find_datetime_column(df)
    if not dt_col:
        return {}
    times = pd.to_datetime(df[dt_col], errors="coerce")
    values = pd.to_numeric(df[column], errors="coerce")
    mask = times.notna() & values.notna()
    if mask.sum() < 3:
        return {}
    times, values = times[mask], values[mask]

    def summarise(labels, label_name):
        grouped = values.groupby(labels)
        means = grouped.mean().dropna()
        counts = grouped.count()
        if means.empty:
            return {}
        peak = means.idxmax()
        low = means.idxmin()
        return {
            "label": label_name,
            "values": {str(k): _safe_round(v) for k, v in means.items()},
            "counts": {str(k): int(v) for k, v in counts.items()},
            "peak": str(peak),
            "peak_value": _safe_round(means.max()),
            "low": str(low),
            "low_value": _safe_round(means.min()),
            "peak_to_low_ratio": _safe_round(means.max() / means.min(), 2) if float(means.min()) > 0 else None,
        }

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_labels = times.dt.month.map(lambda m: month_names[m - 1])
    season_map = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
                  6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
    return {
        "diurnal": summarise(times.dt.hour, "Hour of day"),
        "monthly": summarise(month_labels, "Month"),
        "seasonal": summarise(times.dt.month.map(season_map), "Season"),
        "weekday": summarise(times.dt.day_name(), "Day of week"),
    }


def anomaly_stats(df: pd.DataFrame, column: Optional[str], z_threshold: float = 3.5,
                  max_events: int = 10) -> dict:
    if not column or column not in df.columns:
        return {}
    values = pd.to_numeric(df[column], errors="coerce")
    clean = values.dropna()
    if len(clean) < 5:
        return {"count": 0, "pct": 0.0, "threshold": z_threshold, "events": []}
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    scale = 1.4826 * mad if mad > 0 else float(clean.std())
    scale = scale if scale and scale > 0 else 1.0
    robust_z = (values - median) / scale
    flagged = df.loc[robust_z.abs() >= z_threshold]
    dt_col = find_datetime_column(df)
    events = []
    for idx in flagged.index[:max_events]:
        row = flagged.loc[idx]
        event = {
            "value": _safe_round(values.loc[idx]),
            "robust_z": _safe_round(robust_z.loc[idx], 2),
        }
        if dt_col:
            event["datetime"] = str(row[dt_col])
        events.append(event)
    return {
        "count": int((robust_z.abs() >= z_threshold).sum()),
        "pct": _safe_round((robust_z.abs() >= z_threshold).mean() * 100, 2),
        "threshold": z_threshold,
        "median": _safe_round(median),
        "scale": _safe_round(scale),
        "events": events,
    }


def top_correlations(df: pd.DataFrame, numeric: Optional[list[str]] = None,
                     limit: int = 8, min_abs: float = 0.25) -> list[dict]:
    numeric = numeric or numeric_columns(df)
    usable = [c for c in numeric if df[c].nunique(dropna=True) > 1]
    if len(usable) < 2:
        return []
    try:
        corr = df[usable].corr(numeric_only=True)
    except Exception:  # noqa: BLE001
        return []
    pairs = []
    for i, a in enumerate(corr.columns):
        for b in corr.columns[i + 1:]:
            value = corr.loc[a, b]
            if pd.isna(value) or abs(value) < min_abs:
                continue
            pairs.append({"a": str(a), "b": str(b), "r": _safe_round(value, 4)})
    pairs.sort(key=lambda p: abs(p["r"]), reverse=True)
    return pairs[:limit]


def exceedance_stats(df: pd.DataFrame, column: Optional[str] = None) -> list[dict]:
    column = column or primary_column(df)
    if not column or column not in df.columns:
        return []
    key = str(column).lower()
    if key not in WHO_STANDARDS:
        key = key.replace(".", "")
    who = WHO_STANDARDS.get(key, {})
    epa = EPA_STANDARDS.get(key, {})
    values, times = get_series(df, column, with_time=True)
    if values.empty:
        return []
    results = []
    for authority, standards in (("WHO", who), ("EPA", epa)):
        for period, threshold in standards.items():
            if period == "annual":
                observed = float(values.mean())
                exceed = observed > threshold
                pct = 100.0 if exceed else 0.0
                n = 1
                label = "annual mean"
            elif period == "24h" and times is not None:
                daily = values.groupby(pd.to_datetime(times).dt.date).mean()
                observed = daily
                exceed = observed > threshold
                n = int(exceed.sum())
                pct = float(exceed.mean() * 100)
                label = "daily means"
            elif period in {"8h", "1h"}:
                exceed = values > threshold
                n = int(exceed.sum())
                pct = float(exceed.mean() * 100)
                label = f"{period} readings"
                observed = values
            else:
                continue
            if isinstance(observed, pd.Series):
                peak = _safe_round(observed.max())
            else:
                peak = _safe_round(observed)
            results.append({
                "authority": authority,
                "period": period,
                "threshold": threshold,
                "unit": unit_for(column),
                "count": n,
                "pct": _safe_round(pct, 2),
                "peak": peak,
                "basis": label,
            })
    return results


def station_stats(df: pd.DataFrame, column: Optional[str]) -> dict:
    if not column:
        return {}
    candidates = [c for c in ("location", "station", "_station", "site", "sensor", "device")
                  if c in df.columns]
    if not candidates:
        return {}
    col = candidates[0]
    if df[col].nunique(dropna=True) < 2:
        return {}
    values = pd.to_numeric(df[column], errors="coerce")
    frame = pd.DataFrame({"station": df[col].astype("string"), "value": values}).dropna()
    if frame.empty:
        return {}
    grouped = frame.groupby("station")["value"]
    stats = grouped.agg(["count", "mean", "median", "max"]).round(3)
    stats = stats.sort_values("mean", ascending=False)
    return {
        "column": str(col),
        "stations": [
            {
                "name": str(name),
                "n": int(row["count"]),
                "mean": _safe_round(row["mean"]),
                "median": _safe_round(row["median"]),
                "max": _safe_round(row["max"]),
            }
            for name, row in stats.iterrows()
        ],
    }


def aqi_stats(df: pd.DataFrame) -> dict:
    if "AQI" not in df.columns:
        return {}
    values = pd.to_numeric(df["AQI"], errors="coerce").dropna()
    if values.empty:
        return {}
    result = {
        "mean": _safe_round(values.mean(), 1),
        "max": _safe_round(values.max(), 1),
        "p95": _safe_round(np.percentile(values, 95), 1),
    }
    if "AQI_Category" in df.columns:
        counts = df["AQI_Category"].dropna().astype(str).value_counts()
        result["categories"] = {str(k): int(v) for k, v in counts.items()}
        unhealthy = counts[[c for c in counts.index if str(c) not in {"Good", "Moderate"}]].sum()
        result["unhealthy_pct"] = _safe_round(unhealthy / max(1, counts.sum()) * 100, 1)
    return result


def list_value_counts(df: pd.DataFrame, column: str, limit: int = 8) -> list[dict]:
    if column not in df.columns:
        return []
    counts = df[column].astype("string").fillna("(missing)").value_counts().head(limit)
    return [{"value": str(k), "count": int(v)} for k, v in counts.items()]
