"""
Extreme Value Analysis Module — AQACT
Expanded WHO/EPA standards to include NO2, CO2, VOC advisory levels.
"""
import pandas as pd
import numpy as np


WHO_STANDARDS = {
    "pm2.5": {"annual": 5,   "24h": 15},
    "pm10":  {"annual": 15,  "24h": 45},
    "no2":   {"annual": 10,  "24h": 25},
    "nox":   {"annual": 40,  "24h": 40},
    "o3":    {"8h": 60},
    "so2":   {"24h": 40},
    "co":    {"24h": 4},       # mg/m³
    "co2":   {"8h": 1000},    # ppm (indoor advisory)
}

EPA_STANDARDS = {
    "pm2.5": {"annual": 12,   "24h": 35},
    "pm10":  {"24h": 150},
    "no2":   {"annual": 53,   "1h": 100},
    "nox":   {"annual": 53,   "1h": 100},
    "o3":    {"8h": 70},
    "so2":   {"1h": 75},
    "co":    {"1h": 35, "8h": 9},
}


def _safe_col(df, col):
    low = {c.lower(): c for c in df.columns}
    return df[low[col.lower()]] if col.lower() in low else None


def _standard_with_overrides(defaults, overrides):
    standard = defaults.copy()
    for period, value in (overrides or {}).items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value >= 0:
            standard[period] = value
    return standard


def run_extreme_analysis(df: pd.DataFrame, column: str, params: dict) -> dict:
    s = _safe_col(df, column)
    if s is None:
        raise ValueError(f"Column '{column}' not found.")
    s = pd.to_numeric(s, errors="coerce").dropna()

    pcts = params.get("percentiles", [50, 75, 90, 95, 99, 99.9])

    # ── Descriptive statistics ────────────────────────────────────────────────
    desc = {
        "count":    int(s.count()),
        "mean":     round(float(s.mean()), 4),
        "median":   round(float(s.median()), 4),
        "std":      round(float(s.std()), 4),
        "min":      round(float(s.min()), 4),
        "max":      round(float(s.max()), 4),
        "skewness": round(float(s.skew()), 4),
        "kurtosis": round(float(s.kurt()), 4),
        "cv_pct":   round(float(s.std() / s.mean() * 100), 2) if s.mean() != 0 else None,
        "p95":      round(float(np.percentile(s, 95)), 4),
        "p99":      round(float(np.percentile(s, 99)), 4),
    }

    # ── Percentiles ───────────────────────────────────────────────────────────
    pct_vals = {f"p{p}": round(float(np.percentile(s, p)), 4) for p in pcts}

    # ── WHO exceedance ────────────────────────────────────────────────────────
    who_result, epa_result = {}, {}
    std_col = column.lower()
    if std_col not in WHO_STANDARDS and std_col not in EPA_STANDARDS:
        std_col = std_col.replace(".", "")

    if params.get("who_standard", True) and std_col in WHO_STANDARDS:
        w = _standard_with_overrides(WHO_STANDARDS[std_col], params.get("who_overrides"))
        if hasattr(s.index, "freq") or isinstance(s.index, pd.DatetimeIndex):
            daily_mean = s.resample("D").mean()
        else:
            daily_mean = s
        if "24h" in w:
            n_exceed = int((daily_mean > w["24h"]).sum())
            total    = int(daily_mean.count())
            who_result["24h_standard"]   = w["24h"]
            who_result["24h_exceed_n"]   = n_exceed
            who_result["24h_exceed_pct"] = round(n_exceed / total * 100, 2) if total else 0
        if "8h" in w:
            who_result["8h_standard"] = w["8h"]
        if "annual" in w:
            ann = float(s.mean())
            who_result["annual_standard"] = w["annual"]
            who_result["annual_mean"]     = round(ann, 4)
            who_result["annual_exceed"]   = ann > w["annual"]

    if params.get("epa_standard", True) and std_col in EPA_STANDARDS:
        e = _standard_with_overrides(EPA_STANDARDS[std_col], params.get("epa_overrides"))
        if hasattr(s.index, "freq") or isinstance(s.index, pd.DatetimeIndex):
            daily_mean = s.resample("D").mean()
        else:
            daily_mean = s
        if "24h" in e:
            n_exceed = int((daily_mean > e["24h"]).sum())
            total    = int(daily_mean.count())
            epa_result["24h_standard"]   = e["24h"]
            epa_result["24h_exceed_n"]   = n_exceed
            epa_result["24h_exceed_pct"] = round(n_exceed / total * 100, 2) if total else 0
        if "annual" in e:
            ann = float(s.mean())
            epa_result["annual_standard"] = e["annual"]
            epa_result["annual_mean"]     = round(ann, 4)
            epa_result["annual_exceed"]   = ann > e["annual"]
        for period in ["1h", "8h"]:
            if period in e:
                epa_result[f"{period}_standard"] = e[period]

    # ── Block maxima ──────────────────────────────────────────────────────────
    block = {"M": "ME", "A": "YE", "Y": "YE", "Q": "QE"}.get(
        str(params.get("block", "ME")).upper(), params.get("block", "ME")
    )
    block_maxima = {}
    if "datetime" in df.columns:
        tmp = df[["datetime", column]].copy()
        tmp["datetime"] = pd.to_datetime(tmp["datetime"], errors="coerce")
        tmp = tmp.set_index("datetime")[column]
        tmp = pd.to_numeric(tmp, errors="coerce")
        bm  = tmp.resample(block).max().dropna()
        block_maxima = {
            "block":           block,
            "values":          [round(float(v), 4) for v in bm.values],
            "dates":           [str(d) for d in bm.index],
            "max_of_maxima":   round(float(bm.max()), 4),
            "mean_of_maxima":  round(float(bm.mean()), 4),
        }

    # ── Return levels (Gumbel) ────────────────────────────────────────────────
    return_levels = {}
    if block_maxima.get("values"):
        bm_vals = np.array(block_maxima["values"])
        mu  = float(bm_vals.mean())
        sig = float(bm_vals.std()) * np.pi / np.sqrt(6)
        for T in [2, 5, 10, 25, 50, 100]:
            y = -np.log(-np.log(1 - 1 / T))
            return_levels[f"{T}yr"] = round(mu + sig * y, 4)

    # ── Top-N events ──────────────────────────────────────────────────────────
    top_n = params.get("top_n", 10)
    if "datetime" in df.columns:
        tmp2 = df[["datetime", column]].copy()
        tmp2["datetime"] = pd.to_datetime(tmp2["datetime"], errors="coerce")
        tmp2[column] = pd.to_numeric(tmp2[column], errors="coerce")
        top_events = (tmp2.nlargest(top_n, column)
                      .assign(datetime=lambda d: d["datetime"].astype(str))
                      .replace({np.nan: None})
                      .to_dict(orient="records"))
    else:
        top_idx    = s.nlargest(top_n).index.tolist()
        top_events = [{"index": int(i), column: round(float(s[i]), 4)} for i in top_idx]

    # ── Histogram ─────────────────────────────────────────────────────────────
    counts, bin_edges = np.histogram(s, bins=params.get("bins", 40))
    histogram = {
        "counts":    counts.tolist(),
        "bin_edges": [round(float(b), 4) for b in bin_edges],
    }

    return {
        "column":       column,
        "descriptive":  desc,
        "percentiles":  pct_vals,
        "who":          who_result,
        "epa":          epa_result,
        "block_maxima": block_maxima,
        "return_levels":return_levels,
        "top_events":   top_events,
        "histogram":    histogram,
    }
