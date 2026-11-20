"""
Atmotube Pro / Plus Data Cleaner
Handles VOC, PM1/2.5/10, temperature, humidity, pressure, GPS.
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*",""]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0","pm2.5","pm10",
               "VOC","temperature","RH","pressure","rainfall",
               "SO2","NOx","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def clean_atmotube(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["date","datetime","timestamp","time","created_at"])
    if dt_col is None:
        raise ValueError("No datetime column found in Atmotube data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce")
    if hasattr(df["datetime"].dt, "tz") and df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location / GPS ────────────────────────────────────────────────────────
    loc_col = _fc(df, ["location","site","device","name"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]
    df["lon"] = pd.to_numeric(df[_fc(df,["longitude","long","lon"])], errors="coerce") \
        if _fc(df,["longitude","long","lon"]) else np.nan
    df["lat"] = pd.to_numeric(df[_fc(df,["latitude","lat"])], errors="coerce") \
        if _fc(df,["latitude","lat"]) else np.nan

    # ── PM ───────────────────────────────────────────────────────────────────
    pm1_c  = _fc(df, ["pm1","pm1.0","pm_1","particulate matter (pm1)"])
    pm25_c = _fc(df, ["pm2.5","pm25","pm_2.5","particulate matter (pm2.5)"])
    pm10_c = _fc(df, ["pm10","pm_10","particulate matter (pm10)"])
    df["pm1.0"] = pd.to_numeric(df[pm1_c],  errors="coerce") if pm1_c  else np.nan
    df["pm2.5"] = pd.to_numeric(df[pm25_c], errors="coerce") if pm25_c else np.nan
    df["pm10"]  = pd.to_numeric(df[pm10_c], errors="coerce") if pm10_c else np.nan

    # ── VOC ───────────────────────────────────────────────────────────────────
    voc_c = _fc(df, ["voc","voc_mg/m3","volatile organic compounds (voc)","tvoc"])
    df["VOC"] = pd.to_numeric(df[voc_c], errors="coerce") if voc_c else np.nan

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["temperature","temp","t,°c","temperature (°c)"])
    rh_c   = _fc(df, ["humidity","rh","humidity (%)","relative humidity"])
    pres_c = _fc(df, ["pressure","pressure (hpa)","barometric pressure"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
    df["rainfall"]    = np.nan

    # Check if temp is Fahrenheit
    if df["temperature"].median(skipna=True) > 50:
        df["temperature"] = (df["temperature"] - 32) / 1.8

    for gas in ["SO2","NOx","NH3","CO","O3"]:
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

    out = [c for c in TARGET_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in TARGET_COLS]
    return df[out + extra].reset_index(drop=True)
