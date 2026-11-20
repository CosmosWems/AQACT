"""
AirBeam 2 / AirBeam 3 Data Cleaner
Handles session CSV exports from AirCasting platform.
GPS-tagged mobile monitor: PM1/2.5/10, temperature, RH.
Daily session files supported via Batch Stack.
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
               "SO2","NOx","NO2","NH3","CO","O3","CO2","VOC"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def clean_airbeam(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    # AirCasting exports use "Timestamp" or "Time" columns
    dt_col = _fc(df, ["timestamp","time","datetime","date","created_at"])
    if dt_col is None:
        raise ValueError("No datetime column found in AirBeam data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce", utc=True)
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location / GPS ────────────────────────────────────────────────────────
    # AirBeam exports may have "Session Name" as location
    loc_col = _fc(df, ["session_name","location","name","site","device",
                        "device_id","serial"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","long","lon","lng"])
    lat_col = _fc(df, ["latitude","lat"])
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan

    # ── PM ────────────────────────────────────────────────────────────────────
    # AirBeam columns often look like "AirBeam3-PM2.5 (μg/m³)"
    for out_col, cands in [
        ("pm1.0", ["airbeam3-pm1","airbeam2-pm1","airbeam-pm1","pm1","pm1.0",
                   "pm_1","airbeam3_pm1","airbeam2_pm1"]),
        ("pm2.5", ["airbeam3-pm2.5","airbeam2-pm2.5","airbeam-pm2.5","pm2.5",
                   "pm25","pm_2.5","airbeam3_pm2_5","airbeam2_pm2_5"]),
        ("pm10",  ["airbeam3-pm10","airbeam2-pm10","airbeam-pm10","pm10",
                   "pm_10","airbeam3_pm10","airbeam2_pm10"]),
    ]:
        # Try fuzzy match first (handles "(μg/m³)" suffixes)
        found = None
        for orig in df.columns:
            orig_lower = orig.lower().replace(" ", "").replace("-", "").replace("_", "")
            for c in cands:
                c_lower = c.lower().replace(" ", "").replace("-", "").replace("_", "")
                if c_lower in orig_lower:
                    found = orig
                    break
            if found:
                break
        if found is None:
            found = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[found], errors="coerce") if found else np.nan

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["airbeam3-f","airbeam3-c","temperature","temp","airbeam2-f"])
    rh_c   = _fc(df, ["airbeam3-rh","airbeam2-rh","humidity","rh","relative_humidity"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = np.nan
    df["rainfall"]    = np.nan

    # Temperature: AirBeam exports in °F
    if df["temperature"].median(skipna=True) > 50:
        df["temperature"] = (df["temperature"] - 32) / 1.8

    # ── Gaseous (pass-through) ────────────────────────────────────────────────
    for gas in ["SO2","NOx","NO2","NH3","CO","O3","CO2","VOC"]:
        gc = _fc(df, [gas, gas.lower()])
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
