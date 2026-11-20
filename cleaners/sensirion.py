"""
Sensirion SEN5x / SPS30 Data Cleaner
Handles SEN54/SEN55 (PM1/PM2.5/PM4/PM10 + NOx + VOC + RH + Temp)
and SPS30 (PM1/PM2.5/PM4/PM10 + particle number concentrations).
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*","","nan","NaN"]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0","pm2.5","pm4.0","pm10",
               "temperature","RH","pressure","rainfall",
               "VOC_index","NOx_index","CO2",
               "NC0.5","NC1.0","NC2.5","NC4.0","NC10",
               "SO2","NOx","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def clean_sensirion(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["timestamp","datetime","time","date_time","created_at","date"])
    if dt_col is None:
        raise ValueError("No datetime column found in Sensirion data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce")
    if hasattr(df["datetime"].dt, "tz") and df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    loc_col = _fc(df, ["location","site","device_id","sensor_id","name","station"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]
    df["lon"] = pd.to_numeric(df[_fc(df,["longitude","lon"])], errors="coerce") \
        if _fc(df,["longitude","lon"]) else np.nan
    df["lat"] = pd.to_numeric(df[_fc(df,["latitude","lat"])], errors="coerce") \
        if _fc(df,["latitude","lat"]) else np.nan

    # ── PM fractions ──────────────────────────────────────────────────────────
    for out_col, cands in [
        ("pm1.0",  ["pm1.0","pm1","pm_1.0","mass_concentration_pm1p0","mc_pm1p0"]),
        ("pm2.5",  ["pm2.5","pm25","pm_2.5","mass_concentration_pm2p5","mc_pm2p5"]),
        ("pm4.0",  ["pm4.0","pm4","pm_4.0","mass_concentration_pm4p0","mc_pm4p0"]),
        ("pm10",   ["pm10","pm_10","mass_concentration_pm10p0","mc_pm10p0"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── Particle number concentrations (SPS30) ────────────────────────────────
    for out_col, cands in [
        ("NC0.5",  ["nc0.5","nc_0.5","number_concentration_pm0p5","nc_pm0p5"]),
        ("NC1.0",  ["nc1.0","nc_1.0","number_concentration_pm1p0","nc_pm1p0"]),
        ("NC2.5",  ["nc2.5","nc_2.5","number_concentration_pm2p5","nc_pm2p5"]),
        ("NC4.0",  ["nc4.0","nc_4.0","number_concentration_pm4p0","nc_pm4p0"]),
        ("NC10",   ["nc10","nc_10","number_concentration_pm10p0","nc_pm10p0"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── VOC / NOx index (SEN5x) ───────────────────────────────────────────────
    voc_c = _fc(df, ["voc_index","vocindex","voc","voc_raw"])
    nox_c = _fc(df, ["nox_index","noxindex","nox_raw"])
    df["VOC_index"] = pd.to_numeric(df[voc_c], errors="coerce") if voc_c else np.nan
    df["NOx_index"] = pd.to_numeric(df[nox_c], errors="coerce") if nox_c else np.nan

    # ── Met / gas ─────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["temperature","temp","temperature_c","ambient_temperature"])
    rh_c   = _fc(df, ["humidity","rh","relative_humidity","relativehumidity"])
    co2_c  = _fc(df, ["co2","co2_ppm","carbon_dioxide"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["CO2"]         = pd.to_numeric(df[co2_c],  errors="coerce") if co2_c  else np.nan
    df["pressure"]    = np.nan
    df["rainfall"]    = np.nan

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
