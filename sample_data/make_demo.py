"""Generate the bundled, deterministic demo dataset.

Run from the project root:

    python sample_data/make_demo.py

The result is a small synthetic teaching dataset with realistic diurnal cycles,
a pollution episode, two locations and a handful of missing values.  It is not
measurement data and must not be used for regulatory decisions.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).with_name("demo_air_quality.csv")
RNG = np.random.default_rng(20250901)

START = pd.Timestamp("2025-09-01 00:00:00")
DAYS = 45
FREQ = "h"
STATIONS = {
    "Riverside": {"lat": 5.6037, "lon": -0.1870, "pm_factor": 1.0, "traffic": 1.0},
    "Hillcrest": {"lat": 5.6500, "lon": -0.2200, "pm_factor": 0.72, "traffic": 0.55},
}

rows = []
for station, info in STATIONS.items():
    index = pd.date_range(START, periods=DAYS * 24, freq=FREQ)
    hour = index.hour.to_numpy()
    day = index.dayofyear.to_numpy()
    weekday = index.dayofweek.to_numpy()

    # Meteorological drivers -------------------------------------------------
    temperature = (25.5 + 4.5 * np.sin((hour - 8) / 24 * 2 * np.pi)
                   + 1.2 * np.sin(day / 28 * 2 * np.pi) + RNG.normal(0, 0.9, len(index)))
    rh = np.clip(78 - 1.5 * (temperature - 25) + RNG.normal(0, 6, len(index)), 35, 98)
    wind_speed = np.clip(RNG.gamma(2.1, 1.05, len(index)) + 0.4, 0.2, 9.5)
    wind_dir = (RNG.normal(215, 55, len(index)) % 360)
    pressure = 1011 + 3 * np.sin(day / 19 * 2 * np.pi) + RNG.normal(0, 0.7, len(index))
    rainfall = np.where(RNG.random(len(index)) < 0.035, RNG.gamma(1.4, 2.4, len(index)), 0.0)

    # Pollution drivers ------------------------------------------------------
    traffic_peak = np.exp(-0.5 * ((hour - 7.5) / 2.2) ** 2) + 0.75 * np.exp(-0.5 * ((hour - 18) / 2.4) ** 2)
    week_effect = np.where(weekday < 5, 1.0, 0.72)
    dilution = 1.0 / (0.65 + wind_speed)
    episode = 1.0 + 2.4 * np.exp(-0.5 * ((day - 19.5) / 2.4) ** 2)

    pm25 = (12.5 * info["pm_factor"] * week_effect * episode
            * (0.72 + 0.55 * traffic_peak)
            * (0.70 + 0.55 * dilution)
            * np.exp(RNG.normal(0, 0.26, len(index))))
    pm25 = np.clip(pm25, 1.5, 280)

    pm10 = np.clip(pm25 * (1.55 + 0.25 * RNG.random(len(index)))
                   + 5.5 * (1 + 0.4 * np.sin((hour - 10) / 24 * 2 * np.pi))
                   + RNG.normal(0, 2.4, len(index)), 3, 420)
    pm1 = np.clip(pm25 * (0.58 + 0.10 * RNG.random(len(index))), 1, 220)

    no2 = np.clip(15 * info["traffic"] * week_effect * traffic_peak * (1.3 - 0.08 * wind_speed)
                  + RNG.normal(0, 3.5, len(index)), 0.5, 180)
    nox = no2 * (1.25 + 0.15 * RNG.random(len(index)))
    o3 = np.clip(28 + 26 * np.exp(-0.5 * ((hour - 14.5) / 3.2) ** 2)
                 - 0.22 * no2 + 6 * np.sin(day / 17 * 2 * np.pi)
                 + RNG.normal(0, 5.5, len(index)), 1, 220)
    co = np.clip(0.45 + 0.05 * no2 / 10 + RNG.normal(0, 0.08, len(index)), 0.05, 9)
    so2 = np.clip(3.2 + 0.08 * pm25 + RNG.normal(0, 1.1, len(index)), 0.2, 60)
    co2 = np.clip(420 + 55 * traffic_peak * week_effect + 0.9 * pm25
                  + RNG.normal(0, 18, len(index)), 395, 1400)
    voc = np.clip(85 + 38 * traffic_peak + RNG.normal(0, 22, len(index)), 0, 500)

    frame = pd.DataFrame({
        "datetime": index,
        "location": station,
        "lat": info["lat"] + RNG.normal(0, 0.0002, len(index)),
        "lon": info["lon"] + RNG.normal(0, 0.0002, len(index)),
        "pm1.0": pm1,
        "pm2.5": pm25,
        "pm10": pm10,
        "NO2": no2,
        "NOx": nox,
        "O3": o3,
        "CO": co,
        "SO2": so2,
        "CO2": co2,
        "VOC": voc,
        "temperature": temperature,
        "RH": rh,
        "pressure": pressure,
        "windspeed": wind_speed,
        "wind_direction": wind_dir,
        "rainfall": rainfall,
    })
    rows.append(frame)

data = pd.concat(rows, ignore_index=True)

# A tiny amount of realistic missingness and one obvious sensor spike.
for column in ("pm10", "VOC", "temperature", "O3"):
    mask = RNG.random(len(data)) < 0.012
    data.loc[mask, column] = np.nan
spike = RNG.integers(0, len(data))
data.loc[spike, "pm2.5"] = 412.0
data.loc[spike, "pm10"] = 488.0

data = data.sort_values(["datetime", "location"]).reset_index(drop=True)
for column in ("pm1.0", "pm2.5", "pm10", "NO2", "NOx", "O3", "CO", "SO2", "CO2", "VOC"):
    data[column] = data[column].round(2)
for column in ("temperature", "RH", "pressure", "windspeed"):
    data[column] = data[column].round(1)
data["wind_direction"] = data["wind_direction"].round(0)
data["rainfall"] = data["rainfall"].round(2)
data["lat"] = data["lat"].round(6)
data["lon"] = data["lon"].round(6)

OUT.parent.mkdir(parents=True, exist_ok=True)
data.to_csv(OUT, index=False)
print(f"Wrote {len(data):,} rows to {OUT}")
