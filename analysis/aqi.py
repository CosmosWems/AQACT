"""
AQI Computation Module — AQACT
Implements Indian CPCB AQI sub-index breakpoints for all major pollutants.
Added: NO2 sub-index, CO2 advisory thresholds, VOC advisory thresholds.
"""
import pandas as pd
import numpy as np


# ── Sub-index calculators ─────────────────────────────────────────────────────

def _pm25_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 30:  return x * 50 / 30
    if x <= 60:  return 50  + (x - 30)  * 50  / 30
    if x <= 90:  return 100 + (x - 60)  * 100 / 30
    if x <= 120: return 200 + (x - 90)  * 100 / 30
    if x <= 250: return 300 + (x - 120) * 100 / 130
    return            400 + (x - 250) * 100 / 130

def _pm10_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 50:  return x
    if x <= 100: return x
    if x <= 250: return 100 + (x - 100) * 100 / 150
    if x <= 350: return 200 + (x - 250)
    if x <= 430: return 300 + (x - 350) * 100 / 80
    return            400 + (x - 430) * 100 / 80

def _so2_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 40:   return x * 50 / 40
    if x <= 80:   return 50  + (x - 40)   * 50  / 40
    if x <= 380:  return 100 + (x - 80)   * 100 / 300
    if x <= 800:  return 200 + (x - 380)  * 100 / 420
    if x <= 1600: return 300 + (x - 800)  * 100 / 800
    return             400 + (x - 1600) * 100 / 800

def _nox_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 40:  return x * 50 / 40
    if x <= 80:  return 50  + (x - 40)  * 50  / 40
    if x <= 180: return 100 + (x - 80)  * 100 / 100
    if x <= 280: return 200 + (x - 180) * 100 / 100
    if x <= 400: return 300 + (x - 280) * 100 / 120
    return            400 + (x - 400) * 100 / 120

def _no2_sub(x):
    """NO2 sub-index (μg/m³) — WHO/EPA approximation mapped to CPCB scale."""
    if pd.isna(x) or x < 0: return np.nan
    if x <= 40:  return x * 50 / 40
    if x <= 80:  return 50  + (x - 40)  * 50  / 40
    if x <= 180: return 100 + (x - 80)  * 100 / 100
    if x <= 280: return 200 + (x - 180) * 100 / 100
    if x <= 400: return 300 + (x - 280) * 100 / 120
    return            400 + (x - 400) * 100 / 120

def _nh3_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 200:  return x * 50 / 200
    if x <= 400:  return 50  + (x - 200)  * 50  / 200
    if x <= 800:  return 100 + (x - 400)  * 100 / 400
    if x <= 1200: return 200 + (x - 800)  * 100 / 400
    if x <= 1800: return 300 + (x - 1200) * 100 / 600
    return             400 + (x - 1800) * 100 / 600

def _co_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 1:  return x * 50 / 1
    if x <= 2:  return 50  + (x - 1)  * 50  / 1
    if x <= 10: return 100 + (x - 2)  * 100 / 8
    if x <= 17: return 200 + (x - 10) * 100 / 7
    if x <= 34: return 300 + (x - 17) * 100 / 17
    return           400 + (x - 34) * 100 / 17

def _o3_sub(x):
    if pd.isna(x) or x < 0: return np.nan
    if x <= 50:  return x * 50 / 50
    if x <= 100: return 50  + (x - 50)  * 50  / 50
    if x <= 168: return 100 + (x - 100) * 100 / 68
    if x <= 208: return 200 + (x - 168) * 100 / 40
    if x <= 748: return 300 + (x - 208) * 100 / 539
    return            400 + (x - 400) * 100 / 539


SUB_FUNCS = {
    "pm2.5": _pm25_sub,
    "pm10":  _pm10_sub,
    "SO2":   _so2_sub,
    "NOx":   _nox_sub,
    "NO2":   _no2_sub,
    "NH3":   _nh3_sub,
    "CO":    _co_sub,
    "O3":    _o3_sub,
}

AQI_CATEGORIES = [
    (50,  "Good",                       "#00e400"),
    (100, "Moderate",                   "#ffff00"),
    (150, "Unhealthy for Sensitive",    "#ff7e00"),
    (200, "Unhealthy",                  "#ff0000"),
    (300, "Very Unhealthy",             "#8f3f97"),
    (500, "Hazardous",                  "#7e0023"),
]

# ── CO2 advisory bands (ppm) ──────────────────────────────────────────────────
CO2_LEVELS = [
    (400,  "Outdoor / Fresh",    "#00e400"),
    (600,  "Normal indoor",      "#80ff00"),
    (1000, "Slightly elevated",  "#ffff00"),
    (1500, "Elevated",           "#ff7e00"),
    (2500, "High",               "#ff0000"),
    (5000, "Very High",          "#8f3f97"),
    (float("inf"), "Hazardous",  "#7e0023"),
]

# ── VOC advisory bands (index, Sensirion scale 0-500) ─────────────────────────
VOC_LEVELS = [
    (100, "Excellent",  "#00e400"),
    (200, "Good",       "#80ff00"),
    (300, "Moderate",   "#ffff00"),
    (400, "Poor",       "#ff7e00"),
    (500, "Unhealthy",  "#ff0000"),
    (float("inf"), "Hazardous", "#8f3f97"),
]


def categorize_aqi(val):
    if pd.isna(val): return np.nan, np.nan
    for threshold, cat, color in AQI_CATEGORIES:
        if val <= threshold:
            return cat, color
    return "Hazardous", "#7e0023"


def categorize_co2(val):
    if pd.isna(val): return np.nan, np.nan
    for threshold, cat, color in CO2_LEVELS:
        if val <= threshold:
            return cat, color
    return "Hazardous", "#7e0023"


def categorize_voc(val):
    if pd.isna(val): return np.nan, np.nan
    for threshold, cat, color in VOC_LEVELS:
        if val <= threshold:
            return cat, color
    return "Hazardous", "#8f3f97"


def compute_aqi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add AQI sub-indices, overall AQI, AQI category, and colour columns.
    Also adds CO2_Category/Color and VOC_Category/Color if those columns exist.
    """
    sub_cols = []
    for poll, func in SUB_FUNCS.items():
        # Case-insensitive column lookup
        col = None
        for c in df.columns:
            if c.lower() == poll.lower():
                col = c
                break
        if col is None:
            col = poll  # leave as-is; will be absent
        if col in df.columns:
            sub_name = f"_sub_{poll}"
            df[sub_name] = df[col].apply(func)
            sub_cols.append(sub_name)

    if sub_cols:
        df["_valid_count"] = df[sub_cols].notna().sum(axis=1)
        df["AQI"] = df[sub_cols].max(axis=1)
        # Require at least PM2.5 sub-index to be valid
        pm25_sub = "_sub_pm2.5"
        if pm25_sub in sub_cols:
            df.loc[df[pm25_sub].isna(), "AQI"] = np.nan
        df.loc[df["_valid_count"] < 2, "AQI"] = np.nan
        df["AQI"] = df["AQI"].round()
        df[["AQI_Category", "AQI_Color"]] = df["AQI"].apply(
            lambda x: pd.Series(categorize_aqi(x))
        )
        df.drop(columns=sub_cols + ["_valid_count"], inplace=True)

    # ── CO2 advisory ──────────────────────────────────────────────────────────
    if "CO2" in df.columns:
        df[["CO2_Category", "CO2_Color"]] = df["CO2"].apply(
            lambda x: pd.Series(categorize_co2(x))
        )

    # ── VOC advisory ──────────────────────────────────────────────────────────
    voc_col = next((c for c in ["VOC_index", "VOC"] if c in df.columns), None)
    if voc_col:
        df[["VOC_Category", "VOC_Color"]] = df[voc_col].apply(
            lambda x: pd.Series(categorize_voc(x))
        )

    return df
