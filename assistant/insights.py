"""Offline insight generation and scientific interpretation.

Everything in this module is deterministic: text is assembled from the numbers
actually present in the uploaded dataset.  No network calls, no model weights,
and no invented observations.  The same functions power the dashboard insight
cards, the assistant's dataset answers, and the plot/analysis explainers.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from assistant.profiling import (
    dataset_profile,
    exceedance_stats,
    find_datetime_column,
    human_interval,
    primary_column,
    resolve_column,
    series_summary,
    unit_for,
)

SEVERITY_WEIGHT = {"critical": 4, "warning": 3, "info": 2, "positive": 1}


# ── formatting helpers ────────────────────────────────────────────────────────
def fmt(value: Any, digits: int = 2) -> str:
    """Format a number for human-facing prose without false precision."""
    if value is None:
        return "not available"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "not available"
    if abs(number) >= 1000:
        return f"{number:,.{digits}f}"
    if abs(number) >= 100:
        return f"{number:.{max(0, digits - 1)}f}"
    return f"{number:.{digits}f}"


def _unit(column: Optional[str]) -> str:
    unit = unit_for(column or "")
    return f" {unit}" if unit else ""


def _pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "an unknown share"
    return f"{fmt(value, digits)}%"


def _direction_phrase(direction: str) -> str:
    return {
        "increasing": "rising",
        "decreasing": "falling",
        "stable": "broadly stable",
        "insufficient data": "not yet assessable",
    }.get(direction, "not yet assessable")


def _severity_for_quality(score: Optional[float]) -> str:
    if score is None:
        return "info"
    if score < 50:
        return "critical"
    if score < 70:
        return "warning"
    if score < 85:
        return "info"
    return "positive"


def _make_insight(kind: str, severity: str, title: str, simple: str,
                  technical: str, evidence: Optional[dict] = None,
                  action: Optional[str] = None) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "simple": simple,
        "technical": technical,
        "evidence": evidence or {},
        "action": action,
    }


# ── dataset insights ─────────────────────────────────────────────────────────
def dataset_insights(df: pd.DataFrame, *, profile: Optional[dict] = None,
                     max_items: int = 9) -> list[dict]:
    """Return prioritised, data-backed insights about the active dataset."""
    if df is None or df.empty:
        return []
    profile = profile or dataset_profile(df)

    insights: list[dict] = []
    rows, cols = profile.get("shape", [0, 0])
    start, end = profile.get("date_start"), profile.get("date_end")
    duration = profile.get("duration_days")

    period = ""
    if start and end:
        period = f" covering {str(start)[:10]} to {str(end)[:10]}"
    insights.append(_make_insight(
        "overview", "info", "Dataset at a glance",
        f"The dataset has {rows:,} rows and {cols} columns{period}.",
        (
            f"n = {rows:,} observations across {cols} fields. "
            f"Temporal extent {start or 'unknown'} → {end or 'unknown'}; "
            f"median sampling interval {profile.get('sampling_interval_label', 'unknown')}; "
            f"estimated timestamp coverage {_pct(profile.get('coverage_pct'))}."
        ),
        evidence={"rows": rows, "columns": cols, "date_start": start, "date_end": end},
        action="Use Explore for descriptive statistics and Plots for visual screening.",
    ))

    quality = profile.get("quality") or {}
    if quality:
        insights.append(_make_insight(
            "quality", _severity_for_quality(quality.get("score")),
            f"Data quality grade {quality.get('grade', '?')}",
            (
                f"Overall data quality is {quality.get('score', '?')} out of 100 "
                f"(grade {quality.get('grade', '?')}). "
                + (" ".join(quality.get("issues", [])[:2]))
            ),
            (
                f"Composite quality score {quality.get('score')} with components "
                f"{quality.get('components', {})}. "
                + " ".join(quality.get("issues", []))
            ),
            evidence=quality,
            action=(
                "Review missing values and duplicated timestamps before formal reporting."
                if (quality.get("score") or 100) < 85 else
                "Quality is suitable for screening analysis; retain the original export as evidence."
            ),
        ))

    primary = (profile.get("primary") or {}).get("column")
    summary = (profile.get("primary") or {}).get("summary") or {}
    if primary and summary:
        unit = _unit(primary)
        insights.append(_make_insight(
            "summary", "info", f"{primary} concentration summary",
            (
                f"{primary} averages {fmt(summary.get('mean'))}{unit}, with a median of "
                f"{fmt(summary.get('median'))}{unit}. Typical high values (95th percentile) "
                f"reach {fmt(summary.get('p95'))}{unit}, and the maximum is "
                f"{fmt(summary.get('max'))}{unit}."
            ),
            (
                f"{primary}: n={summary.get('valid')}, mean={fmt(summary.get('mean'), 4)}, "
                f"median={fmt(summary.get('median'), 4)}, SD={fmt(summary.get('std'), 4)}, "
                f"p95={fmt(summary.get('p95'), 4)}, p99={fmt(summary.get('p99'), 4)}, "
                f"CV={fmt(summary.get('cv_pct'), 2)}%, skewness={fmt(summary.get('skewness'), 3)}."
            ),
            evidence={"column": primary, "summary": summary, "unit": unit.strip()},
            action="Compare these values with the applicable WHO or national guideline.",
        ))

        trend = summary.get("trend") or {}
        if trend.get("direction") in {"increasing", "decreasing", "stable"}:
            p_value = trend.get("p_value")
            significant = p_value is not None and p_value < 0.05
            change = trend.get("change_pct_per_year")
            severity = "warning" if trend.get("direction") == "increasing" and significant else "info"
            qualifier = "a statistically significant" if significant else "no statistically significant"
            insights.append(_make_insight(
                "trend", severity, f"{primary} trend",
                (
                    f"{primary} is {_direction_phrase(trend.get('direction'))} over the record"
                    + (f", changing by about {fmt(change)}% per year" if change is not None else "")
                    + f". This is {qualifier} linear trend."
                ),
                (
                    f"Ordinary least-squares slope={fmt(trend.get('slope_per_day'), 6)} "
                    f"per day, p={fmt(p_value, 4) if p_value is not None else 'not available'}, "
                    f"R²={fmt(trend.get('r_squared'), 3)}. Significance assessed at α=0.05."
                ),
                evidence=trend,
                action=(
                    "Investigate emission sources, seasonality, and instrument drift before attributing the change."
                    if severity == "warning" else
                    "A longer record improves confidence in trend detection."
                ),
            ))

    # Seasonality / diurnal rhythm.
    groups = profile.get("groups") or {}
    diurnal = groups.get("diurnal") or {}
    if diurnal.get("peak") is not None:
        insights.append(_make_insight(
            "diurnal", "info", "Daily rhythm",
            (
                f"Concentrations tend to peak around {diurnal.get('peak')}:00 and are lowest "
                f"around {diurnal.get('low')}:00, with a peak-to-low ratio of about "
                f"{fmt(diurnal.get('peak_to_low_ratio'))}."
            ),
            (
                f"Hour-of-day group means: peak {diurnal.get('peak')} = "
                f"{fmt(diurnal.get('peak_value'), 3)}{_unit(primary)}, low {diurnal.get('low')} = "
                f"{fmt(diurnal.get('low_value'), 3)}{_unit(primary)}. "
                f"Counts per hour are retained in the profile payload."
            ),
            evidence=diurnal,
            action="Check whether the peak aligns with traffic, cooking, or boundary-layer cycles.",
        ))

    monthly = groups.get("monthly") or {}
    if monthly.get("peak") is not None:
        insights.append(_make_insight(
            "seasonal", "info", "Seasonal pattern",
            (
                f"The highest monthly mean occurs in {monthly.get('peak')} "
                f"({fmt(monthly.get('peak_value'))}{_unit(primary)}) and the lowest in "
                f"{monthly.get('low')} ({fmt(monthly.get('low_value'))}{_unit(primary)})."
            ),
            (
                f"Monthly arithmetic means: {monthly.get('values', {})}. "
                f"Seasonal means: {(groups.get('seasonal') or {}).get('values', {})}."
            ),
            evidence=monthly,
            action="Seasonal contrast can reflect meteorology, heating, dust, or agricultural burning.",
        ))

    anomalies = profile.get("anomalies") or {}
    if anomalies.get("count"):
        insights.append(_make_insight(
            "anomaly",
            "warning" if (anomalies.get("pct") or 0) > 1 else "info",
            "Unusual readings detected",
            (
                f"{anomalies.get('count')} readings ({_pct(anomalies.get('pct'), 2)}) are "
                f"statistically unusual relative to the rest of the record."
            ),
            (
                f"Robust median/MAD detection at |z| ≥ {anomalies.get('threshold')}: "
                f"{anomalies.get('count')} readings. Robust centre={fmt(anomalies.get('median'), 3)}, "
                f"scale={fmt(anomalies.get('scale'), 3)}."
            ),
            evidence=anomalies,
            action="Inspect the timestamps for sensor faults, dust events, or genuine pollution episodes.",
        ))

    correlations = profile.get("correlations") or []
    if correlations:
        top = correlations[0]
        strength = "strong" if abs(top.get("r", 0)) >= 0.7 else "moderate"
        others = ", ".join(
            f"{p['a']}–{p['b']} ({fmt(p['r'])})" for p in correlations[1:4]
        ) or "none"
        insights.append(_make_insight(
            "correlation", "info", "Strongest relationship",
            (
                f"{top.get('a')} and {top.get('b')} move together {strength}ly "
                f"(correlation {fmt(top.get('r'))}). Correlation is not proof of cause."
            ),
            (
                f"Pearson r({top.get('a')}, {top.get('b')}) = {fmt(top.get('r'), 4)}. "
                f"Other notables: {others}."
            ),
            evidence={"pairs": correlations[:5]},
            action="Use scatter plots to check linearity and the influence of outliers.",
        ))

    exceedances = [e for e in (profile.get("exceedances") or []) if (e.get("pct") or 0) > 0]
    if exceedances:
        worst = max(exceedances, key=lambda e: e.get("pct") or 0)
        insights.append(_make_insight(
            "exceedance", "warning" if (worst.get("pct") or 0) > 5 else "info",
            "Guideline exceedances",
            (
                f"{worst.get('authority')} {worst.get('period')} screening shows exceedances in "
                f"{_pct(worst.get('pct'), 2)} of {worst.get('basis')} "
                f"(threshold {fmt(worst.get('threshold'))}{_unit(primary)})."
            ),
            (
                f"Exceedance basis: {worst.get('authority')} {worst.get('period')} threshold "
                f"{worst.get('threshold')} {worst.get('unit')}; {worst.get('count')} exceedances; "
                f"peak {fmt(worst.get('peak'), 3)}."
            ),
            evidence={"exceedances": exceedances},
            action="Confirm the legally applicable averaging period and instrument calibration before reporting.",
        ))

    stations = (profile.get("stations") or {}).get("stations") or []
    if len(stations) > 1:
        best, worst_station = stations[0], stations[-1]
        station_means = ", ".join(f"{s['name']}={fmt(s['mean'], 3)}" for s in stations[:8])
        insights.append(_make_insight(
            "stations", "info", "Station contrast",
            (
                f"{best.get('name')} records the highest mean "
                f"({fmt(best.get('mean'))}{_unit(primary)}) and {worst_station.get('name')} the lowest "
                f"({fmt(worst_station.get('mean'))}{_unit(primary)})."
            ),
            (
                f"Station means: {station_means}."
            ),
            evidence={"stations": stations},
            action="Run the Multi-Station workspace for significance testing and aligned time series.",
        ))

    insights.sort(key=lambda item: SEVERITY_WEIGHT.get(item["severity"], 0), reverse=True)
    return insights[:max_items]


# ── plot descriptions ────────────────────────────────────────────────────────
PLOT_GUIDES: dict[str, dict[str, Any]] = {
    "timeseries": {
        "title": "Time series",
        "purpose": "track how a measurement changes through time",
        "look_for": ["long-term direction", "repeated daily or seasonal cycles", "isolated spikes", "gaps in the record"],
        "caveat": "Gaps and irregular sampling can make a line look smoother or more volatile than reality.",
    },
    "area": {
        "title": "Area chart",
        "purpose": "show the accumulated magnitude of one or more measurements over time",
        "look_for": ["periods of sustained elevation", "the relative contribution of each series", "step changes"],
        "caveat": "Overlapping areas can hide the smaller series; check trace order.",
    },
    "calendar": {
        "title": "Calendar heatmap",
        "purpose": "reveal day-to-day structure and seasonal blocks",
        "look_for": ["runs of high days", "weekday/weekend contrast", "seasonal bands", "missing days"],
        "caveat": "Colour scales are sensitive to the chosen range and ramp; compare with the histogram.",
    },
    "boxplot": {
        "title": "Box plot",
        "purpose": "compare distributions and spot unusual values across groups",
        "look_for": ["differences in medians", "spread (box height)", "long whiskers or separate points", "outliers"],
        "caveat": "Box plots hide sample size and multi-modality; pair with a histogram or violin.",
    },
    "violin": {
        "title": "Violin plot",
        "purpose": "show the full shape of a distribution across groups",
        "look_for": ["multiple peaks", "skew", "differences in spread", "small groups with misleading shapes"],
        "caveat": "Kernel smoothing can create apparent structure in short or sparse groups.",
    },
    "histogram": {
        "title": "Histogram",
        "purpose": "show how often each concentration range occurs",
        "look_for": ["the most common range", "skew toward high values", "a second peak", "long upper tail"],
        "caveat": "Bin width changes the shape; confirm important features with a different bin count.",
    },
    "seasonal_box": {
        "title": "Seasonal box plot",
        "purpose": "compare distributions between seasons",
        "look_for": ["seasonal medians", "winter/summer spread", "outlier seasons", "small seasonal samples"],
        "caveat": "Seasons with few observations should be interpreted cautiously.",
    },
    "diurnal": {
        "title": "Diurnal profile",
        "purpose": "show the average daily cycle",
        "look_for": ["morning/evening peaks", "midday dips", "traffic-like patterns", "flat profiles"],
        "caveat": "Averaging can cancel opposing patterns on weekdays and weekends.",
    },
    "monthly": {
        "title": "Monthly profile",
        "purpose": "compare average conditions month by month",
        "look_for": ["peak months", "low months", "monotonic change", "unusual months"],
        "caveat": "Incomplete months can bias the comparison; check observation counts.",
    },
    "time_of_week": {
        "title": "Time-of-week heatmap",
        "purpose": "reveal weekly rhythms by hour and weekday",
        "look_for": ["weekday rush-hour bands", "weekend shifts", "night-time baseline", "missing cells"],
        "caveat": "Timezone handling and DST transitions can shift the pattern.",
    },
    "scatter": {
        "title": "Scatter plot with regression",
        "purpose": "examine the relationship between two measurements",
        "look_for": ["direction and tightness", "curvature", "clusters", "influential outliers"],
        "caveat": "A fitted line assumes a roughly linear relationship and is sensitive to outliers.",
    },
    "bubble": {
        "title": "Bubble chart",
        "purpose": "show three variables at once",
        "look_for": ["relationship between x and y", "whether size explains deviations", "overplotting"],
        "caveat": "Bubble area is harder to judge than length; treat the third variable qualitatively.",
    },
    "correlation": {
        "title": "Correlation matrix",
        "purpose": "screen for linear relationships among numeric fields",
        "look_for": ["strong red/blue cells", "blocks of related pollutants", "counter-intuitive signs"],
        "caveat": "Correlation measures linear association only and can be driven by shared trends.",
    },
    "windrose": {
        "title": "Wind rose",
        "purpose": "relate pollutant concentrations to wind direction",
        "look_for": ["dominant direction", "high-concentration sectors", "calm conditions", "source direction"],
        "caveat": "Local topography and sensor siting can distort the apparent direction.",
    },
    "aqi_bar": {
        "title": "AQI category bar",
        "purpose": "count readings in each air-quality category",
        "look_for": ["share of unhealthy categories", "dominant category", "rare hazardous readings"],
        "caveat": "AQI breakpoints depend on the pollutant and the adopted national scheme.",
    },
    "pm_size_dist": {
        "title": "PM size distribution",
        "purpose": "compare particulate mass and number fractions",
        "look_for": ["dominant size fraction", "coarse vs fine balance", "unexpected number concentrations"],
        "caveat": "Mass and number fractions have different units and should not be compared directly.",
    },
    "nc_timeseries": {
        "title": "Particle number concentration",
        "purpose": "track number concentrations across size bins",
        "look_for": ["simultaneous spikes", "shifts in the smallest bin", "instrument saturation"],
        "caveat": "Optical particle counters lose sensitivity above their upper size limit.",
    },
    "co2_met": {
        "title": "CO₂ and meteorology",
        "purpose": "compare CO₂ with temperature, humidity, and VOC",
        "look_for": ["occupancy-related CO₂ rises", "ventilation drops", "correlation with humidity"],
        "caveat": "Indoor CO₂ reflects occupancy and ventilation, not outdoor air quality.",
    },
    "radar": {
        "title": "Multi-pollutant radar",
        "purpose": "compare several pollutants on a normalised scale",
        "look_for": ["which axis dominates", "balanced vs spiky profiles", "station-to-station shape"],
        "caveat": "Normalisation choices determine the visual ranking.",
    },
    "exceedance": {
        "title": "Exceedance frequency curve",
        "purpose": "show how often each concentration level is exceeded",
        "look_for": ["steep drop at low concentrations", "persistent high-concentration tail", "guideline crossings"],
        "caveat": "The curve is only as reliable as the record length and completeness.",
    },
    "spatial": {
        "title": "Spatial heatmap",
        "purpose": "show the geographic distribution of a measurement",
        "look_for": ["hotspots", "gradients", "isolated points", "coordinate problems"],
        "caveat": "Point colours are not a dispersion model; nearby points may not be independent.",
    },
}


def _figure_meta(figure: Optional[dict]) -> dict:
    """Extract lightweight descriptive metadata from a Plotly figure dict."""
    if not figure or not isinstance(figure, dict):
        return {}
    data = figure.get("data") or []
    layout = figure.get("layout") or {}
    trace_types = [str(t.get("type", "scatter")) for t in data if isinstance(t, dict)]
    names = [str(t.get("name")) for t in data if isinstance(t, dict) and t.get("name")]
    x_title = ((layout.get("xaxis") or {}).get("title") or {})
    y_title = ((layout.get("yaxis") or {}).get("title") or {})
    if isinstance(x_title, dict):
        x_title = x_title.get("text")
    if isinstance(y_title, dict):
        y_title = y_title.get("text")
    return {
        "traces": len(data),
        "trace_types": trace_types,
        "series_names": names[:12],
        "title": layout.get("title", {}).get("text") if isinstance(layout.get("title"), dict) else layout.get("title"),
        "x_title": x_title,
        "y_title": y_title,
    }


def describe_plot(plot_type: str, *, params: Optional[dict] = None,
                  figure: Optional[dict] = None, profile: Optional[dict] = None,
                  column: Optional[str] = None) -> dict:
    """Build a plain-language and technical description of the current chart."""
    guide = PLOT_GUIDES.get(str(plot_type), {
        "title": str(plot_type or "Chart").replace("_", " ").title(),
        "purpose": "visualise the selected measurements",
        "look_for": ["the overall pattern", "unusual points", "differences between groups"],
        "caveat": "Check the axes, units, and sample size before drawing conclusions.",
    })
    meta = _figure_meta(figure)
    params = params or {}
    column = column or params.get("column") or (
        (profile or {}).get("primary") or {}
    ).get("column")
    unit = _unit(column)

    details: list[str] = []
    if meta.get("traces"):
        details.append(f"{meta['traces']} data trace(s)")
    if meta.get("series_names"):
        details.append("series: " + ", ".join(meta["series_names"]))
    if meta.get("x_title") or meta.get("y_title"):
        details.append(
            "axes: " + ", ".join(str(x) for x in [meta.get("x_title"), meta.get("y_title")] if x)
        )
    if params.get("group_by"):
        details.append(f"grouped by {params['group_by']}")
    if params.get("columns"):
        details.append("columns: " + ", ".join(str(c) for c in params["columns"][:8]))
    if params.get("color_col"):
        details.append(f"coloured by {params['color_col']}")
    if params.get("facet_col"):
        details.append(f"faceted by {params['facet_col']}")

    title = params.get("title") or meta.get("title") or guide["title"]
    simple = (
        f"This {guide['title'].lower()} is used to {guide['purpose']}. "
        + (f"It contains {meta['traces']} plotted series. " if meta.get("traces") else "")
        + "Look for " + ", ".join(guide["look_for"][:3]) + "."
    )
    technical = (
        f"Chart type: {plot_type}; {('; '.join(details) if details else 'single-series default rendering')}. "
        f"Primary variable: {column or 'not specified'}{unit}. "
        f"Interpretation is descriptive; no causal inference is implied."
    )
    caption = (
        f"Figure. {title}. {guide['title']} of {column or 'the selected measurements'}"
        + (f" ({', '.join(meta['series_names'][:4])})" if meta.get("series_names") else "")
        + f". The chart is intended to {guide['purpose']}."
    )
    return {
        "plot_type": plot_type,
        "title": title,
        "caption": caption,
        "summary": simple,
        "simple": simple,
        "technical": technical,
        "purpose": guide["purpose"],
        "what_to_look_for": guide["look_for"],
        "caveat": guide["caveat"],
        "meta": meta,
        "suggested_questions": [
            "What is the overall pattern in this chart?",
            "Are there any anomalies or unusual periods?",
            "How does this compare with WHO guidelines?",
            "Explain this chart in simple language.",
        ],
    }


# ── analysis explainers ──────────────────────────────────────────────────────
def _finding(title: str, text: str) -> dict:
    return {"title": title, "text": text}


def explain_analysis(kind: str, result: dict, *, params: Optional[dict] = None,
                     column: Optional[str] = None) -> dict:
    """Explain an analysis payload produced by the analysis/ modules."""
    kind = str(kind or "").lower()
    params = params or {}
    result = result or {}
    column = column or result.get("column") or result.get("target") or params.get("column") or "the selected measurement"
    unit = _unit(column)
    findings: list[dict] = []
    caveats: list[str] = []

    if kind == "extreme":
        desc = result.get("descriptive") or {}
        findings.append(_finding(
            "Distribution",
            f"{column} has a mean of {fmt(desc.get('mean'))}{unit} and a median of "
            f"{fmt(desc.get('median'))}{unit}. The maximum is {fmt(desc.get('max'))}{unit}.",
        ))
        if desc.get("skewness") is not None:
            skew = float(desc.get("skewness") or 0)
            if skew > 1:
                findings.append(_finding("Skew", "The distribution is strongly right-skewed: most readings are low, with a long high-concentration tail."))
            elif skew < -1:
                findings.append(_finding("Skew", "The distribution is left-skewed: a tail of unusually low readings pulls the mean down."))
            else:
                findings.append(_finding("Skew", "The distribution is roughly symmetric."))
        for authority in ("who", "epa"):
            block = result.get(authority) or {}
            if block.get("24h_standard") is not None:
                findings.append(_finding(
                    f"{authority.upper()} 24-hour screening",
                    f"{block.get('24h_exceed_n', 0)} daily means exceeded "
                    f"{block.get('24h_standard')}{unit} "
                    f"({_pct(block.get('24h_exceed_pct'), 2)} of days).",
                ))
            if block.get("annual_exceed") is not None:
                findings.append(_finding(
                    f"{authority.upper()} annual screening",
                    f"Annual mean {fmt(block.get('annual_mean'))}{unit} is "
                    + ("above" if block.get("annual_exceed") else "below")
                    + f" the {block.get('annual_standard')}{unit} guideline.",
                ))
        block_max = result.get("block_maxima") or {}
        if block_max.get("max_of_maxima") is not None:
            findings.append(_finding(
                "Block maxima",
                f"The largest block maximum is {fmt(block_max.get('max_of_maxima'))}{unit}; "
                f"the average block maximum is {fmt(block_max.get('mean_of_maxima'))}{unit}.",
            ))
        caveats.append("Extreme-value estimates from short records are uncertain; treat long return periods as indicative.")

    elif kind == "compliance":
        findings.append(_finding(
            "Coverage",
            f"{result.get('readings', 0):,} valid readings from "
            f"{str(result.get('first_reading'))[:10]} to {str(result.get('last_reading'))[:10]}; "
            f"{_pct(result.get('missing_percent'), 2)} missing.",
        ))
        for item in result.get("standards", []):
            findings.append(_finding(
                f"{item.get('authority')} {item.get('period')}",
                f"{item.get('exceedance_count', 0)} readings exceeded {item.get('threshold')}{unit} "
                f"({_pct(item.get('exceedance_percent'), 2)}), forming "
                f"{item.get('episode_count', 0)} episode(s).",
            ))
        for rec in result.get("recommendations", [])[:3]:
            findings.append(_finding(rec.get("title", "Recommendation"), rec.get("text", "")))
        caveats.append("Compliance output is screening evidence, not a legal determination.")

    elif kind == "machine_learning":
        model = result.get("model", "model")
        findings.append(_finding("Model", f"{str(model).replace('_', ' ').title()} on {column}; {result.get('readings', 0):,} readings used."))
        if result.get("slope_per_day") is not None:
            findings.append(_finding(
                "Trend",
                f"Estimated change is {fmt(result.get('slope_per_day'), 5)}{unit} per day.",
            ))
        if result.get("holdout"):
            hold = result["holdout"]
            findings.append(_finding(
                "Holdout accuracy",
                f"RMSE {fmt(hold.get('rmse'), 3)}, MAE {fmt(hold.get('mae'), 3)}, "
                f"bias {fmt(hold.get('bias'), 3)} on {hold.get('n')} held-out readings.",
            ))
        if result.get("metrics"):
            metric = result["metrics"]
            findings.append(_finding(
                "Baseline accuracy",
                f"RMSE {fmt(metric.get('rmse'), 3)} and MAE {fmt(metric.get('mae'), 3)}.",
            ))
        if result.get("feature_importance"):
            ranked = sorted(result["feature_importance"].items(), key=lambda kv: kv[1], reverse=True)[:4]
            findings.append(_finding("Drivers", "Most influential features: " + ", ".join(f"{k} ({fmt(v, 3)})" for k, v in ranked) + "."))
        if result.get("anomaly_count") is not None:
            findings.append(_finding("Anomalies", f"{result.get('anomaly_count')} unusual readings were flagged at robust z ≥ {result.get('threshold')}."))
        if result.get("hourly_baseline"):
            baseline = result["hourly_baseline"]
            peak = max(baseline, key=baseline.get)
            findings.append(_finding("Daily cycle", f"The highest hourly baseline is at {peak}:00 ({fmt(baseline[peak])}{unit})."))
        caveats.append("Model output is diagnostic; validate with independent data before using it for forecasting or enforcement.")

    elif kind == "model_eval":
        findings.append(_finding(
            "Agreement",
            f"Model evaluation uses {result.get('n', 0):,} paired readings. "
            f"R = {fmt(result.get('R'), 3)}, R² = {fmt(result.get('R2'), 3)}, "
            f"IOA = {fmt(result.get('IOA'), 3)}.",
        ))
        findings.append(_finding(
            "Error",
            f"MB = {fmt(result.get('MB'), 3)}, RMSE = {fmt(result.get('RMSE'), 3)}, "
            f"NRMSE = {fmt(result.get('NRMSE'), 1)}%, PBIAS = {fmt(result.get('PBIAS'), 1)}%.",
        ))
        findings.append(_finding(
            "Regression",
            f"Fitted model = {fmt(result.get('slope'), 4)} × observed + {fmt(result.get('intercept'), 4)}.",
        ))
        caveats.append("Metrics such as R and IOA are sensitive to range, sample size, and outliers.")

    elif kind == "comparative":
        names = result.get("station_names") or []
        ranking = result.get("ranking") or []
        findings.append(_finding("Stations", f"{len(names)} station(s) compared on {column}."))
        if ranking:
            findings.append(_finding(
                "Ranking",
                "; ".join(f"#{r.get('rank')} {r.get('station')} ({fmt(r.get('mean'))}{unit})" for r in ranking[:6]),
            ))
        anova = result.get("anova") or {}
        if anova.get("p_value") is not None:
            findings.append(_finding(
                "ANOVA",
                f"F = {fmt(anova.get('F_statistic'), 3)}, p = {fmt(anova.get('p_value'), 4)}. "
                + ("Station means differ significantly." if anova.get("significant") else "No significant difference detected."),
            ))
        caveats.append("ANOVA assumes independent observations; autocorrelated time series can exaggerate significance.")

    elif kind == "stats":
        shape = result.get("shape") or []
        findings.append(_finding("Shape", f"{shape[0] if shape else 0:,} rows × {shape[1] if len(shape) > 1 else 0} columns."))
        missing = result.get("missing_pct") or {}
        worst = sorted(((k, v) for k, v in missing.items() if v), key=lambda kv: kv[1], reverse=True)[:5]
        if worst:
            findings.append(_finding("Missing values", ", ".join(f"{k} {fmt(v)}%" for k, v in worst)))
        else:
            findings.append(_finding("Missing values", "No missing values were detected."))

    else:
        findings.append(_finding("Result", "The analysis completed successfully. Inspect the metrics and charts for details."))

    simple = " ".join(f["text"] for f in findings[:3]) if findings else "The analysis completed successfully."
    technical = (
        f"Analysis type: {kind or 'unknown'}; target: {column}{unit}. "
        + " ".join(f"{f['title']}: {f['text']}" for f in findings)
    )
    return {
        "kind": kind,
        "column": column,
        "summary": simple,
        "simple": simple,
        "technical": technical,
        "findings": findings,
        "caveats": caveats,
    }
