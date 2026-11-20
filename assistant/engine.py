"""Deterministic, fully offline assistant for AQACT.

The assistant is a small reasoning layer over :mod:`assistant.profiling` and
:mod:`assistant.insights`.  It recognises common questions, resolves the
relevant columns, computes answers from the active dataframe, and writes an
explanation in either plain language or technical language.

There is intentionally no network dependency and no hidden generative model.
Every sentence is traceable to a computed statistic, which keeps the tool
suitable for scientific and regulatory screening work.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import numpy as np
import pandas as pd

from assistant.insights import (
    dataset_insights,
    describe_plot,
    explain_analysis,
    fmt,
)
from assistant.profiling import (
    anomaly_stats,
    dataset_profile,
    exceedance_stats,
    find_columns_in_text,
    find_datetime_column,
    get_series,
    primary_column,
    resolve_column,
    series_summary,
    station_stats,
    top_correlations,
    unit_for,
)

# ── glossary ─────────────────────────────────────────────────────────────────
GLOSSARY: dict[str, dict[str, str]] = {
    "pm2.5": {
        "name": "PM2.5",
        "simple": "PM2.5 is the mass of tiny particles smaller than 2.5 micrometres. They can travel deep into the lungs and are linked to heart and lung problems.",
        "technical": "PM2.5 denotes particulate matter with an aerodynamic diameter ≤ 2.5 µm, reported as mass concentration (µg/m³). It is a primary health-relevant metric in WHO and national air-quality standards.",
    },
    "pm10": {
        "name": "PM10",
        "simple": "PM10 is the mass of particles smaller than 10 micrometres, including dust, pollen, and smoke. It can irritate the eyes, nose, and airways.",
        "technical": "PM10 is particulate matter with an aerodynamic diameter ≤ 10 µm, expressed as mass concentration (µg/m³). It includes both coarse and fine fractions.",
    },
    "pm1": {
        "name": "PM1",
        "simple": "PM1 is the mass of very fine particles smaller than 1 micrometre — the smallest fraction usually reported by optical sensors.",
        "technical": "PM1 is particulate matter with an aerodynamic diameter ≤ 1 µm (µg/m³), often reported by optical particle counters such as Sensirion and Plantower devices.",
    },
    "no2": {
        "name": "Nitrogen dioxide (NO₂)",
        "simple": "NO₂ is a gas produced mainly by traffic and combustion. High levels can irritate the airways and worsen asthma.",
        "technical": "NO₂ is a traffic- and combustion-related trace gas (µg/m³) and a precursor to ozone and secondary particulate matter.",
    },
    "o3": {
        "name": "Ozone (O₃)",
        "simple": "Ozone is a gas that forms in sunlight from traffic and industrial pollution. It can irritate the lungs, especially on hot afternoons.",
        "technical": "O₃ is a secondary photochemical pollutant (µg/m³) with strong diurnal and seasonal cycles driven by sunlight, temperature, and precursor concentrations.",
    },
    "so2": {
        "name": "Sulphur dioxide (SO₂)",
        "simple": "SO₂ comes from burning sulphur-containing fuels such as coal and oil. It can irritate the airways and contribute to acid rain.",
        "technical": "SO₂ is a combustion-derived trace gas (µg/m³) associated with fuel oil and coal use; it is a precursor to sulphate aerosol.",
    },
    "co": {
        "name": "Carbon monoxide (CO)",
        "simple": "CO is an odourless gas from incomplete combustion. It reduces the blood's ability to carry oxygen.",
        "technical": "CO is a colourless, odourless combustion product reported in mg/m³ in this platform; it has a high affinity for haemoglobin.",
    },
    "co2": {
        "name": "Carbon dioxide (CO₂)",
        "simple": "CO₂ is a gas exhaled by people and released by combustion. Indoors it is a useful indicator of ventilation and occupancy.",
        "technical": "CO₂ is reported in ppm. Outdoor background is roughly 420 ppm; indoor advisory thresholds are often set near 1000 ppm for ventilation assessment.",
    },
    "voc": {
        "name": "Volatile organic compounds (VOC)",
        "simple": "VOCs are a group of gases released by paints, cleaning products, fuels, and people. The sensor index is a relative signal, not a concentration.",
        "technical": "VOC measurements are usually reported as a manufacturer-specific index (e.g. Sensirion 0–500) rather than a mass concentration, limiting direct regulatory comparison.",
    },
    "aqi": {
        "name": "Air Quality Index (AQI)",
        "simple": "AQI turns pollutant concentrations into a simple 0–500 scale. Higher values mean greater health concern.",
        "technical": "AQI is a piecewise-linear index mapped from pollutant concentrations using breakpoints defined by the relevant authority; the platform uses common WHO/EPA-inspired categories for screening.",
    },
    "nox": {
        "name": "Nitrogen oxides (NOx)",
        "simple": "NOx is the sum of nitric oxide and nitrogen dioxide, mostly from traffic and combustion.",
        "technical": "NOx = NO + NO₂ (µg/m³); it is a precursor to ozone and nitrate aerosol and is strongly traffic-correlated.",
    },
    "rh": {
        "name": "Relative humidity (RH)",
        "simple": "Relative humidity is how full the air is with water vapour, from 0% to 100%.",
        "technical": "RH (%) is the ratio of actual vapour pressure to saturation vapour pressure at the same temperature; it affects optical particle sensor response.",
    },
    "correlation": {
        "name": "Correlation",
        "simple": "Correlation is a number from -1 to +1 that says whether two measurements move together. It does not prove that one causes the other.",
        "technical": "Pearson's r measures the strength and direction of a linear association. r near +1 indicates a strong positive linear relationship, r near -1 a strong negative one, and r near 0 little linear association.",
    },
    "who": {
        "name": "WHO guidelines",
        "simple": "The World Health Organization publishes recommended air-quality limits to protect public health.",
        "technical": "WHO air-quality guidelines provide health-based recommended limits for pollutants and averaging periods; they are not directly enforceable law in most jurisdictions.",
    },
    "epa": {
        "name": "EPA guidelines",
        "simple": "EPA-style limits are national or agency standards used to judge whether air quality is acceptable.",
        "technical": "EPA (or national environment agency) standards define enforceable or advisory limits by pollutant and averaging period; always confirm the jurisdiction-specific values.",
    },
    "anomaly": {
        "name": "Anomaly",
        "simple": "An anomaly is a reading that is unusually different from the rest of the dataset. It may be a real pollution event or a sensor problem.",
        "technical": "Anomalies are detected here with a robust median/MAD z-score, which is less sensitive to extreme values than the ordinary mean/SD z-score.",
    },
}

INTENT_LABELS = {
    "greeting": "Greeting",
    "help": "How to use the assistant",
    "overview": "Dataset overview",
    "columns": "Column list",
    "stats": "Descriptive statistics",
    "extremes": "Highest and lowest values",
    "missing": "Missing data",
    "quality": "Data quality",
    "timerange": "Time coverage",
    "trend": "Trend",
    "anomalies": "Anomalies",
    "correlation": "Relationships",
    "exceedance": "Guideline exceedance",
    "stations": "Station comparison",
    "glossary": "Definition",
    "plot": "Chart interpretation",
    "analysis": "Analysis interpretation",
    "insights": "Key insights",
    "values": "Value counts",
    "unsupported": "General guidance",
}

ACTION_WORDS = (
    "clean", "plot", "chart", "map", "export", "upload", "download", "compare",
    "trend", "anomaly", "compliance", "model", "analysis", "report", "guide",
)


def _unit(column: Optional[str]) -> str:
    unit = unit_for(column or "")
    return f" {unit}" if unit else ""


def _pct(value: Any, digits: int = 1) -> str:
    return f"{fmt(value, digits)}%"


def _mode_from_text(text: str, requested: Optional[str]) -> str:
    if requested in {"simple", "technical"}:
        return requested
    low = str(text).lower()
    technical_words = ("technical", "statistically", "statistic", "rigorous", "detail", "method", "p-value", "p value", "confidence")
    simple_words = ("simple", "beginner", "plain", "easy", "layman", "explain like", "in short")
    if any(word in low for word in technical_words):
        return "technical"
    if any(word in low for word in simple_words):
        return "simple"
    return "simple"


def _as_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce").dropna()


def _time_lookup(df: pd.DataFrame, column: str, index: Any) -> Optional[str]:
    dt_col = find_datetime_column(df)
    if not dt_col or index not in df.index:
        return None
    try:
        value = df.loc[index, dt_col]
        if pd.isna(value):
            return None
        return str(value)
    except Exception:  # noqa: BLE001
        return None


def _pick_columns(df: pd.DataFrame, question: str) -> list[str]:
    found = [c for c in find_columns_in_text(df, question) if c in df.columns]
    if found:
        return found
    primary = primary_column(df)
    return [primary] if primary else []


def _series_answer(df: pd.DataFrame, column: str) -> dict:
    series, times = get_series(df, column, with_time=True)
    summary = series_summary(series, times)
    return {"column": column, "summary": summary}


def _format_stats_block(column: str, summary: dict, technical: bool) -> str:
    unit = _unit(column)
    if not summary or summary.get("n", 0) == 0:
        return f"{column} has no valid numeric readings."
    if technical:
        return (
            f"{column}: n = {summary.get('valid')}, mean = {fmt(summary.get('mean'), 4)}{unit}, "
            f"median = {fmt(summary.get('median'), 4)}{unit}, SD = {fmt(summary.get('std'), 4)}, "
            f"min = {fmt(summary.get('min'), 4)}, p5 = {fmt(summary.get('p5'), 4)}, "
            f"p95 = {fmt(summary.get('p95'), 4)}, p99 = {fmt(summary.get('p99'), 4)}, "
            f"max = {fmt(summary.get('max'), 4)}; CV = {fmt(summary.get('cv_pct'), 2)}%."
        )
    return (
        f"{column} usually sits around {fmt(summary.get('mean'))}{unit} "
        f"(median {fmt(summary.get('median'))}{unit}). "
        f"Most readings fall between {fmt(summary.get('p5'))} and {fmt(summary.get('p95'))}{unit}, "
        f"and the highest recorded value is {fmt(summary.get('max'))}{unit}."
    )


def _answer_overview(df: pd.DataFrame, profile: dict, technical: bool) -> tuple[str, list[str]]:
    rows, cols = profile.get("shape", [0, 0])
    start, end = profile.get("date_start"), profile.get("date_end")
    quality = profile.get("quality") or {}
    primary = (profile.get("primary") or {}).get("column")
    bullets = [
        f"{rows:,} rows × {cols} columns",
        f"Period: {(start or 'unknown')[:19]} → {(end or 'unknown')[:19]}",
        f"Median sampling interval: {profile.get('sampling_interval_label', 'unknown')}",
        f"Quality grade: {quality.get('grade', '?')} ({quality.get('score', '?')}/100)",
    ]
    if primary:
        bullets.append(f"Primary pollutant column: {primary}")
    if technical:
        answer = (
            f"The active dataset contains {rows:,} observations and {cols} fields. "
            f"Temporal coverage is {profile.get('coverage_pct', 'unknown')}% of the expected record count, "
            f"with {profile.get('duplicate_timestamps', 0)} duplicated timestamps and "
            f"{_pct(profile.get('missing_pct'), 2)} missing cells. "
            f"The composite quality score is {quality.get('score')} (grade {quality.get('grade')})."
        )
    else:
        answer = (
            f"You have {rows:,} rows and {cols} columns. "
            + (f"The data covers {str(start)[:10]} to {str(end)[:10]}. " if start and end else "")
            + f"Data quality is graded {quality.get('grade', '?')} out of 100. "
            + (f"The main measurement appears to be {primary}." if primary else "")
        )
    return answer, bullets


def _answer_columns(df: pd.DataFrame, technical: bool) -> tuple[str, list[str]]:
    numeric = [str(c) for c in df.select_dtypes(include="number").columns]
    other = [str(c) for c in df.columns if str(c) not in numeric]
    bullets = [f"Numeric ({len(numeric)}): {', '.join(numeric[:15])}"]
    if other:
        bullets.append(f"Other ({len(other)}): {', '.join(other[:15])}")
    answer = (
        f"The dataset has {df.shape[1]} columns: {len(numeric)} numeric and {len(other)} text/datetime. "
        + ("Numeric fields include " + ", ".join(numeric[:8]) + "." if numeric else "")
    )
    if technical:
        dtypes = ", ".join(f"{c}={df[c].dtype}" for c in list(df.columns)[:20])
        answer += f" Dtypes: {dtypes}."
    return answer, bullets


def _answer_stats(df: pd.DataFrame, question: str, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = _pick_columns(df, question)
    if not columns:
        return "I could not identify a numeric column. Ask about a field such as PM2.5, PM10, temperature, or RH.", [], []
    chosen = columns[:3]
    lines: list[str] = []
    bullets: list[str] = []
    for column in chosen:
        summary = _series_answer(df, column)["summary"]
        lines.append(_format_stats_block(column, summary, technical))
        if summary.get("n"):
            bullets.append(
                f"{column}: mean {fmt(summary.get('mean'))}{_unit(column)}, "
                f"p95 {fmt(summary.get('p95'))}{_unit(column)}, max {fmt(summary.get('max'))}{_unit(column)}"
            )
    return " ".join(lines), bullets, chosen


def _answer_extremes(df: pd.DataFrame, question: str, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = _pick_columns(df, question)
    if not columns:
        return "I could not identify which column to inspect. Mention a field such as PM2.5 or temperature.", [], []
    column = columns[0]
    series, _ = get_series(df, column)
    if series.empty:
        return f"{column} has no valid numeric readings.", [], [column]
    low_wanted = any(word in question.lower() for word in ("lowest", "minimum", "min ", "smallest", "least"))
    index = series.idxmin() if low_wanted else series.idxmax()
    value = series.loc[index]
    when = _time_lookup(df, column, index)
    label = "lowest" if low_wanted else "highest"
    answer = (
        f"The {label} {column} reading is {fmt(value)}{_unit(column)}"
        + (f", recorded at {when}." if when else ".")
    )
    bullets = [f"{label.title()}: {fmt(value)}{_unit(column)}"]
    if when:
        bullets.append(f"Time: {when}")
    if technical:
        s = _as_series(df, column)
        answer += (
            f" Percentile context: p95={fmt(np.percentile(s, 95), 4)}, "
            f"p99={fmt(np.percentile(s, 99), 4)}, mean={fmt(s.mean(), 4)}."
        )
    return answer, bullets, [column]


def _answer_missing(df: pd.DataFrame, profile: dict, technical: bool) -> tuple[str, list[str]]:
    top = profile.get("missing_top") or []
    bullets = [f"{m['column']}: {m['pct']}% missing ({m['count']} cells)" for m in top[:8]]
    if not top:
        return "No missing values were detected in the active dataset.", bullets
    worst = top[0]
    answer = (
        f"About {_pct(profile.get('missing_pct'), 2)} of all cells are missing. "
        f"The most affected field is {worst['column']} at {_pct(worst['pct'])} "
        f"({worst['count']} cells)."
    )
    if technical:
        answer += f" The top missing fields are: " + ", ".join(f"{m['column']}={m['pct']}%" for m in top[:10]) + "."
    return answer, bullets


def _answer_quality(profile: dict, technical: bool) -> tuple[str, list[str]]:
    quality = profile.get("quality") or {}
    bullets = [f"Grade {quality.get('grade')} — {quality.get('score')}/100"]
    bullets.extend(quality.get("issues", [])[:5])
    answer = (
        f"Data quality is grade {quality.get('grade', '?')} with a score of "
        f"{quality.get('score', '?')} out of 100. "
        + " ".join(quality.get("issues", [])[:2])
    )
    if technical:
        answer += f" Components: {quality.get('components', {})}."
    return answer, bullets


def _answer_timerange(profile: dict, technical: bool) -> tuple[str, list[str]]:
    start, end = profile.get("date_start"), profile.get("date_end")
    if not start:
        return "No usable datetime column was detected, so I cannot describe the time coverage.", []
    bullets = [
        f"Start: {start}",
        f"End: {end}",
        f"Duration: {fmt(profile.get('duration_days'))} days",
        f"Median sampling interval: {profile.get('sampling_interval_label')}",
        f"Coverage: {_pct(profile.get('coverage_pct'))}",
        f"Duplicate timestamps: {profile.get('duplicate_timestamps')}",
    ]
    answer = (
        f"The record runs from {str(start)[:19]} to {str(end)[:19]}, "
        f"about {fmt(profile.get('duration_days'))} days, with a typical interval of "
        f"{profile.get('sampling_interval_label')}. Estimated timestamp coverage is "
        f"{_pct(profile.get('coverage_pct'))}."
    )
    if technical:
        answer += f" Detected datetime field: {profile.get('datetime_column')}."
    return answer, bullets


def _answer_trend(df: pd.DataFrame, question: str, profile: dict, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = _pick_columns(df, question)
    if not columns:
        return "Mention the measurement you want trended (for example PM2.5 or temperature).", [], []
    primary = (profile.get("primary") or {}).get("column")
    if primary in columns:
        summary = (profile.get("primary") or {}).get("summary") or {}
        trend = summary.get("trend") or {}
        column = primary
        bullets = [
            f"Direction: {trend.get('direction')}",
            f"Slope: {fmt(trend.get('slope_per_day'), 6)} per day",
            f"p-value: {fmt(trend.get('p_value'), 4)}",
            f"Annual change: {fmt(trend.get('change_pct_per_year'))}%",
        ]
        answer = (
            f"{column} is {str(trend.get('direction', 'not assessable')).replace('insufficient data', 'not yet assessable')} "
            f"across the record"
            + (f", with an estimated change of {fmt(trend.get('change_pct_per_year'))}% per year" if trend.get('change_pct_per_year') is not None else "")
            + ". "
            + ("This is statistically significant at the 5% level." if (trend.get("p_value") is not None and trend["p_value"] < 0.05) else "The trend is not statistically significant at the 5% level.")
        )
        if technical:
            answer += f" OLS slope={trend.get('slope_per_day')}, p={trend.get('p_value')}, R²={trend.get('r_squared')}."
        return answer, bullets, [column]
    column = columns[0]
    summary = _series_answer(df, column)["summary"]
    trend = summary.get("trend") or {}
    bullets = [f"{k}: {v}" for k, v in trend.items()]
    answer = (
        f"{column} is {trend.get('direction', 'not assessable')} "
        f"(change {fmt(trend.get('change_pct_per_year'))}% per year, "
        f"p = {fmt(trend.get('p_value'), 4)})."
    )
    return answer, bullets, [column]


def _answer_anomalies(df: pd.DataFrame, question: str, profile: dict, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = _pick_columns(df, question)
    if not columns:
        columns = [(profile.get("primary") or {}).get("column")] if (profile.get("primary") or {}).get("column") else []
    if not columns:
        return "I could not identify a numeric column to scan for anomalies.", [], []
    column = columns[0]
    stats = anomaly_stats(df, column)
    events = stats.get("events") or []
    bullets = [
        f"{e.get('datetime', 'unknown time')}: {fmt(e.get('value'))}{_unit(column)} (robust z={e.get('robust_z')})"
        for e in events[:6]
    ]
    answer = (
        f"I found {stats.get('count', 0)} unusual {column} readings "
        f"({_pct(stats.get('pct'), 2)} of valid data), using a robust z-score threshold of "
        f"{stats.get('threshold')}. Unusual readings can be real episodes or sensor faults."
    )
    if technical:
        answer += (
            f" Robust centre={fmt(stats.get('median'), 4)}, scale={fmt(stats.get('scale'), 4)} "
            f"(1.4826 × median absolute deviation)."
        )
    return answer, bullets, [column]


def _answer_correlation(df: pd.DataFrame, question: str, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = [c for c in find_columns_in_text(df, question) if c in df.columns]
    if len(columns) >= 2:
        a, b = columns[0], columns[1]
        pair = df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(pair) >= 3:
            r = pair[a].corr(pair[b])
            answer = (
                f"{a} and {b} have a correlation of {fmt(r, 3)} "
                f"across {len(pair):,} paired readings. "
                + ("This is a strong positive linear relationship." if r >= 0.7 else
                   "This is a strong negative linear relationship." if r <= -0.7 else
                   "This is a moderate relationship." if abs(r) >= 0.4 else
                   "There is little linear relationship.")
            )
            if technical:
                answer += " Pearson's r is sensitive to outliers and shared time trends."
            return answer, [f"r = {fmt(r, 4)}", f"n = {len(pair):,}"], [a, b]
    pairs = top_correlations(df, limit=6)
    if not pairs:
        return "No meaningful linear relationships were found among the numeric fields.", [], []
    bullets = [f"{p['a']} ↔ {p['b']}: r = {fmt(p['r'], 3)}" for p in pairs]
    answer = (
        "The strongest relationships are: "
        + "; ".join(f"{p['a']} and {p['b']} (r={fmt(p['r'])})" for p in pairs[:3])
        + ". Correlation does not prove causation."
    )
    if technical:
        answer += " These are Pearson correlations on complete pairs; autocorrelation is not adjusted."
    return answer, bullets, []


def _answer_exceedance(df: pd.DataFrame, question: str, profile: dict, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = _pick_columns(df, question)
    column = columns[0] if columns else (profile.get("primary") or {}).get("column")
    if not column:
        return "I could not identify a pollutant column for the guideline check.", [], []
    stats = exceedance_stats(df, column)
    if not stats:
        return f"No configured WHO or EPA guideline is available for {column}.", [], [column]
    bullets = []
    exceeded = []
    for item in stats:
        bullets.append(
            f"{item.get('authority')} {item.get('period')}: {item.get('count')} exceedance(s), "
            f"{_pct(item.get('pct'), 2)} of {item.get('basis')} (threshold {fmt(item.get('threshold'))}{_unit(column)})"
        )
        if (item.get("pct") or 0) > 0:
            exceeded.append(item)
    if exceeded:
        worst = max(exceeded, key=lambda item: item.get("pct") or 0)
        answer = (
            f"{column} exceeds the {worst.get('authority')} {worst.get('period')} screening threshold "
            f"of {fmt(worst.get('threshold'))}{_unit(column)} in {_pct(worst.get('pct'), 2)} of "
            f"{worst.get('basis')} ({worst.get('count')} cases)."
        )
    else:
        answer = f"{column} does not exceed the configured WHO/EPA screening thresholds in this record."
    if technical:
        answer += " Exceedance tests use daily or running-period means where the standard defines an averaging period."
    return answer, bullets, [column]


def _answer_stations(df: pd.DataFrame, profile: dict, technical: bool) -> tuple[str, list[str]]:
    block = profile.get("stations") or {}
    stations = block.get("stations") or []
    if len(stations) < 2:
        return "This dataset does not contain two or more labelled stations. Use the Multi-Station workspace to compare separate files.", []
    bullets = [
        f"{s['name']}: mean {fmt(s['mean'])}, median {fmt(s['median'])}, max {fmt(s['max'])} (n={s['n']})"
        for s in stations[:10]
    ]
    answer = (
        f"I found {len(stations)} station labels. The highest mean is at {stations[0]['name']} "
        f"({fmt(stations[0]['mean'])}{_unit((profile.get('primary') or {}).get('column'))}) and the lowest at "
        f"{stations[-1]['name']} ({fmt(stations[-1]['mean'])}{_unit((profile.get('primary') or {}).get('column'))})."
    )
    if technical:
        answer += f" Grouping field: {block.get('column')}."
    return answer, bullets


def _answer_glossary(question: str, technical: bool) -> tuple[str, list[str]]:
    low = question.lower()
    best_key = None
    for key in GLOSSARY:
        pattern = r"\b" + re.escape(key).replace(r"\.", r"\.?") + r"\b"
        if re.search(pattern, low):
            best_key = key
            break
    if not best_key:
        return "I can define common air-quality terms such as PM2.5, PM10, NO₂, ozone, CO₂, VOC, AQI, correlation, or the WHO guidelines.", []
    item = GLOSSARY[best_key]
    text = item["technical"] if technical else item["simple"]
    return f"{item['name']}: {text}", [f"Technical note: {item['technical']}"] if not technical else []


def _answer_help() -> tuple[str, list[str]]:
    answer = (
        "I can answer questions about your active dataset, explain charts and analysis results, and define air-quality terms. "
        "Try asking: 'Summarise the dataset', 'What is the average PM2.5?', 'Are there any trends?', "
        "'Show the strongest correlations', 'How many readings exceed WHO guidelines?', or 'Explain the current plot'."
    )
    bullets = [
        "Ask about any column, e.g. PM2.5, PM10, NO2, temperature, or RH.",
        "Ask for a simple or technical explanation at any time.",
        "Use the Plot and Analysis tabs first, then ask me to interpret the result.",
    ]
    return answer, bullets


def _answer_values(df: pd.DataFrame, question: str, technical: bool) -> tuple[str, list[str], list[str]]:
    columns = [c for c in find_columns_in_text(df, question) if c in df.columns]
    if not columns:
        text_cols = [str(c) for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        if not text_cols:
            return "This dataset has no categorical columns to count.", [], []
        column = text_cols[0]
    else:
        column = columns[0]
    counts = df[column].astype("string").fillna("(missing)").value_counts().head(10)
    bullets = [f"{value}: {count:,}" for value, count in counts.items()]
    answer = (
        f"The most common values in {column} are: "
        + ", ".join(f"{value} ({count:,})" for value, count in counts.head(5).items())
        + "."
    )
    if technical:
        answer += f" The field has {df[column].nunique(dropna=True):,} distinct non-missing values."
    return answer, bullets, [column]


def suggestions_for(df: Optional[pd.DataFrame], session: Optional[dict] = None) -> list[str]:
    suggestions = [
        "Summarise this dataset",
        "What is the average PM2.5?",
        "Are there any significant trends?",
        "Show the strongest correlations",
        "How many readings exceed WHO guidelines?",
        "Explain this chart in simple language",
    ]
    if df is not None and not df.empty:
        primary = primary_column(df)
        if primary:
            suggestions.insert(1, f"What is the highest {primary} reading?")
        if "location" in df.columns or "_station" in df.columns:
            suggestions.append("Compare the stations")
        if session and session.get("last_analysis"):
            suggestions.insert(0, "Explain the last analysis")
        if session and session.get("last_plot"):
            suggestions.insert(0, "Explain the current plot")
    return suggestions[:8]


def _detect_intent(question: str, df: pd.DataFrame) -> str:
    low = str(question).lower().strip()
    if not low:
        return "help"
    if re.fullmatch(r"(hi|hello|hey|good (morning|afternoon|evening)|greetings)[!. ]*", low):
        return "greeting"
    if any(word in low for word in ("what can you do", "how do i use", "how to use", "help me", "commands", "what can i ask")):
        return "help"
    if any(word in low for word in ("explain the last analysis", "explain analysis", "interpret the result", "last result", "what does the analysis mean")):
        return "analysis"
    if any(word in low for word in ("explain the plot", "explain this chart", "interpret the chart", "current plot", "this figure", "this graph", "interpret the plot")):
        return "plot"
    if re.search(r"\bwhat (is|are|does) (a |an )?(pm|no|o3|so2|co2|co|voc|aqi|who|epa|rh|correlation|anomaly)", low):
        return "glossary"
    if re.search(r"\b(definition|meaning|define|what does .* mean)", low) and any(key in low for key in GLOSSARY):
        return "glossary"
    if any(word in low for word in ("summarise", "summarize", "overview", "describe the dataset", "tell me about the data", "what is this data")):
        return "overview"
    if any(word in low for word in ("which columns", "what columns", "list columns", "fields", "variables")):
        return "columns"
    if any(word in low for word in ("missing", "gap", "null", "nan", "blank")):
        return "missing"
    if any(word in low for word in ("quality", "reliable", "clean is", "trust", "valid")):
        return "quality"
    if any(word in low for word in ("time range", "date range", "period", "how long", "start date", "end date", "coverage")):
        return "timerange"
    if any(word in low for word in ("trend", "increasing", "decreasing", "rising", "falling", "going up", "going down", "slope")):
        return "trend"
    if any(word in low for word in ("anomal", "outlier", "spike", "unusual", "extreme reading")):
        return "anomalies"
    if any(word in low for word in ("correlat", "relationship", "related", "associated", "depend")):
        return "correlation"
    if any(word in low for word in ("exceed", "guideline", "who standard", "epa standard", "compliance", "limit")):
        return "exceedance"
    if any(word in low for word in ("station", "location", "site", "compare")):
        return "stations"
    if any(word in low for word in ("highest", "maximum", "max ", "peak", "lowest", "minimum", "min ", "smallest")):
        return "extremes"
    if any(word in low for word in ("how many", "count", "value counts", "categories", "distribution of")):
        return "values"
    if any(word in low for word in ("average", "mean", "median", "p95", "percentile", "summary", "statistic", "describe", "standard deviation", "std", "range")):
        return "stats"
    if any(word in low for word in ("insight", "key finding", "what stands out", "anything important", "what should i know")):
        return "insights"
    # A bare column name or "pm2.5" is most usefully treated as a summary request.
    if _pick_columns(df, low):
        return "stats"
    return "unsupported"


def answer_question(question: str, df: pd.DataFrame, *, session: Optional[dict] = None,
                    profile: Optional[dict] = None, mode: Optional[str] = None,
                    max_bullets: int = 8) -> dict:
    """Answer a free-text question with a deterministic, data-grounded reply."""
    question = str(question or "").strip()
    chosen_mode = _mode_from_text(question, mode)
    technical = chosen_mode == "technical"
    session = session or {}
    profile = profile or dataset_profile(df)

    intent = _detect_intent(question, df)
    bullets: list[str] = []
    columns: list[str] = []
    extra: dict[str, Any] = {}

    try:
        if intent == "greeting":
            answer = "Hello. I can help you understand this air-quality dataset, interpret charts, and explain analysis results. What would you like to know?"
        elif intent == "help":
            answer, bullets = _answer_help()
        elif intent == "overview":
            answer, bullets = _answer_overview(df, profile, technical)
        elif intent == "columns":
            answer, bullets = _answer_columns(df, technical)
        elif intent == "missing":
            answer, bullets = _answer_missing(df, profile, technical)
        elif intent == "quality":
            answer, bullets = _answer_quality(profile, technical)
        elif intent == "timerange":
            answer, bullets = _answer_timerange(profile, technical)
        elif intent == "trend":
            answer, bullets, columns = _answer_trend(df, question, profile, technical)
        elif intent == "anomalies":
            answer, bullets, columns = _answer_anomalies(df, question, profile, technical)
        elif intent == "correlation":
            answer, bullets, columns = _answer_correlation(df, question, technical)
        elif intent == "exceedance":
            answer, bullets, columns = _answer_exceedance(df, question, profile, technical)
        elif intent == "stations":
            answer, bullets = _answer_stations(df, profile, technical)
        elif intent == "glossary":
            answer, bullets = _answer_glossary(question, technical)
        elif intent == "values":
            answer, bullets, columns = _answer_values(df, question, technical)
        elif intent == "extremes":
            answer, bullets, columns = _answer_extremes(df, question, technical)
        elif intent == "stats":
            answer, bullets, columns = _answer_stats(df, question, technical)
        elif intent == "plot":
            last_plot = session.get("last_plot")
            if not last_plot:
                answer = "Generate a plot first, then ask me to interpret it. I can explain the chart type, what to look for, and the main caveats."
            else:
                description = describe_plot(
                    last_plot.get("plot_type", "chart"),
                    params=last_plot.get("params"),
                    profile=profile,
                )
                answer = description["technical"] if technical else description["simple"]
                bullets = [f"Look for: {item}" for item in description.get("what_to_look_for", [])[:4]]
                bullets.append(f"Caveat: {description.get('caveat')}")
                extra["plot"] = description
        elif intent == "analysis":
            last = session.get("last_analysis")
            if not last:
                answer = "Run an analysis (Extreme, Compliance, Machine Learning, Model Evaluation, or Comparative) and I will explain the result."
            else:
                explanation = explain_analysis(
                    last.get("type"),
                    last.get("result") or {},
                    params=last.get("params"),
                )
                answer = explanation["technical"] if technical else explanation["simple"]
                bullets = [f"{f['title']}: {f['text']}" for f in explanation.get("findings", [])[:6]]
                bullets += [f"Caveat: {c}" for c in explanation.get("caveats", [])[:2]]
                extra["analysis"] = explanation
        elif intent == "insights":
            items = dataset_insights(df, profile=profile)
            answer = "Here is what stands out most in this dataset. " + " ".join(i["technical" if technical else "simple"] for i in items[:3])
            bullets = [f"{i['title']}: {i['technical' if technical else 'simple']}" for i in items[:6]]
            extra["insights"] = items
        else:
            primary = primary_column(df)
            context = (
                f" For context, the dataset has {df.shape[0]:,} rows and the main measurement is {primary}."
                if primary else f" For context, the dataset has {df.shape[0]:,} rows."
            )
            answer = (
                "I could not map that question to a supported analysis. I can summarise the dataset, describe columns, "
                "find trends and anomalies, check guideline exceedances, compare stations, and explain charts or analysis results."
                + context
            )
            bullets = suggestions_for(df, session)[:5]
    except Exception as exc:  # noqa: BLE001 - assistant must never break the UI
        answer = (
            "I could not complete that answer because of an unexpected data condition. "
            "Try a simpler question, or check that the column contains numeric readings."
        )
        extra["error"] = str(exc)

    return {
        "answer": answer,
        "mode": chosen_mode,
        "intent": intent,
        "intent_label": INTENT_LABELS.get(intent, "Answer"),
        "confidence": 0.95 if intent not in {"unsupported"} else 0.4,
        "bullets": bullets[:max_bullets],
        "columns": columns,
        "suggestions": suggestions_for(df, session)[:6],
        "references": {
            "profile_shape": profile.get("shape"),
            "primary": (profile.get("primary") or {}).get("column"),
        },
        **extra,
    }
