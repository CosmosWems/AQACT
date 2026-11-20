"""
Model Evaluation Module — AQACT
Computes comprehensive statistical metrics between observed and modelled data:
MB, NMB, RMSE, MAE, IOA, R, R², FAC2, regression parameters + scatter data.
"""
import numpy as np
import pandas as pd
from scipy import stats


def run_model_evaluation(df: pd.DataFrame, obs_col: str, mod_col: str, params: dict) -> dict:
    if obs_col not in df.columns:
        raise ValueError(f"Observed column '{obs_col}' not found.")
    if mod_col not in df.columns:
        raise ValueError(f"Model column '{mod_col}' not found.")

    sub = df[[obs_col, mod_col]].copy()
    sub[obs_col] = pd.to_numeric(sub[obs_col], errors="coerce")
    sub[mod_col] = pd.to_numeric(sub[mod_col], errors="coerce")
    sub = sub.dropna()

    if len(sub) < 5:
        raise ValueError("Too few paired data points for evaluation (need ≥ 5).")

    obs = sub[obs_col].values
    mod = sub[mod_col].values

    n        = len(obs)
    obs_mean = float(obs.mean())
    mod_mean = float(mod.mean())
    diff     = mod - obs

    MB   = float(diff.mean())
    NMB  = float(MB / obs_mean * 100) if obs_mean != 0 else np.nan
    RMSE = float(np.sqrt((diff ** 2).mean()))
    NMRMSe = float(RMSE / obs_mean * 100) if obs_mean != 0 else np.nan
    MAE  = float(np.abs(diff).mean())
    NMAE = float(MAE / obs_mean * 100) if obs_mean != 0 else np.nan

    corr_matrix = np.corrcoef(obs, mod)
    R    = float(corr_matrix[0, 1])
    R_sq = float(R ** 2)

    num_IOA = float(((mod - obs) ** 2).sum())
    den_IOA = float(((np.abs(mod - obs_mean) + np.abs(obs - obs_mean)) ** 2).sum())
    IOA = float(1 - num_IOA / den_IOA) if den_IOA != 0 else np.nan

    ratio = mod / np.where(obs == 0, np.nan, obs)
    FAC2  = float(np.nansum((ratio >= 0.5) & (ratio <= 2.0)) / n * 100)

    slope, intercept, r_val, p_val, std_err = stats.linregress(obs, mod)

    PBIAS = float(diff.sum() / obs.sum() * 100) if obs.sum() != 0 else np.nan

    r_kge = float(np.corrcoef(obs, mod)[0, 1])
    alpha  = float(mod.std() / obs.std()) if obs.std() != 0 else np.nan
    beta   = float(mod.mean() / obs_mean) if obs_mean != 0 else np.nan
    KGE    = float(1 - np.sqrt((r_kge - 1)**2 + (alpha - 1)**2 + (beta - 1)**2)) if not np.isnan(alpha) else np.nan

    max_pts = params.get("max_scatter_pts", 2000)
    if n > max_pts:
        idx = np.random.choice(n, max_pts, replace=False)
        obs_s, mod_s = obs[idx].tolist(), mod[idx].tolist()
    else:
        obs_s, mod_s = obs.tolist(), mod.tolist()

    daily_stats = {}
    if "datetime" in df.columns:
        tmp = df[["datetime", obs_col, mod_col]].copy()
        tmp["datetime"] = pd.to_datetime(tmp["datetime"], errors="coerce")
        tmp = tmp.set_index("datetime")[[obs_col, mod_col]]
        tmp[obs_col] = pd.to_numeric(tmp[obs_col], errors="coerce")
        tmp[mod_col] = pd.to_numeric(tmp[mod_col], errors="coerce")
        daily = tmp.resample("D").mean().dropna()
        if len(daily) >= 5:
            d_obs = daily[obs_col].values
            d_mod = daily[mod_col].values
            d_diff = d_mod - d_obs
            daily_stats = {
                "n":    int(len(daily)),
                "MB":   round(float(d_diff.mean()), 4),
                "RMSE": round(float(np.sqrt((d_diff**2).mean())), 4),
                "R":    round(float(np.corrcoef(d_obs, d_mod)[0, 1]), 4),
                "R2":   round(float(np.corrcoef(d_obs, d_mod)[0, 1]**2), 4),
            }

    bias_ts = []
    if "datetime" in df.columns:
        tmp2 = df[["datetime", obs_col, mod_col]].copy()
        tmp2["datetime"] = pd.to_datetime(tmp2["datetime"], errors="coerce")
        tmp2[obs_col] = pd.to_numeric(tmp2[obs_col], errors="coerce")
        tmp2[mod_col] = pd.to_numeric(tmp2[mod_col], errors="coerce")
        tmp2 = tmp2.dropna()
        tmp2["bias_pct"] = ((tmp2[mod_col] - tmp2[obs_col]) / tmp2[obs_col].replace(0, np.nan)) * 100
        bias_ts = tmp2[["datetime","bias_pct"]].assign(
            datetime=lambda d: d["datetime"].astype(str)
        ).replace({np.nan: None}).to_dict(orient="records")

    return {
        "n":          n,
        "obs_mean":   round(obs_mean, 4),
        "mod_mean":   round(mod_mean, 4),
        "MB":         round(MB, 4),
        "NMB":        round(NMB, 4) if not np.isnan(NMB) else None,
        "RMSE":       round(RMSE, 4),
        "NRMSE":      round(NMRMSe, 4) if not np.isnan(NMRMSe) else None,
        "MAE":        round(MAE, 4),
        "NMAE":       round(NMAE, 4) if not np.isnan(NMAE) else None,
        "R":          round(R, 4),
        "R2":         round(R_sq, 4),
        "IOA":        round(IOA, 4) if not np.isnan(IOA) else None,
        "FAC2":       round(FAC2, 2),
        "PBIAS":      round(PBIAS, 4) if not np.isnan(PBIAS) else None,
        "KGE":        round(KGE, 4) if not np.isnan(KGE) else None,
        "slope":      round(float(slope), 4),
        "intercept":  round(float(intercept), 4),
        "p_value":    round(float(p_val), 6),
        "std_err":    round(float(std_err), 6),
        "scatter":    {"obs": obs_s, "mod": mod_s},
        "daily_stats": daily_stats,
        "bias_ts":    bias_ts[:500],
    }
