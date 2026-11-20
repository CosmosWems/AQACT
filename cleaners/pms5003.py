"""
Plantower PMS5003 / PMS7003 / PMSA003 Data Cleaner
Raw SD card or serial logger output from RPi/Arduino loggers.
Handles PM1/2.5/10 standard & atmospheric + particle counts per 0.1L.
"""
import pandas as pd
import numpy as np

MONTH_SEASON = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
NA_VALUES = [-99.9,-9.99,-9999,-999,-1,"M"," ","*",""]

TARGET_COLS = ["datetime","year","month","day","hour","season",
               "location","lon","lat",
               "pm1.0_std","pm2.5_std","pm10_std",
               "pm1.0_atm","pm2.5","pm10",
               "gt0.3um","gt0.5um","gt1.0um","gt2.5um","gt5.0um","gt10um",
               "temperature","RH","pressure","rainfall",
               "SO2","NOx","NH3","CO","O3"]


def _fc(df, cands):
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def clean_pms5003(df: pd.DataFrame, options: dict) -> pd.DataFrame:
    df = df.copy()
    df.replace(NA_VALUES, np.nan, inplace=True)

    # ── datetime ──────────────────────────────────────────────────────────────
    dt_col = _fc(df, ["timestamp","datetime","time","date_time","date"])
    if dt_col is None:
        # Try combining date + time columns
        date_c = _fc(df, ["date"])
        time_c = _fc(df, ["time"])
        if date_c and time_c:
            df["datetime"] = pd.to_datetime(
                df[date_c].astype(str) + " " + df[time_c].astype(str), errors="coerce"
            )
        else:
            raise ValueError("No datetime column found in PMS5003 data.")
    else:
        df["datetime"] = pd.to_datetime(df[dt_col], errors="coerce")

    if hasattr(df["datetime"].dt, "tz") and df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert(None)

    df["year"]   = df["datetime"].dt.year
    df["month"]  = df["datetime"].dt.month
    df["day"]    = df["datetime"].dt.day
    df["hour"]   = df["datetime"].dt.hour
    df["season"] = df["month"].map(MONTH_SEASON)

    # ── location ──────────────────────────────────────────────────────────────
    loc_col = _fc(df, ["location","site","name","device_id","sensor"])
    df["location"] = np.nan
    # Override with user-assigned station name if provided
    if options.get("station_name"):
        df["location"] = options["station_name"]
    df["lon"] = pd.to_numeric(df[_fc(df,["longitude","lon"])], errors="coerce") \
        if _fc(df,["longitude","lon"]) else np.nan
    df["lat"] = pd.to_numeric(df[_fc(df,["latitude","lat"])], errors="coerce") \
        if _fc(df,["latitude","lat"]) else np.nan

    # ── PM standard environment (CF=1) ───────────────────────────────────────
    pm1s  = _fc(df, ["pm1.0_std","pm1_std","pm1.0cf1","pm1_cf1","pm1.0 (ug/m3)","pm1_ug_m3"])
    pm25s = _fc(df, ["pm2.5_std","pm25_std","pm2.5cf1","pm25_cf1","pm2.5 (ug/m3)","pm25_ug_m3"])
    pm10s = _fc(df, ["pm10_std","pm10cf1","pm10 (ug/m3)","pm10_ug_m3"])
    df["pm1.0_std"] = pd.to_numeric(df[pm1s],  errors="coerce") if pm1s  else np.nan
    df["pm2.5_std"] = pd.to_numeric(df[pm25s], errors="coerce") if pm25s else np.nan
    df["pm10_std"]  = pd.to_numeric(df[pm10s], errors="coerce") if pm10s else np.nan

    # ── PM atmospheric environment ────────────────────────────────────────────
    pm1a  = _fc(df, ["pm1.0_atm","pm1_atm","pm1.0_env"])
    pm25a = _fc(df, ["pm2.5_atm","pm25_atm","pm2.5_env","pm2.5","pm25"])
    pm10a = _fc(df, ["pm10_atm","pm10_env","pm10"])
    df["pm1.0_atm"] = pd.to_numeric(df[pm1a],  errors="coerce") if pm1a  else df.get("pm1.0_std", np.nan)
    df["pm2.5"]     = pd.to_numeric(df[pm25a], errors="coerce") if pm25a else df.get("pm2.5_std", np.nan)
    df["pm10"]      = pd.to_numeric(df[pm10a], errors="coerce") if pm10a else df.get("pm10_std",  np.nan)

    # ── Particle counts per 0.1L ──────────────────────────────────────────────
    for out_col, cands in [
        ("gt0.3um", ["particles_>0.3um","gt0.3um","n_0.3","count_0.3um","0.3um"]),
        ("gt0.5um", ["particles_>0.5um","gt0.5um","n_0.5","count_0.5um","0.5um"]),
        ("gt1.0um", ["particles_>1.0um","gt1.0um","n_1.0","count_1.0um","1.0um"]),
        ("gt2.5um", ["particles_>2.5um","gt2.5um","n_2.5","count_2.5um","2.5um"]),
        ("gt5.0um", ["particles_>5.0um","gt5.0um","n_5.0","count_5.0um","5.0um"]),
        ("gt10um",  ["particles_>10um","gt10um","n_10","count_10um","10um"]),
    ]:
        c = _fc(df, cands)
        df[out_col] = pd.to_numeric(df[c], errors="coerce") if c else np.nan

    # ── Optional DHT22 / BME280 attached sensors ──────────────────────────────
    temp_c = _fc(df, ["temperature","temp","temperature_c","t"])
    rh_c   = _fc(df, ["humidity","rh","relative_humidity"])
    pres_c = _fc(df, ["pressure","pressure_hpa"])
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

    out = [c for c in TARGET_COLS if c in df.columns]
    extra = [c for c in df.columns if c not in TARGET_COLS]
    return df[out + extra].reset_index(drop=True)
