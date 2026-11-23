# AQACT — Air Quality Analysis & Compliance Assessment Tool
### Climate-Air-Health Research & Analysis Platform

---

## What's New in v2

### 🆕 New Sensor Data Cleaners
| Sensor | Module | Key Measurements |
|--------|--------|-----------------|
| Sensirion SEN54/SEN55/SPS30 | `cleaners/sensirion.py` | PM1–PM10, NC0.5–NC10 (number conc.), VOC index, NOx index, CO2, RH, Temp |
| Atmotube Pro/Plus | `cleaners/atmotube.py` | PM1/2.5/10, VOC (mg/m³), GPS, met |
| Plantower PMS5003/7003/PMSA003 | `cleaners/pms5003.py` | PM std/atm fractions, particle counts per 0.1L (>0.3–>10µm), optional DHT22/BME280 |
| Generic / Auto-Detect | `cleaners/generic.py` | Fuzzy column mapping for any CSV with recognisable air quality fields |

### 📦 Batch File Stacking (new tab)
- Upload all daily files from a sensor deployment at once (e.g. PurpleAir SD card CSVs, AirBeam session files)
- Files are sorted alphabetically/chronologically and concatenated into one continuous time-series
- A `_source_file` column tracks which original file each row came from
- Seamlessly hands off the stacked dataset to the existing Clean workflow

### 🏙 Multi-Station Comparative Analysis (new tab)
- Upload datasets from **different locations** simultaneously
- Assign station names to each file via the UI
- One-click clean all stations with the same provider settings
- Generates side-by-side:
  - **Descriptive statistics table** (mean, median, std, CV, skewness, kurtosis)
  - **Time series overlay** (daily/hourly/weekly/monthly resolution)
  - **Diurnal profiles** for all stations
  - **Monthly bar charts**
  - **Seasonal means** (DJF / MAM / JJA / SON)
  - **Pairwise Pearson correlation matrix**
  - **Percentile comparison curves**
  - **Exceedance counts** against WHO/EPA thresholds
  - **One-way ANOVA** test (are station means significantly different?)
  - **Station ranking** by mean concentration
- Export as merged CSV or multi-sheet Excel (one sheet per station)

### 🌗 Dark / Light Theme Toggle
- Click the 🌙 / ☀️ button in the header to switch themes
- All plots and the entire UI switch simultaneously
- Theme-aware Plotly layouts (dark grid lines vs light grid lines)

### 📊 New Plot Types
| Plot | Use Case |
|------|----------|
| Violin | Distribution shape + outliers across seasons/groups |
| Bubble | 3-variable relationship (X, Y, size) |
| Multi-Pollutant Radar | Normalised pollutant overview for a sensor |
| CO2 + Met Dual-Axis | CO2, Temp, RH, VOC index on one chart |
| NC Fraction Timeseries | Particle number conc. fractions (SPS30/SEN5x) |
| PM Size Distribution | Bar chart of PM1/2.5/4/10 mass + NC fractions |
| Exceedance Frequency Curve | % of time exceeding each concentration |

### 🧪 Expanded AQI Module
- Added NO2 sub-index (μg/m³)
- CO2 advisory categorisation (400 → Hazardous/Outdoor/Indoor bands)
- VOC advisory categorisation (Sensirion 0–500 index scale)
- Adds `CO2_Category`, `CO2_Color`, `VOC_Category`, `VOC_Color` columns automatically

### 🗺 Map Enhancements
- **Colour-by parameter** selector: PM2.5, PM10, AQI, CO2, Temperature, RH, O3
- Popup now shows temperature and RH alongside PM2.5
- Generic colour gradient for non-AQI parameters

### ⚡ Extreme Analysis Additions
- Added NO2, NOx, CO2 to WHO/EPA standards tables
- Added CV%, p95, p99 to descriptive stats block

---

## Project Structure

```
AQAT_upgraded/
├── run.py                    ← Start here: python run.py
├── app.py                    ← Flask backend (all API routes)
├── templates/
│   └── index.html            ← Full single-page frontend
├── static/                   ← Auto-downloaded on first run
│   ├── plotly-2.35.2.min.js
│   ├── leaflet-1.9.4.js / .css
│   └── leaflet-heat.js
├── cleaners/
│   ├── purpleair.py          (original — EPA-TTQ correction)
│   ├── airnote.py            (original)
│   ├── airgradient.py        (original — CO2/VOC aware)
│   ├── clarity.py            (original)
│   ├── airqo.py              (original)
│   ├── airqoon.py            (original)
│   ├── airbeam.py            (original — F→C detection)
│   ├── sensirion.py          ★ NEW — SEN5x/SPS30
│   ├── atmotube.py           ★ NEW — Atmotube Pro
│   ├── pms5003.py            ★ NEW — Plantower PMS5003/7003
│   └── generic.py            ★ NEW — Auto-detect
├── analysis/
│   ├── aqi.py                ★ UPGRADED — NO2, CO2, VOC
│   ├── extreme.py            ★ UPGRADED — expanded standards
│   ├── model_eval.py         (original)
│   └── comparative.py        ★ NEW — multi-station stats
└── plots/
    └── generators.py         ★ UPGRADED — 8 new plot types
```

---

## Quick Start

```bash
cd AQAT_upgraded
python run.py
# Browser opens automatically at http://localhost:5000
```

---

## Workflow Guide

### Single Dataset
1. **Upload** → Drop your CSV/Excel file(s)
2. **Clean** → Select provider, configure options, click Clean
3. **Explore** → View statistics and missing data
4. **Plots** → Build charts (20+ plot types)
5. **Map** → Visualise spatial distribution
6. **Extreme** → Return levels, exceedance statistics
7. **Model Eval** → Compare observed vs modelled columns

### Daily File Batch (PurpleAir SD, AirBeam sessions)
1. Go to **Batch Stack** tab
2. Select provider and station name
3. Drop all daily files → Click Stack & Load
4. Continue from **Clean** tab as normal

### Multi-Station Comparison
1. Go to **Multi-Station** tab
2. Select provider, drop one file per station
3. Label each station in the UI
4. Click Upload All Stations
5. Click Clean All Stations
6. Select pollutant, time resolution, standards
7. Click Run Comparative Analysis

---

## Supported Sensors Summary

| Provider | PM | Gas | Met | GPS | CO2 | VOC | NC |
|----------|:--:|:---:|:---:|:---:|:---:|:---:|:--:|
| PurpleAir | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Airnote | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirGradient | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Clarity | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirQo | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirQoon | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AirBeam | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Sensirion SEN5x | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| Sensirion SPS30 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Atmotube Pro | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| PMS5003/7003 | ✅ | ❌ | ✅* | ✅ | ❌ | ❌ | ✅ |
| Generic Auto | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

*PMS5003 met requires attached DHT22 or BME280 logged to same CSV.
