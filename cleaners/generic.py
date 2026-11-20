"""
Generic / Auto-Detect Data Cleaner
Attempts to intelligently map common column name patterns to AQAT standard columns.
Works as a fallback for any CSV data with recognizable air quality columns.
"""
import pandas as pd
import numpy as np
import re

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*","","nan","NaN","NULL","null","N/A","n/a"]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0","pm2.5","pm4.0","pm10",
               "temperature","RH","pressure","rainfall","windspeed","wind_direction",
               "CO2","VOC","VOC_index","NOx_index",
               "SO2","NOx","NO2","NH3","CO","O3",
               "NC0.5","NC1.0","NC2.5","NC10"]

# Fuzzy pattern -> standard column
PATTERN_MAP = [
    (["pm1.0","pm1 ","pm_1","pm1$","mc_pm1p0","pm1.0_"],             "pm1.0"),
    (["pm2.5","pm25","pm_25","pm2_5","mc_pm2p5","pm2.5_"],            "pm2.5"),
    (["pm4.0","pm4 ","pm_4","mc_pm4p0"],                              "pm4.0"),
    (["pm10 ","pm_10","pm10$","mc_pm10"],                             "pm10"),
    (["temperature","temp","t_c","t/°c","ambient_temp"],              "temperature"),
    (["humidity","rh","relative_h","relativehumidity"],               "RH"),
    (["pressure","baro","hpa"],                                       "pressure"),
    (["rainfall","rain","precip"],                                     "rainfall"),
    (["windspeed","wind_speed","ws ","wind speed"],                   "windspeed"),
    (["wind_dir","winddir","wind direction","wd "],                   "wind_direction"),
    (["co2","carbon_dioxide","co2_ppm"],                              "CO2"),
    (["voc_index","vocindex"],                                        "VOC_index"),
    (["nox_index","noxindex"],                                        "NOx_index"),
    (["voc ","tvoc","voc$"],                                          "VOC"),
    (["so2 ","sulfur_diox"],                                          "SO2"),
    (["nox ","nitrogen_ox"],                                          "NOx"),
    (["no2 ","nitrogen_di","no2$"],                                   "NO2"),
    (["nh3 ","ammonia"],                                              "NH3"),
    ([" co ","co_ppm","co$","carbon_mono"],                           "CO"),
    (["o3 ","ozone","o3$"],                                           "O3"),
    (["longitude","long ","lon$","lng$"],                             "lon"),
    (["latitude","lat$"],                                             "lat"),
    (["nc0.5","nc_0.5","n0.5","count_0.5"],                          "NC0.5"),
    (["nc1.0","nc_1.0","n1.0","count_1.0"],                          "NC1.0"),
    (["nc2.5","nc_2.5","n2.5","count_2.5"],                          "NC2.5"),
    (["nc10","nc_10","n10","count_10"],                               "NC10"),
]

DT_CANDIDATES = ["timestamp","datetime","time","date_time","created_at","date","recorded_at","obs_time"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def _fuzzy_match(col_name: str, patterns: list) -> bool:
    cn = col_name.lower().replace("-","_").replace(" ","_")
    pm_target = next((target for pats, target in PATTERN_MAP if pats is patterns and target in {"pm1.0", "pm2.5", "pm4.0", "pm10"}), None)
    if pm_target:
        aliases = {"pm1.0": {"pm1", "pm1.0"}, "pm2.5": {"pm25", "pm2.5"},
                   "pm4.0": {"pm4", "pm4.0"}, "pm10": {"pm10", "pm10.0"}}
        tokens = re.findall(r"pm(?:\d+(?:\.\d+)?)", col_name.lower().replace("_", "."))
        return any(token in aliases[pm_target] for token in tokens)
    for p in patterns:
        # Strip trailing space (used as word-boundary hint)
        pp = p.strip()
        if pp.endswith("$"):
            if cn == pp[:-1]:
                return True
        elif pp in cn:
            return True
    return False


def _auto_map(df: pd.DataFrame) -> dict:
    """Return {standard_col: original_col} mapping."""
    mapping = {}
    used = set()
    for patterns, std_col in PATTERN_MAP:
        if std_col in mapping:
            continue
        for orig in df.columns:
            if orig in used:
                continue
            if _fuzzy_match(orig, patterns):
                mapping[std_col] = orig
                used.add(orig)
                break
    return mapping


def clean_generic(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, DT_CANDIDATES)
    if dt_col is None:
        # Try any column with 'date' or 'time' substring
        for c in df.columns:
            if "date" in c.lower() or "time" in c.lower():
                dt_col = c
                break
    if dt_col is None:
        raise ValueError("Cannot find a datetime column. Check your data.")

    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce")
    if hasattr(df["datetime"].dt, "tz") and df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── Auto-map columns ──────────────────────────────────────────────────────
    mapping = _auto_map(df)
    for std_col, orig_col in mapping.items():
        if std_col == "location":
            df[std_col] = df[orig_col].astype(str).str.strip()
        else:
            df[std_col] = pd.to_numeric(df[orig_col], errors="coerce")

    # Defaults for unmapped
    for col in TARGET_COLS:
        if col not in df.columns:
            df[col] = np.nan if col != "season" else df.get("season", "")

    # Location and station identity are user supplied; never infer them from headers.
    if options.get("location"):
        df["location"] = str(options["location"]).strip()
    if options.get("station_name"):
        df["location"] = str(options["station_name"]).strip()

    # Temperature Fahrenheit detection
    if "temperature" in df.columns:
        med = df["temperature"].median(skipna=True)
        if pd.notna(med) and med > 60:
            df["temperature"] = (df["temperature"] - 32) / 1.8

    # ── Resample ──────────────────────────────────────────────────────────────
    if options.get("resample_hourly", True):
        df = df.set_index("datetime")
        num  = df.select_dtypes(include="number").resample("h").mean()
        cats = df[["location"]].resample("h").first()
        df   = pd.concat([num, cats], axis=1).reset_index()

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["year"]   = df["datetime"].dt.year;  df["month"] = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day;   df["hour"]  = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    s = options.get("start_date"); e = options.get("end_date")
    if s: df = df[df["datetime"] >= pd.to_datetime(s)]
    if e: df = df[df["datetime"] <= pd.to_datetime(e)]

    out = [c for c in TARGET_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in TARGET_COLS and c != dt_col]
    return df[out + extra].reset_index(drop=True)
