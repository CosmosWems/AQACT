"""JSON-safe serialisation helpers.

Every value returned to the browser passes through this module.  This avoids
the classic Flask failure where ``NaN``/``Timestamp`` values are emitted as
invalid JSON, and keeps preview payloads bounded for large sensor files.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


def to_py(value: Any) -> Any:
    """Convert numpy/pandas scalars into plain Python JSON-safe values."""
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, (np.datetime64,)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (list, tuple)):
        return [to_py(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_py(v) for k, v in value.items()}
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def records_preview(df: pd.DataFrame, max_rows: int = 200) -> list[dict]:
    if df is None or df.empty:
        return []
    sample = df.head(max(0, int(max_rows)))
    records: list[dict] = []
    for row in sample.itertuples(index=False, name=None):
        records.append({str(col): to_py(val) for col, val in zip(sample.columns, row)})
    return records


def column_kind(dtype) -> str:
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_numeric_dtype(dtype):
        return "numeric"
    if isinstance(dtype, pd.CategoricalDtype):
        return "categorical"
    return "text"


def df_to_payload(df: pd.DataFrame, preview_rows: int = 200) -> dict:
    """Serialise dataframe metadata plus a bounded preview."""
    preview_rows = max(0, min(int(preview_rows), 1000))
    dtypes = {str(c): str(t) for c, t in df.dtypes.items()}
    kinds = {str(c): column_kind(t) for c, t in df.dtypes.items()}
    try:
        memory_mb = round(float(df.memory_usage(deep=True).sum()) / (1024 * 1024), 2)
    except Exception:  # noqa: BLE001
        memory_mb = None
    return {
        "columns": [str(c) for c in df.columns],
        "dtypes": dtypes,
        "kinds": kinds,
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "preview": records_preview(df, preview_rows),
        "preview_rows": min(int(df.shape[0]), preview_rows),
        "truncated": bool(df.shape[0] > preview_rows),
        "memory_mb": memory_mb,
        "numeric_columns": [str(c) for c in df.select_dtypes(include="number").columns],
        "datetime_columns": [str(c) for c in df.select_dtypes(include=["datetime64", "datetimetz"]).columns],
    }


def json_safe(value: Any) -> Any:
    """Recursively sanitise an arbitrary result before jsonify()."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return df_to_payload(value)
    if isinstance(value, pd.Series):
        return [json_safe(v) for v in value.tolist()]
    return to_py(value)


def unique_strings(values: Iterable[Any], limit: int = 50) -> list[str]:
    out: list[str] = []
    seen = set()
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def safe_float(value: Optional[Any], digits: int = 4) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)
