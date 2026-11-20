"""
AirQoon Balloon-Borne Sensor Data Cleaner
Handles CSVs from AirQoon tethered/free balloon payloads.
Full met suite: pressure, altitude, temperature, RH + PM + gases.
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*","","nan","NaN","NULL","null","N/A"]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat","altitude",
               "pm1.0","pm2.5","pm10",
               "temperature","RH","pressure","rainfall",
               "windspeed","wind_direction",
               "CO2","VOC","SO2","NOx","NO2","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def clean_airqoon(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["timestamp","datetime","time","date","gps_time","fix_time"])
    if dt_col is None:
        raise ValueError("No datetime column found in AirQoon data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce", utc=True)
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location / GPS ────────────────────────────────────────────────────────
    loc_col = _fc(df, ["location","name","flight_id","payload_id","balloon","site"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","lon","gps_lon","lng"])
    lat_col = _fc(df, ["latitude","lat","gps_lat"])
    alt_col = _fc(df, ["altitude","alt","gps_alt","height_m","elevation"])
    df["lon"]      = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"]      = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan
    df["altitude"] = pd.to_numeric(df[alt_col], errors="coerce") if alt_col else np.nan

    # ── PM ────────────────────────────────────────────────────────────────────
    for out_col, cands in [
        ("pm1.0", ["pm1","pm1.0","pm_1","pm1_ug_m3"]),
        ("pm2.5", ["pm2.5","pm25","pm_2.5","pm2_5","pm25_ug_m3"]),
        ("pm10",  ["pm10","pm_10","pm10_ug_m3"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── Met (full balloon suite) ───────────────────────────────────────────────
    temp_c = _fc(df, ["temperature","temp","temperature_c","ext_temp","ambient_temp"])
    rh_c   = _fc(df, ["humidity","rh","relative_humidity","rh_%"])
    pres_c = _fc(df, ["pressure","baro","pressure_hpa","baro_hpa","station_pressure"])
    ws_c   = _fc(df, ["windspeed","wind_speed","wind_ms","ws"])
    wd_c   = _fc(df, ["wind_direction","winddir","wind_dir","wd"])
    df["temperature"]    = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]             = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]       = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
    df["windspeed"]      = pd.to_numeric(df[ws_c],   errors="coerce") if ws_c   else np.nan
    df["wind_direction"] = pd.to_numeric(df[wd_c],   errors="coerce") if wd_c   else np.nan
    df["rainfall"]       = np.nan

    # ── Gaseous ───────────────────────────────────────────────────────────────
    co2_c = _fc(df, ["co2","co2_ppm","carbon_dioxide"])
    voc_c = _fc(df, ["voc","tvoc","voc_ppb"])
    df["CO2"] = pd.to_numeric(df[co2_c], errors="coerce") if co2_c else np.nan
    df["VOC"] = pd.to_numeric(df[voc_c], errors="coerce") if voc_c else np.nan

    for gas in ["SO2","NOx","NO2","NH3","CO","O3"]:
        gc = _fc(df, [gas, gas.lower()])
        df[gas] = pd.to_numeric(df[gc], errors="coerce") if gc else np.nan

    # ── Sort by datetime (balloon data is often unsorted) ─────────────────────
    df = df.sort_values("datetime").reset_index(drop=True)

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
