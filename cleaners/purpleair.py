"""
PurpleAir PA-I / PA-II / SD-card Data Cleaner
Handles both API downloads and SD-card daily CSVs.
Applies the EPA-TTQ PM2.5 correction by default.
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*","","nan","NaN","NULL","null","N/A"]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0","pm2.5","pm2.5_cf1","pm10",
               "temperature","RH","pressure","rainfall",
               "VOC","CO2","SO2","NOx","NO2","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower().strip() in low:
            return low[c.lower().strip()]
    return None


def _epa_correction(pm25_cf1, rh):
    """EPA-TTQ correction (2021): PM2.5 = 0.524*CF1 - 0.0862*RH + 5.75"""
    if pd.isna(pm25_cf1) or pm25_cf1 < 0:
        return np.nan
    rh_val = rh if (pd.notna(rh) and 0 <= rh <= 100) else 50.0
    corrected = 0.524 * pm25_cf1 - 0.0862 * rh_val + 5.75
    return max(corrected, 0.0)


def clean_purpleair(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["UTCDateTime","created_at","timestamp","datetime","time",
                       "date_time","Date","date"])
    if dt_col is None:
        raise ValueError("No datetime column found in PurpleAir data.")
    df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce", utc=True)
    df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    loc_col = _fc(df, ["name","location","site","sensor_id","SensorId","Label","label"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]

    lon_col = _fc(df, ["longitude","lon","Lon","Long"])
    lat_col = _fc(df, ["latitude","lat","Lat"])
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan
    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan

    # ── PM fractions ──────────────────────────────────────────────────────────
    pm1_c   = _fc(df, ["pm1.0_atm","pm1_atm","pm1.0","pm1","PM1.0","PM1"])
    pm25cf1 = _fc(df, ["pm2.5_cf_1","pm2.5_cf1","PM2.5_CF1","pm2.5cf1","pm25_cf1"])
    pm25_c  = _fc(df, ["pm2.5_atm","pm2.5_alt","pm2.5","pm25","PM2.5"])
    pm10_c  = _fc(df, ["pm10.0_atm","pm10_atm","pm10.0","pm10","PM10"])

    df["pm1.0"]     = pd.to_numeric(df[pm1_c],   errors="coerce") if pm1_c   else np.nan
    df["pm2.5_cf1"] = pd.to_numeric(df[pm25cf1], errors="coerce") if pm25cf1 else np.nan
    df["pm2.5"]     = pd.to_numeric(df[pm25_c],  errors="coerce") if pm25_c  else np.nan
    df["pm10"]      = pd.to_numeric(df[pm10_c],  errors="coerce") if pm10_c  else np.nan

    # Fall back: use CF1 as raw pm2.5 if atm not present
    if df["pm2.5"].isna().all() and not df["pm2.5_cf1"].isna().all():
        df["pm2.5"] = df["pm2.5_cf1"]

    # ── Met ───────────────────────────────────────────────────────────────────
    temp_c = _fc(df, ["temperature","temp","Temperature","temp_f","Temp"])
    rh_c   = _fc(df, ["humidity","rh","Humidity","RH"])
    pres_c = _fc(df, ["pressure","Pressure","p_hpa"])
    df["temperature"] = pd.to_numeric(df[temp_c], errors="coerce") if temp_c else np.nan
    df["RH"]          = pd.to_numeric(df[rh_c],   errors="coerce") if rh_c   else np.nan
    df["pressure"]    = pd.to_numeric(df[pres_c], errors="coerce") if pres_c else np.nan
    df["rainfall"]    = np.nan

    # Temperature: PurpleAir reports °F internally (sensor runs ~8°F warm)
    if df["temperature"].median(skipna=True) > 50:
        df["temperature"] = (df["temperature"] - 32) / 1.8
        # Apply sensor heating offset correction
        df["temperature"] = df["temperature"] - 4.4

    # ── EPA-TTQ PM2.5 correction ──────────────────────────────────────────────
    apply_epa = options.get("epa_correction", True)
    if apply_epa and not df["pm2.5_cf1"].isna().all():
        df["pm2.5"] = df.apply(
            lambda r: _epa_correction(r["pm2.5_cf1"], r["RH"]), axis=1
        )

    # ── Gaseous pollutants (rare on PurpleAir but pass through) ───────────────
    for gas in ["VOC","CO2","SO2","NOx","NO2","NH3","CO","O3"]:
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
