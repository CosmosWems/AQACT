"""
AirGradient Open-Source Monitor Data Cleaner
Handles CSV exports from AirGradient API and local loggers.
Sensors: CO2 (SCD30/SCD40), PM (PMS5003), NO2, VOC (SGP41), met.
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
               "CO2","VOC","VOC_index","NOx_index",
               "SO2","NOx","NO2","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def clean_airgradient(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["timestamp","datetime","time","date","created_at","recorded_at"])
    if dt_col is None:
        raise ValueError("No datetime column found in AirGradient data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce", utc=True)
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    loc_col = _fc(df, ["location","name","sensor_id","device","serial","place_name"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","lon","lng"])
    lat_col = _fc(df, ["latitude","lat"])
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan

    # ── PM (PMS5003) ──────────────────────────────────────────────────────────
    for out_col, cands in [
        ("pm1.0", ["pm01","pm1","pm1.0","pm_01","pm_1"]),
        ("pm2.5", ["pm02","pm25","pm2.5","pm_02","pm_25","pm2_5"]),
        ("pm10",  ["pm10","pm_10"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── CO2 (SCD30/SCD40) ────────────────────────────────────────────────────
    co2_c = _fc(df, ["rco2","co2","co2_ppm","carbon_dioxide","scd_co2"])
    df["CO2"] = pd.to_numeric(df[co2_c], errors="coerce") if co2_c else np.nan

    # ── VOC / NOx index (SGP41) ───────────────────────────────────────────────
    voc_c     = _fc(df, ["tvoc_index","tvocindex","voc_index","vocindex","voc"])
    nox_c     = _fc(df, ["nox_index","noxindex","nox"])
    df["VOC_index"] = pd.to_numeric(df[voc_c], errors="coerce") if voc_c else np.nan
    df["NOx_index"] = pd.to_numeric(df[nox_c], errors="coerce") if nox_c else np.nan
    df["VOC"]       = df["VOC_index"]  # alias

    # ── NO2 (if external electrochemical board) ───────────────────────────────
    no2_c = _fc(df, ["no2","nitrogen_dioxide"])
    df["NO2"] = pd.to_numeric(df[no2_c], errors="coerce") if no2_c else np.nan

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["atmp","temperature","temp","temperature_c","ambient_temp"])
    rh_c   = _fc(df, ["rhum","humidity","rh","relative_humidity"])
    pres_c = _fc(df, ["pressure","baro"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
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

    out   = [c for c in TARGET_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in TARGET_COLS]
    return df[out + extra].reset_index(drop=True)
