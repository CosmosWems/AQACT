"""
Airnote (Blues Wireless Notecard) Data Cleaner
Handles CSV exports from Notehub.io with full met suite.
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*","","nan","NaN","NULL","null","N/A"]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0","pm2.5","pm10",
               "temperature","RH","pressure","rainfall",
               "CO2","VOC","SO2","NOx","NO2","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def _parse_datetime(values):
    """Parse regular datetime values and numeric Unix epochs."""
    numeric = pd.to_numeric(values, errors="coerce")
    numeric_values = numeric.dropna()

    if len(numeric_values) and numeric.notna().mean() >= 0.8:
        magnitude = numeric_values.abs().median()
        if magnitude < 1e11:
            unit = "s"
        elif magnitude < 1e14:
            unit = "ms"
        elif magnitude < 1e17:
            unit = "us"
        else:
            unit = "ns"
        return pd.to_datetime(numeric, errors="coerce", unit=unit, utc=True)

    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    numeric_mask = parsed.isna() & numeric.notna()
    if numeric_mask.any():
        magnitude = numeric[numeric_mask].abs().median()
        unit = "s" if magnitude < 1e11 else "ms" if magnitude < 1e14 else "us" if magnitude < 1e17 else "ns"
        parsed.loc[numeric_mask] = pd.to_datetime(
            numeric[numeric_mask], errors="coerce", unit=unit, utc=True
        )
    return parsed


def clean_airnote(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)
    mapping = options.get("column_mapping", {}) or {}

    def mapped(target, candidates):
        selected = mapping.get(target)
        if selected in df.columns:
            return selected
        return _fc(df, candidates)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = mapped("datetime", ["captured_at","when","timestamp","datetime","time","date"])
    if dt_col is None:
        raise ValueError("No datetime column found in Airnote data.")
    df["datetime"] = _parse_datetime(df[dt_col])
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","lon","best_lon","gps_lon"])
    lat_col = _fc(df, ["latitude","lat","best_lat","gps_lat"])
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan

    # ── PM ────────────────────────────────────────────────────────────────────
    for out_col, cands in [
        ("pm1.0", ["pms_pm01_0","pm1.0","pm1","pms_pm1"]),
        ("pm2.5", ["pms_pm02_5","pm2.5","pm25","pms_pm2_5","pm2.5 (μg/m³)"]),
        ("pm10",  ["pms_pm10_0","pm10","pms_pm10"]),
    ]:
        c = mapped("pm1" if out_col == "pm1.0" else out_col, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = mapped("temperature", ["temperature","bme_temperature","temp","bme_temp"])
    rh_c   = mapped("RH", ["humidity","bme_humidity","rh","relative_humidity"])
    pres_c = _fc(df, ["pressure","bme_pressure","baro"])
    co2_c  = mapped("CO2", ["co2","scd_co2","co2_ppm","carbon_dioxide"])
    voc_c  = mapped("VOC", ["tvoc","voc","bme_gas","gas_resistance"])

    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
    df["CO2"]         = pd.to_numeric(df[co2_c],  errors="coerce") if co2_c  else np.nan
    df["VOC"]         = pd.to_numeric(df[voc_c],  errors="coerce") if voc_c  else np.nan
    rain_c = mapped("rainfall", ["rainfall","rain","precipitation"])
    df["rainfall"]    = pd.to_numeric(df[rain_c], errors="coerce") if rain_c else np.nan

    for gas in ["SO2","NOx","NO2","NH3","CO","O3"]:
        gc = mapped(gas, [gas, gas.lower()])
        df[gas] = pd.to_numeric(df[gc], errors="coerce") if gc else np.nan

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

    out   = [c for c in TARGET_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in TARGET_COLS]
    return df[out + extra].reset_index(drop=True)
