"""
Comparative Analysis Module
Runs statistical comparison across multiple station/location DataFrames.
Computes: descriptive stats per station, temporal overlap stats,
pairwise correlations, seasonal means, diurnal profiles, and exceedance counts.
"""
import numpy as np
import pandas as pd
from scipy import stats


def run_comparative_analysis(stations: dict, column: str, params: dict) -> dict:
    """
    Parameters
    ----------
    stations : {name: pd.DataFrame}  — cleaned dataframes keyed by station name
    column   : pollutant column to compare (e.g. "pm2.5")
    params   : optional config (resample_freq, percentiles, standards)

    Returns dict suitable for JSON serialisation.
    """
    raw_freq = params.get("resample_freq", "D")
    # Map deprecated pandas aliases to pandas 3.x
    _freq_map = {"H": "h", "M": "ME", "A": "YE", "Y": "YE", "Q": "QE",
                 "h": "h", "D": "D", "W": "W", "ME": "ME"}
    freq = _freq_map.get(raw_freq.upper(), raw_freq)  # D / h / W / ME
    percentiles = params.get("percentiles", [25, 50, 75, 90, 95, 99])
    who_24h = params.get("who_24h", None)   # e.g. 15 μg/m³ for PM2.5
    epa_24h = params.get("epa_24h", None)   # e.g. 35 μg/m³
    top_n   = params.get("top_n", 10)

    resolved_column = column  # may be updated to case-matched name below
    result = {
        "column": resolved_column,  # updated after resolution
        "station_names": list(stations.keys()),
        "descriptive": {},
        "timeseries": {},
        "diurnal": {},
        "monthly": {},
        "seasonal": {},
        "exceedance": {},
        "pairwise_corr": {},
        "percentile_comparison": {},
    }

    series_map = {}  # name -> cleaned Series with datetime index

    # Build a map of lowercase->actual column name for each station
    available_cols_all = set()
    for name, df in stations.items():
        available_cols_all.update(df.columns.tolist())

    # Try exact match first, then case-insensitive
    resolved_column = column
    if column not in available_cols_all:
        col_lower = column.lower()
        for c in available_cols_all:
            if c.lower() == col_lower:
                resolved_column = c
                break

    for name, df in stations.items():
        # Find the actual column in this station's df
        actual_col = None
        if resolved_column in df.columns:
            actual_col = resolved_column
        else:
            col_lower = resolved_column.lower()
            for c in df.columns:
                if c.lower() == col_lower:
                    actual_col = c
                    break
        if actual_col is None:
            continue
        df2 = df.copy()
        if "datetime" not in df2.columns:
            continue
        df2["datetime"] = pd.to_datetime(df2["datetime"], errors="coerce")
        df2 = df2.set_index("datetime")
        s = pd.to_numeric(df2[actual_col], errors="coerce").dropna()
        if len(s) == 0:
            continue
        series_map[name] = s

    if not series_map:
        # Give a helpful error listing what IS available
        numeric_cols = sorted(set(
            c for df in stations.values()
            for c in df.select_dtypes(include="number").columns.tolist()
            if c not in ("year","month","day","hour","AQI")
        ))
        raise ValueError(
            f"Column '{column}' not found in any station dataset. "
            f"Available numeric columns: {numeric_cols}"
        )
    # Update result with the resolved column name
    result["column"] = resolved_column

    # ── Descriptive statistics ────────────────────────────────────────────────
    for name, s in series_map.items():
        result["descriptive"][name] = {
            "n":       int(s.count()),
            "mean":    round(float(s.mean()), 3),
            "median":  round(float(s.median()), 3),
            "std":     round(float(s.std()), 3),
            "min":     round(float(s.min()), 3),
            "max":     round(float(s.max()), 3),
            "skewness":round(float(s.skew()), 4),
            "kurtosis":round(float(s.kurtosis()), 4),
            "cv_pct":  round(float(s.std()/s.mean()*100), 2) if s.mean() != 0 else None,
            "date_start": str(s.index.min().date()) if len(s) else None,
            "date_end":   str(s.index.max().date()) if len(s) else None,
        }
        # Percentiles
        pct_vals = {}
        for p in percentiles:
            pct_vals[f"p{p}"] = round(float(np.nanpercentile(s.values, p)), 3)
        result["percentile_comparison"][name] = pct_vals

    # ── Resampled time series (for multi-line plot) ───────────────────────────
    all_ts = {}
    for name, s in series_map.items():
        resampled = s.resample(freq).mean().dropna()
        all_ts[name] = {
            "dates":  [str(d.date()) for d in resampled.index],
            "values": [round(float(v), 3) for v in resampled.values],
        }
    result["timeseries"] = all_ts

    # ── Diurnal profiles ──────────────────────────────────────────────────────
    for name, s in series_map.items():
        grp = s.groupby(s.index.hour)
        result["diurnal"][name] = {
            "hours":  list(range(24)),
            "mean":   [round(float(grp.get_group(h).mean()), 3) if h in grp.groups else None for h in range(24)],
            "median": [round(float(grp.get_group(h).median()), 3) if h in grp.groups else None for h in range(24)],
        }

    # ── Monthly means ─────────────────────────────────────────────────────────
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    for name, s in series_map.items():
        grp = s.groupby(s.index.month).mean()
        result["monthly"][name] = {
            "months": [month_names[i-1] for i in grp.index.tolist()],
            "values": [round(float(v), 3) for v in grp.values],
        }

    # ── Seasonal means ────────────────────────────────────────────────────────
    def month_to_season(m):
        return {12:"DJF",1:"DJF",2:"DJF",3:"MAM",4:"MAM",5:"MAM",
                6:"JJA",7:"JJA",8:"JJA",9:"SON",10:"SON",11:"SON"}.get(m,"?")

    for name, s in series_map.items():
        seasons = s.groupby(s.index.month.map(month_to_season)).mean()
        result["seasonal"][name] = {k: round(float(v), 3) for k, v in seasons.items()}

    # ── Exceedance counts ─────────────────────────────────────────────────────
    for name, s in series_map.items():
        exc = {}
        daily = s.resample("D").mean().dropna()
        if who_24h:
            exc["who_24h_count"] = int((daily > who_24h).sum())
            exc["who_24h_pct"]   = round(float((daily > who_24h).mean() * 100), 2)
        if epa_24h:
            exc["epa_24h_count"] = int((daily > epa_24h).sum())
            exc["epa_24h_pct"]   = round(float((daily > epa_24h).mean() * 100), 2)
        result["exceedance"][name] = exc

    # ── Pairwise Pearson correlations ─────────────────────────────────────────
    names = list(series_map.keys())
    corr_matrix = {}
    for i, n1 in enumerate(names):
        corr_matrix[n1] = {}
        for j, n2 in enumerate(names):
            if i == j:
                corr_matrix[n1][n2] = 1.0
                continue
            s1, s2 = series_map[n1], series_map[n2]
            # Align on common timestamps
            common = s1.index.intersection(s2.index)
            if len(common) < 5:
                corr_matrix[n1][n2] = None
                continue
            r, _ = stats.pearsonr(s1[common].values, s2[common].values)
            corr_matrix[n1][n2] = round(float(r), 4)
    result["pairwise_corr"] = {
        "names": names,
        "matrix": [[corr_matrix[n1].get(n2) for n2 in names] for n1 in names],
    }

    # ── ANOVA test across stations ────────────────────────────────────────────
    if len(series_map) >= 2:
        groups = [s.values for s in series_map.values()]
        try:
            f_stat, p_val = stats.f_oneway(*groups)
            result["anova"] = {
                "F_statistic": round(float(f_stat), 4),
                "p_value":     round(float(p_val), 6),
                "significant": bool(p_val < 0.05),
            }
        except Exception:
            result["anova"] = None

    # ── Rank stations by mean ─────────────────────────────────────────────────
    ranking = sorted(
        [(name, result["descriptive"][name]["mean"]) for name in result["descriptive"]],
        key=lambda x: x[1], reverse=True
    )
    result["ranking"] = [{"station": n, "mean": v, "rank": i+1} for i, (n, v) in enumerate(ranking)]

    return result
