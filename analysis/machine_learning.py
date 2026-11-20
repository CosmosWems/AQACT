"""Explainable sensor forecasting and anomaly models."""
import numpy as np
import pandas as pd


def _metrics(actual, predicted):
    error = predicted - actual
    return {
        "n": int(len(actual)),
        "mae": round(float(np.mean(np.abs(error))), 4),
        "rmse": round(float(np.sqrt(np.mean(error ** 2))), 4),
        "bias": round(float(np.mean(error)), 4),
    }


def run_machine_learning(df, target, model, params=None):
    params = params or {}
    if target not in df.columns:
        raise ValueError(f"Target column '{target}' not found.")
    if "datetime" not in df.columns:
        raise ValueError("A datetime column is required for machine learning analysis.")
    data = df.copy()
    data["datetime"] = pd.to_datetime(data["datetime"], errors="coerce")
    data[target] = pd.to_numeric(data[target], errors="coerce")
    data = data.dropna(subset=["datetime", target]).sort_values("datetime")
    if len(data) < 8:
        raise ValueError("At least 8 valid timestamped readings are required.")

    if model in {"random_forest", "svm", "xgboost", "gradient_boosting", "extra_trees", "linear_regression"}:
        result = _supervised(data, target, model, params)
    elif model == "trend":
        result = _trend(data, target)
    elif model == "seasonal":
        result = _seasonal(data[["datetime", target]].rename(columns={target: "value"}))
    elif model == "anomaly":
        result = _anomaly(data[["datetime", target]].rename(columns={target: "value"}), float(params.get("z_score", 3.0)))
    else:
        raise ValueError("Choose trend, seasonal, or anomaly analysis.")
    result.update({"target": target, "model": model, "readings": int(len(data))})
    return result


def _feature_frame(data, target, features):
    work = data.copy()
    work["hour"] = work["datetime"].dt.hour
    work["day_of_week"] = work["datetime"].dt.dayofweek
    work["day_of_year"] = work["datetime"].dt.dayofyear
    work["month"] = work["datetime"].dt.month
    numeric = work.select_dtypes(include="number").drop(columns=[target], errors="ignore")
    if features:
        selected = [name for name in features if name in numeric.columns]
        if selected:
            numeric = numeric[selected]
    if numeric.empty:
        numeric = pd.DataFrame({"time_index": np.arange(len(work), dtype=float)}, index=work.index)
    return numeric


def _supervised(data, target, model, params):
    try:
        from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LinearRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.svm import SVR
    except ImportError as exc:
        raise ValueError("Scikit-learn is unavailable. Run python run.py or install scikit-learn.") from exc
    features = params.get("features") or []
    x = _feature_frame(data, target, features)
    y = data[target].to_numpy(dtype=float)
    split = max(5, int(len(data) * 0.8))
    if split >= len(data):
        split = len(data) - 1
    models = {
        "linear_regression": LinearRegression(),
        "random_forest": RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1, min_samples_leaf=2),
        "svm": SVR(C=10, epsilon=0.1, kernel="rbf"),
        "gradient_boosting": GradientBoostingRegressor(random_state=42, n_estimators=100),
        "extra_trees": ExtraTreesRegressor(n_estimators=150, random_state=42, n_jobs=-1, min_samples_leaf=2),
    }
    if model == "xgboost":
        try:
            from xgboost import XGBRegressor
        except ImportError as exc:
            raise ValueError("XGBoost is unavailable. Install xgboost and restart the application.") from exc
        models[model] = XGBRegressor(n_estimators=150, max_depth=4, learning_rate=0.05,
                                     objective="reg:squarederror", random_state=42, n_jobs=2)
    estimator = make_pipeline(SimpleImputer(strategy="median"), models[model])
    estimator.fit(x.iloc[:split], y[:split])
    predicted = estimator.predict(x.iloc[split:])
    result = {"features": list(x.columns), "holdout": _metrics(y[split:], predicted),
              "holdout_series": [{"datetime": str(timestamp), "actual": round(float(actual), 4),
                                  "predicted": round(float(forecast), 4)}
                                 for timestamp, actual, forecast in zip(
                                     data["datetime"].iloc[split:].head(500), y[split:], predicted)],
              "message": f"{model.replace('_', ' ').title()} trained on the first 80% and evaluated on the final 20%."}
    fitted = estimator[-1]
    if hasattr(fitted, "feature_importances_"):
        result["feature_importance"] = {name: round(float(value), 5) for name, value in zip(x.columns, fitted.feature_importances_)}
    return result


def _trend(data, target):
    x = (data["datetime"] - data["datetime"].iloc[0]).dt.total_seconds().to_numpy() / 86400
    y = data[target].to_numpy()
    split = max(4, int(len(data) * 0.8))
    coefficients = np.polyfit(x[:split], y[:split], 1)
    predicted = np.polyval(coefficients, x[split:])
    return {"slope_per_day": round(float(coefficients[0]), 6),
            "intercept": round(float(coefficients[1]), 4),
            "holdout": _metrics(y[split:], predicted),
            "holdout_series": [{"datetime": str(timestamp), "actual": round(float(actual), 4),
                                "predicted": round(float(forecast), 4)}
                               for timestamp, actual, forecast in zip(
                                   data["datetime"].iloc[split:].head(500), y[split:], predicted)],
            "message": "A linear trend is fitted on the first 80% and evaluated on the final 20%."}


def _seasonal(data):
    work = data.copy()
    work["hour"] = work["datetime"].dt.hour
    baseline = work.groupby("hour")["value"].transform("mean")
    valid = baseline.notna()
    return {"hourly_baseline": {str(int(k)): round(float(v), 4) for k, v in work.groupby("hour")["value"].mean().items()},
            "seasonal_series": [{"hour": int(hour), "value": round(float(value), 4)}
                    for hour, value in work.groupby("hour")["value"].mean().items()],
            "metrics": _metrics(work.loc[valid, "value"].to_numpy(), baseline[valid].to_numpy()),
            "message": "An explainable hourly seasonal baseline is estimated from the available sensor history."}


def _anomaly(data, z_score):
    values = data["value"]
    median = float(values.median())
    mad = float(np.median(np.abs(values - median)))
    scale = 1.4826 * mad if mad else float(values.std())
    scale = scale if scale > 0 else 1.0
    robust_z = (values - median) / scale
    flagged = data.loc[robust_z.abs() >= z_score, ["datetime", "value"]].copy()
    flagged["datetime"] = flagged["datetime"].astype(str)
    return {"center": round(median, 4), "scale": round(scale, 4),
            "threshold": z_score, "anomaly_count": int(len(flagged)),
            "anomalies": flagged.head(100).to_dict(orient="records"),
            "anomaly_series": [{"datetime": str(timestamp), "value": round(float(value), 4),
                                "is_anomaly": bool(abs(score) >= z_score)}
                               for timestamp, value, score in zip(
                                   data["datetime"].head(1000), values.head(1000), robust_z.head(1000))],
            "message": "Robust median/MAD anomaly detection limits sensitivity to extreme pollution spikes."}
