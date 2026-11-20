"""
AirQo Low-Cost Sensor Network Data Cleaner
Handles CSV exports from the AirQo Analytics Dashboard (Africa network).
Sensors: PM2.5, PM10, met. Some nodes include NO2, CO.
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
               "NO2","CO","SO2","NOx","NH3","O3","CO2","VOC"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def clean_airqo(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["time","timestamp","datetime","date","created_at","start_time"])
    if dt_col is None:
        raise ValueError("No datetime column found in AirQo data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce", utc=True)
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    loc_col = _fc(df, ["site_name","location","name","device","device_id",
                        "channel_name","site","station"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","lon","lng"])
    lat_col = _fc(df, ["latitude","lat"])
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan

    # ── PM ────────────────────────────────────────────────────────────────────
    for out_col, cands in [
        ("pm1.0", ["pm1","pm1.0","pm_1","calibratedpm1"]),
        ("pm2.5", ["pm2_5","pm2.5","pm25","calibrated_pm2_5","calibratedpm2_5",
                   "pm2.5_calibrated","pm2_5_calibrated"]),
        ("pm10",  ["pm10","pm_10","calibratedpm10","pm10_calibrated"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── Gaseous ───────────────────────────────────────────────────────────────
    no2_c = _fc(df, ["no2","nitrogen_dioxide","no2_concentration"])
    co_c  = _fc(df, ["co","carbon_monoxide","co_concentration"])
    df["NO2"] = pd.to_numeric(df[no2_c], errors="coerce") if no2_c else np.nan
    df["CO"]  = pd.to_numeric(df[co_c],  errors="coerce") if co_c  else np.nan

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["temperature","temp","internaltemperature","external_temperature"])
    rh_c   = _fc(df, ["humidity","relativehumidity","rh","internalhumidity"])
    pres_c = _fc(df, ["pressure","barometricpressure"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
    df["rainfall"]    = np.nan

    for gas in ["SO2","NOx","NH3","O3","CO2","VOC"]:
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
