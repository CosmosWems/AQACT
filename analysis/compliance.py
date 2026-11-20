"""Compliance monitoring and action guidance for sensor readings."""
import numpy as np
import pandas as pd

from analysis.extreme import EPA_STANDARDS, WHO_STANDARDS, _standard_with_overrides


def _matching_standard(column, standards, overrides):
    key = column.lower()
    if key not in standards:
        key = key.replace(".", "")
    if key not in standards:
        return {}
    return _standard_with_overrides(standards[key], overrides)


def _episodes(values, threshold, timestamps):
    exceed = values > threshold
    episodes = []
    start = None
    for index, is_exceeding in enumerate(exceed):
        if is_exceeding and start is None:
            start = index
        if start is not None and (not is_exceeding or index == len(exceed) - 1):
            end = index if is_exceeding and index == len(exceed) - 1 else index - 1
            segment = values.iloc[start:end + 1]
            episodes.append({
                "start": str(timestamps.iloc[start]),
                "end": str(timestamps.iloc[end]),
                "readings": int(len(segment)),
                "peak": round(float(segment.max()), 3),
                "mean": round(float(segment.mean()), 3),
            })
            start = None
    return episodes


def run_compliance_analysis(df, column, params):
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found.")
    if "datetime" not in df.columns:
        raise ValueError("A datetime column is required for compliance monitoring.")

    data = pd.DataFrame({
        "datetime": pd.to_datetime(df["datetime"], errors="coerce"),
        "value": pd.to_numeric(df[column], errors="coerce"),
    }).dropna().sort_values("datetime")
    if data.empty:
        raise ValueError("No valid timestamped readings found for compliance monitoring.")

    who = _matching_standard(column, WHO_STANDARDS, params.get("who_overrides"))
    epa = _matching_standard(column, EPA_STANDARDS, params.get("epa_overrides"))
    standards = []
    for label, values in [("WHO", who), ("Country's EPA", epa)]:
        for period, threshold in values.items():
            if period in {"24h", "8h", "1h", "annual"}:
                standards.append({"authority": label, "period": period, "threshold": threshold})

    values = data["value"]
    timestamps = data["datetime"]
    interval_hours = data["datetime"].diff().dt.total_seconds().median() / 3600
    interval_hours = float(interval_hours) if pd.notna(interval_hours) and interval_hours > 0 else None
    results = []
    for standard in standards:
        episodes = _episodes(values, standard["threshold"], timestamps)
        exceeded = values > standard["threshold"]
        hourly = data.set_index("datetime")["value"].resample("h").max().dropna()
        daily = data.set_index("datetime")["value"].resample("D").max().dropna()
        item = {**standard, "exceedance_count": int(exceeded.sum()),
                "exceedance_percent": round(float(exceeded.mean() * 100), 2),
            "episodes": episodes[:50], "episode_count": len(episodes),
            "hourly_exceedances": [{"datetime": str(timestamp), "value": round(float(value), 3),
                        "exceeded": bool(value > standard["threshold"])}
                           for timestamp, value in hourly.head(2000).items()],
            "daily_exceedances": [{"date": str(timestamp.date()), "value": round(float(value), 3),
                           "exceeded": bool(value > standard["threshold"])}
                          for timestamp, value in daily.head(1000).items()]}
        if interval_hours:
            item["estimated_exceedance_hours"] = round(float(exceeded.sum() * interval_hours), 2)
        results.append(item)

    missing = int(pd.to_numeric(df[column], errors="coerce").isna().sum())
    return {
        "column": column,
        "readings": int(len(data)),
        "first_reading": str(data["datetime"].iloc[0]),
        "last_reading": str(data["datetime"].iloc[-1]),
        "sampling_interval_hours": round(interval_hours, 3) if interval_hours else None,
        "missing_readings": missing,
        "missing_percent": round(missing / len(df) * 100, 2) if len(df) else 0,
        "standards": results,
        "recommendations": build_recommendations(results, missing / len(df) * 100 if len(df) else 0),
    }


def build_recommendations(results, missing_percent):
    recommendations = []
    exceedances = [item for item in results if item["exceedance_count"]]
    if exceedances:
        worst = max(exceedances, key=lambda item: item["exceedance_percent"])
        recommendations.append({
            "priority": "high",
            "title": "Verify and notify",
            "text": f"Readings exceeded the {worst['authority']} {worst['period']} guideline {worst['exceedance_percent']}% of the valid observations. Verify the instrument and notify the responsible environmental health authority according to local protocol.",
        })
        recommendations.append({
            "priority": "high",
            "title": "Protect exposed people",
            "text": "Issue a clear public-health advisory for sensitive groups, reduce avoidable emissions where possible, and document the start, peak, and end of each episode.",
        })
    else:
        recommendations.append({
            "priority": "low",
            "title": "Continue surveillance",
            "text": "No configured guideline exceedance was detected in the valid readings. Continue monitoring and review the thresholds against the applicable national protocol.",
        })
    if missing_percent > 10:
        recommendations.append({
            "priority": "medium",
            "title": "Check data completeness",
            "text": f"About {missing_percent:.1f}% of source readings are missing or invalid. Inspect power, connectivity, calibration, and time synchronization before making a formal compliance determination.",
        })
    recommendations.append({
        "priority": "medium",
        "title": "Confirm regulatory basis",
        "text": "Treat these results as screening evidence. A qualified authority should confirm averaging periods, units, siting, calibration, and the legally applicable standard before enforcement or publication.",
    })
    return recommendations
