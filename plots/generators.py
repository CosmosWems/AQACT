"""
Plot Generators — AQACT
Upgrades: expanded sensor columns (CO2, VOC, NC fractions, NO2, pm1.0, pm4.0),
new plot types (violin, bubble, multi-pollutant radar, PM size distribution),
comparative multi-station overlays.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from scipy.stats import theilslopes
from scipy.ndimage import gaussian_filter
from plotly.subplots import make_subplots


def _to_json(fig) -> str:
    return fig.to_json()


_PALETTES = {
    "Plotly": px.colors.qualitative.Plotly,
    "D3": px.colors.qualitative.D3,
    "G10": px.colors.qualitative.G10,
    "Safe": px.colors.qualitative.Safe,
    "Set2": px.colors.qualitative.Set2,
    "Pastel": px.colors.qualitative.Pastel,
    "Dark24": px.colors.qualitative.Dark24,
}


def _comparison_options(df, params, x_col=None):
    color = params.get("color_col") or None
    facet = params.get("facet_col") or None
    if color not in df.columns:
        color = None
    if facet not in df.columns or facet == x_col:
        facet = None
    if color and color == facet:
        facet = None
    for column in {color, facet} - {None}:
        df[column] = df[column].astype("string").fillna("Missing")
    return color, facet, _PALETTES.get(params.get("color_palette"), _PALETTES["Plotly"])


# ── Time Series ───────────────────────────────────────────────────────────────
def _time_series_frame(df, params, value_cols=None, default_resample=None):
    """Melt value columns into one tidy time/value frame.

    Shared by the line, area, and particle-number time-series charts so all
    three use the same datetime parsing, colour/facet handling, and optional
    resampling.  Keeping this in one place avoids the duplicated melt/resample
    logic that previously drifted between the chart types.
    """
    dt_col = _datetime_column(df)
    value_cols = value_cols if value_cols is not None else params.get("columns", ["pm2.5"])
    value_cols = [col for col in value_cols if col in df.columns]
    if not value_cols:
        raise ValueError("Select at least one available numeric column.")

    df2 = df.copy()
    df2[dt_col] = pd.to_datetime(df2[dt_col], errors="coerce")
    color, facet, palette = _comparison_options(df2, params, dt_col)

    id_vars = [dt_col] + [c for c in (color, facet) if c and c != dt_col]
    long_df = df2.melt(id_vars=id_vars, value_vars=value_cols,
                       var_name="_measure", value_name="_value")
    long_df["_value"] = pd.to_numeric(long_df["_value"], errors="coerce")
    long_df = long_df.dropna(subset=[dt_col, "_value"])
    if long_df.empty:
        raise ValueError("The selected columns do not contain usable numeric values with valid dates.")

    resample = params.get("resample", default_resample)
    if resample:
        group_cols = [c for c in (color, facet, "_measure") if c]
        pieces = []
        for keys, group_df in long_df.groupby(group_cols, dropna=False, sort=False):
            if not isinstance(keys, tuple):
                keys = (keys,)
            series_obj = group_df.set_index(dt_col)["_value"].resample(resample)
            statistic = params.get("statistic", "mean")
            if statistic == "median":
                series = series_obj.median()
            elif statistic == "percentile":
                series = series_obj.quantile(float(params.get("percentile", 95)) / 100)
            elif statistic == "frequency":
                series = series_obj.count()
            else:
                series = series_obj.mean()
            series = series.dropna()
            part = series.rename("_value").reset_index()
            for key, value in zip(group_cols, keys):
                part[key] = value
            pieces.append(part)
        long_df = pd.concat(pieces, ignore_index=True) if pieces else long_df.iloc[0:0]

    return long_df, value_cols, dt_col, color, facet, palette


def _datetime_column(df):
    candidates = {"datetime", "timestamp", "date", "time", "utcdatetime", "captured_at", "created_at"}
    return next((c for c in df.columns if str(c).lower() in candidates), df.columns[0])


def _timeseries(df, params):
    long_df, value_cols, dt_col, color, facet, palette = _time_series_frame(df, params)
    kwargs = {
        "x": dt_col, "y": "_value",
        "title": params.get("title", "Time Series"),
        "template": "plotly_white",
        "render_mode": "svg",
        "color": color or "_measure",
    }
    if len(value_cols) > 1:
        kwargs["line_group"] = "_measure"
    if facet:
        kwargs["facet_col"] = facet
    # SVG rendering avoids WebGL context failures on long time-series plots.
    fig = px.line(long_df, color_discrete_sequence=palette, **kwargs)
    statistic = params.get("statistic", "mean") if params.get("resample") else "raw observations"
    ylabel = "Valid records per interval" if statistic == "frequency" else (
        f"{params.get('percentile', 95)}th percentile" if statistic == "percentile" else statistic.title())
    fig.update_layout(xaxis_title="Date/Time", yaxis_title=ylabel)
    return _to_json(fig)


# ── Box Plot ──────────────────────────────────────────────────────────────────
_MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
_SEASON_ORDER = ["winter","spring","summer","fall"]

def _boxplot(df, params):
    col   = params.get("column", "pm2.5")
    group = params.get("group_by", "season")   # month | season | hour | location
    title = params.get("title", None)
    df2 = df.copy()
    df2[col] = pd.to_numeric(df2[col], errors="coerce")

    # Ensure grouping column exists, derive from datetime if needed
    dt_col = "datetime" if "datetime" in df2.columns else None
    if group == "month" and "month" not in df2.columns and dt_col:
        df2["month"] = pd.to_datetime(df2[dt_col], errors="coerce").dt.month
    if group == "hour" and "hour" not in df2.columns and dt_col:
        df2["hour"] = pd.to_datetime(df2[dt_col], errors="coerce").dt.hour
    if group == "season" and "season" not in df2.columns and "month" in df2.columns:
        _ms = {1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
               6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall",12:"winter"}
        df2["season"] = df2["month"].map(_ms)

    category_orders = {}
    if group and group in df2.columns:
        raw_groups = df2[group].dropna().unique()

        # Determine display order
        if group == "season":
            order = [s for s in _SEASON_ORDER if s in raw_groups]
        elif group == "month":
            order = sorted([g for g in raw_groups], key=lambda x: int(x) if str(x).isdigit() else 0)
        elif group == "hour":
            order = sorted([g for g in raw_groups], key=lambda x: int(x) if str(x).isdigit() else 0)
        else:
            order = sorted(raw_groups, key=str)
        if group == "season":
            category_orders[group] = order
    color, facet, palette = _comparison_options(df2, params, group)
    kwargs = {"y": col, "title": title or f"Box Plot — {col}  ·  grouped by {group}",
              "template": "plotly_white", "category_orders": category_orders,
              "color_discrete_sequence": palette, "points": "outliers"}
    if group and group in df2.columns:
        kwargs["x"] = group
    if color:
        kwargs["color"] = color
    if facet:
        kwargs["facet_col"] = facet
    fig = px.box(df2, **kwargs)

    group_label = {"month":"Month","season":"Season","hour":"Hour of Day",
                   "location":"Location / Station"}.get(group, group.title())
    fig.update_layout(
        title=title or f"Box Plot — {col}  ·  grouped by {group_label}",
        yaxis_title=col,
        xaxis_title=group_label,
        template="plotly_white",
    )
    return _to_json(fig)


# ── Violin Plot ───────────────────────────────────────────────────────────────
def _violin(df, params):
    col   = params.get("column", "pm2.5")
    group = params.get("group_by", "season")
    title = params.get("title", f"Violin — {col}")
    df2 = df.copy()
    df2[col] = pd.to_numeric(df2[col], errors="coerce")
    color, facet, palette = _comparison_options(df2, params, group)
    kwargs = {"y": col, "title": title, "template": "plotly_white",
              "color_discrete_sequence": palette, "box": True, "points": False}
    if group and group in df2.columns:
        kwargs["x"] = group
    if color:
        kwargs["color"] = color
    if facet:
        kwargs["facet_col"] = facet
    fig = px.violin(df2, **kwargs)
    fig.update_layout(yaxis_title=col)
    return _to_json(fig)


# ── Calendar Heatmap ──────────────────────────────────────────────────────────
def _calendar(df, params):
    col        = params.get("column", "pm2.5")
    year_filt  = params.get("year", None)          # int or None → all years merged
    colorscale = params.get("colorscale", "YlOrRd") # e.g. "Jet", "Spectral_r"
    title      = params.get("title", None)

    df2 = df.copy()
    dt_col = "datetime" if "datetime" in df2.columns else df2.columns[0]
    df2[dt_col] = pd.to_datetime(df2[dt_col], errors="coerce")
    df2[col]    = pd.to_numeric(df2[col], errors="coerce")
    df2 = df2.set_index(dt_col)
    daily = df2[col].resample("D").mean().dropna().reset_index()
    daily.columns = ["date", col]
    daily["dow"]  = daily["date"].dt.dayofweek
    daily["week"] = daily["date"].dt.isocalendar().week.astype(int)
    daily["year"] = daily["date"].dt.year

    # Apply year filter
    if year_filt is not None:
        daily = daily[daily["year"] == int(year_filt)]
    if daily.empty:
        raise ValueError(f"No data for year {year_filt}.")

    # Build one subplot per year (or single if filtered / single year present)
    years = sorted(daily["year"].unique())
    day_labels = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

    if len(years) == 1 or year_filt is not None:
        pivot = daily.pivot_table(index="dow", columns="week", values=col, aggfunc="mean")
        yr_label = str(years[0]) if year_filt is None else str(year_filt)
        auto_title = f"Calendar Heatmap — {col}  ({yr_label})"
        fig = go.Figure(go.Heatmap(
            z=pivot.values,
            x=[f"W{w}" for w in pivot.columns.tolist()],
            y=[day_labels[i] for i in pivot.index],
            colorscale=colorscale,
            colorbar=dict(title=col),
            hoverongaps=False,
        ))
        fig.update_layout(
            title=title or auto_title,
            xaxis_title="Week of Year",
            yaxis_title="Day of Week",
            template="plotly_white",
        )
    else:
        # Multi-year: one heatmap per year stacked vertically
        from plotly.subplots import make_subplots
        fig = make_subplots(
            rows=len(years), cols=1,
            subplot_titles=[str(y) for y in years],
            vertical_spacing=0.04,
        )
        for row_idx, yr in enumerate(years, 1):
            sub = daily[daily["year"] == yr]
            pivot = sub.pivot_table(index="dow", columns="week", values=col, aggfunc="mean")
            fig.add_trace(
                go.Heatmap(
                    z=pivot.values,
                    x=[f"W{w}" for w in pivot.columns.tolist()],
                    y=[day_labels[i] for i in pivot.index],
                    colorscale=colorscale,
                    colorbar=dict(title=col) if row_idx == 1 else None,
                    showscale=(row_idx == 1),
                    hoverongaps=False,
                    name=str(yr),
                ),
                row=row_idx, col=1,
            )
        fig.update_layout(
            title=title or f"Calendar Heatmap — {col}",
            template="plotly_white",
            height=220 * len(years) + 80,
        )
    return _to_json(fig)


# ── Diurnal Profile ───────────────────────────────────────────────────────────
def _bootstrap_group_mean(grouped, seed=2026, resamples=300):
    rng = np.random.default_rng(seed)
    means, lows, highs = [], [], []
    for _, values in grouped:
        values = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
        means.append(float(np.mean(values)) if len(values) else np.nan)
        if len(values) < 2:
            lows.append(means[-1]); highs.append(means[-1]); continue
        sample = values if len(values) <= 500 else values[np.linspace(0, len(values)-1, 500, dtype=int)]
        boot = np.mean(rng.choice(sample, size=(resamples, len(sample)), replace=True), axis=1)
        lows.append(float(np.quantile(boot, .025))); highs.append(float(np.quantile(boot, .975)))
    index = list(grouped.indices.keys())
    return (pd.Series(means, index=index), pd.Series(lows, index=index), pd.Series(highs, index=index))


def _diurnal(df, params):
    cols  = params.get("columns", ["pm2.5"])
    title = params.get("title", "Diurnal Profile")
    df2 = df.copy()
    dt_col = _datetime_column(df2)
    df2[dt_col] = pd.to_datetime(df2[dt_col], errors="coerce")
    df2["hour"] = df2[dt_col].dt.hour
    fig = go.Figure()
    for col in cols:
        if col not in df2.columns:
            continue
        df2[col] = pd.to_numeric(df2[col], errors="coerce")
        grp = df2.groupby("hour")[col]
        mean, lower, upper = _bootstrap_group_mean(grp)
        fig.add_trace(go.Scatter(x=mean.index, y=mean.values, mode="lines+markers", name=col,
            error_y={"type": "data", "symmetric": False, "array": (upper-mean).values,
                     "arrayminus": (mean-lower).values, "thickness": 1, "width": 2}))
    fig.update_layout(title=title, xaxis_title="Hour of Day", yaxis_title="Concentration", template="plotly_white")
    return _to_json(fig)


# ── Monthly Profile ───────────────────────────────────────────────────────────
def _monthly(df, params):
    cols  = params.get("columns", ["pm2.5"])
    title = params.get("title", "Monthly Profile")
    df2 = df.copy()
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    fig = go.Figure()
    for col in cols:
        if col not in df2.columns:
            continue
        df2[col] = pd.to_numeric(df2[col], errors="coerce")
        dt_col = _datetime_column(df2)
        months = df2["month"] if "month" in df2.columns else pd.to_datetime(df2[dt_col], errors="coerce").dt.month
        grp = df2.assign(_month=months).groupby("_month")[col].agg(["mean", "count", "std"]).dropna(subset=["mean"])
        sem = grp["std"] / np.sqrt(grp["count"].clip(lower=1))
        if not grp.empty:
            fig.add_trace(go.Bar(x=[month_names[int(i)-1] for i in grp.index], y=grp["mean"], name=col,
                error_y={"type":"data", "array":(1.96*sem.fillna(0)).values, "visible":True}))
    fig.update_layout(title=title, xaxis_title="Month", yaxis_title="Mean Concentration",
                      template="plotly_white", barmode="group")
    return _to_json(fig)


def _timevariation(df, params):
    col = params.get("column", "pm2.5")
    if col not in df: raise ValueError(f"Selected column '{col}' was not found.")
    dt = _datetime_column(df)
    if str(dt).lower() not in {"datetime", "timestamp", "date", "time", "utcdatetime", "captured_at", "created_at"}:
        raise ValueError("Temporal variation plots require a recognized datetime column.")
    work = pd.DataFrame({"time": pd.to_datetime(df[dt], errors="coerce"),
                         "value": pd.to_numeric(df[col], errors="coerce")}).dropna()
    if work.empty: raise ValueError("No valid numeric observations with dates are available.")
    work["hour"] = work.time.dt.hour
    work["weekday"] = work.time.dt.dayofweek
    work["month"] = work.time.dt.month
    means = {}
    fig = make_subplots(rows=2, cols=2,
        subplot_titles=("Diurnal profile (95% bootstrap CI)", "Day of week (95% bootstrap CI)",
                        "Hour × day of week", "Monthly profile (95% CI)"),
        specs=[[{"type":"xy"},{"type":"xy"}],[{"type":"xy"},{"type":"xy"}]])
    for key, row, col_idx, label in (("hour",1,1,"Hour"),("weekday",1,2,"Weekday"),("month",2,2,"Month")):
        grouped = work.groupby(key).value
        mean, lower, upper = _bootstrap_group_mean(grouped, seed=2026 + row*10 + col_idx)
        means[key] = mean
        xlabels = (["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if key == "weekday" else
                   ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"] if key == "month" else mean.index)
        xvals = list(mean.index)
        err = {"type":"data", "symmetric":False, "array":(upper-mean).values, "arrayminus":(mean-lower).values,
               "thickness":1, "width":2}
        fig.add_trace(go.Scatter(x=xvals,y=mean.values,mode="lines+markers",name=label,error_y=err),row=row,col=col_idx)
        if key in ("weekday","month"):
            fig.update_xaxes(tickmode="array",tickvals=xvals,ticktext=[xlabels[int(i)] if key == "weekday" else xlabels[int(i)-1] for i in xvals],row=row,col=col_idx)
    pivot = work.pivot_table(index="weekday",columns="hour",values="value",aggfunc="mean").reindex(index=range(7),columns=range(24))
    fig.add_trace(go.Heatmap(z=pivot.values,x=list(range(24)),y=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        colorscale="Turbo",colorbar={"title":col},hovertemplate="%{y} %{x}:00<br>Mean %{z:.3g}<extra></extra>",name="Mean"),row=2,col=1)
    fig.update_xaxes(title_text="Hour of day",row=1,col=1); fig.update_yaxes(title_text=col,row=1,col=1)
    fig.update_xaxes(title_text="Day of week",row=1,col=2); fig.update_yaxes(title_text=col,row=1,col=2)
    fig.update_xaxes(title_text="Hour of day",row=2,col=1); fig.update_yaxes(title_text="Day of week",row=2,col=1)
    fig.update_xaxes(title_text="Month",row=2,col=2); fig.update_yaxes(title_text=col,row=2,col=2)
    fig.update_layout(title=params.get("title",f"OpenAir-style Time Variation — {col}"),template="plotly_white",height=680)
    return _to_json(fig)


# ── Wind Rose ─────────────────────────────────────────────────────────────────
def _windrose(df, params):
    ws_col, wd_col = _wind_columns(df, params)
    if not ws_col or ws_col not in df or not wd_col or wd_col not in df:
        raise ValueError("Select available wind speed and wind direction columns.")
    data = pd.DataFrame({"ws": pd.to_numeric(df[ws_col], errors="coerce"),
                         "wd": pd.to_numeric(df[wd_col], errors="coerce") % 360}).dropna()
    data = data[data.ws >= 0]
    if data.empty: raise ValueError("No valid non-negative wind speed and direction pairs were found.")
    data["sector"] = _wind_sector(data.wd, angle=float(params.get("angle", 22.5)))
    edges = [float(x) for x in params.get("speed_bins", [0, 1, 2, 4, 6, np.inf])]
    if not np.isinf(edges[-1]): edges.append(np.inf)
    labels = [f"{edges[i]:g}–{edges[i+1]:g}" if np.isfinite(edges[i+1]) else f"≥{edges[i]:g}" for i in range(len(edges)-1)]
    data["speed_range"] = pd.cut(data.ws, bins=edges, labels=labels, right=False, include_lowest=True)
    summary = data.groupby(["sector", "speed_range"], observed=False).size().unstack(fill_value=0)
    summary = summary.reindex(columns=labels, fill_value=0).div(len(data)).mul(100)
    fig = go.Figure()
    for label in labels:
        fig.add_trace(go.Barpolar(r=summary[label].values, theta=summary.index, name=label,
                                  opacity=.9, width=float(params.get("angle", 22.5))))
    fig.update_layout(title=params.get("title", "Wind Rose"), barmode="stack",
        polar={"angularaxis": {"direction": "clockwise", "rotation": 90, "dtick": 45},
               "radialaxis": {"title": "Frequency (%)", "ticksuffix": "%"}},
        template="plotly_white", legend_title="Wind speed (m/s)")
    return _to_json(fig)


# ── Scatter + Regression ──────────────────────────────────────────────────────
def _scatter(df, params):
    x_col = params.get("x_col", "pm2.5")
    y_col = params.get("y_col", "pm10")
    color = params.get("color_col", None) or None   # empty string → None
    title = params.get("title", None)
    df2 = df.copy()
    df2[x_col] = pd.to_numeric(df2[x_col], errors="coerce")
    df2[y_col] = pd.to_numeric(df2[y_col], errors="coerce")
    df2 = df2.dropna(subset=[x_col, y_col])
    color, facet, palette = _comparison_options(df2, params, x_col)

    auto_title = f"Scatter: {x_col} vs {y_col}" + (f"  ·  colour: {color}" if color else "")
    kwargs = {
        "x": x_col, "y": y_col,
        "trendline": "ols",
        "title": title or auto_title,
        "template": "plotly_white",
        "opacity": 0.7,
        "render_mode": "svg",
    }
    if color:
        kwargs["color"] = color
    if facet:
        kwargs["facet_col"] = facet
    fig = px.scatter(df2, color_discrete_sequence=palette, **kwargs)
    fig.update_traces(marker=dict(size=5))
    return _to_json(fig)


# ── Bubble Chart (3-variable) ─────────────────────────────────────────────────
def _bubble(df, params):
    x_col    = params.get("x_col", "pm2.5")
    y_col    = params.get("y_col", "temperature")
    size_col = params.get("size_col", "RH")
    color    = params.get("color_col", None)
    title    = params.get("title", f"Bubble: {x_col} vs {y_col} (size={size_col})")
    df2 = df.copy()
    for c in [x_col, y_col, size_col]:
        if c in df2.columns:
            df2[c] = pd.to_numeric(df2[c], errors="coerce")
    df2 = df2.dropna(subset=[x_col, y_col, size_col])
    color, facet, palette = _comparison_options(df2, params, x_col)
    kwargs = {"x": x_col, "y": y_col, "size": size_col, "title": title,
              "template": "plotly_white", "opacity": 0.7}
    if color:
        kwargs["color"] = color
    if facet:
        kwargs["facet_col"] = facet
    fig = px.scatter(df2, color_discrete_sequence=palette, **kwargs)
    return _to_json(fig)


# ── Heatmap / Correlation Matrix ──────────────────────────────────────────────
def _correlation(df, params):
    cols  = params.get("columns", None)
    title = params.get("title", "Correlation Matrix")
    df2 = df.select_dtypes(include="number")
    if cols:
        df2 = df2[[c for c in cols if c in df2.columns]]
    corr = df2.corr().round(3)
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=corr.columns.tolist(), y=corr.index.tolist(),
        colorscale="RdBu", zmid=0,
        text=corr.values.round(2), texttemplate="%{text}",
        colorbar=dict(title="r"),
    ))
    fig.update_layout(title=title, template="plotly_white")
    return _to_json(fig)


# ── Seasonal Box ──────────────────────────────────────────────────────────────
def _seasonal_box(df, params):
    cols  = params.get("columns", ["pm2.5"])
    title = params.get("title", "Seasonal Variation")
    df2 = df.copy()
    season_order = ["winter","spring","summer","fall"]
    fig = go.Figure()
    for col in cols:
        if col not in df2.columns:
            continue
        df2[col] = pd.to_numeric(df2[col], errors="coerce")
        for s in season_order:
            if "season" not in df2.columns:
                break
            sub = df2[df2["season"] == s][col].dropna()
            if len(sub):
                fig.add_trace(go.Box(y=sub.values, name=f"{s} ({col})", boxmean=True))
    fig.update_layout(title=title, template="plotly_white")
    return _to_json(fig)


# ── Histogram ─────────────────────────────────────────────────────────────────
def _histogram(df, params):
    col   = params.get("column", "pm2.5")
    bins  = int(params.get("bins", 40))      # controlled by UI slider
    title = params.get("title", None)
    df2 = df.copy()
    df2[col] = pd.to_numeric(df2[col], errors="coerce").dropna()
    if df2[col].empty:
        raise ValueError(f"No numeric data in column '{col}'.")
    color, facet, palette = _comparison_options(df2, params)
    auto_title = f"Distribution — {col}  (n={len(df2[col]):,}, bins={bins})"
    kwargs = dict(
        x=col, nbins=bins,
        title=title or auto_title,
        template="plotly_white",
        marginal="box",
        opacity=0.82,
    )
    if color:
        kwargs["color"] = color
    if facet:
        kwargs["facet_col"] = facet
    fig = px.histogram(df2, color_discrete_sequence=palette, **kwargs)
    fig.update_traces(marker_line_width=0.5, marker_line_color="white")
    # Add mean and ±1 std lines
    mu  = float(df2[col].mean())
    std = float(df2[col].std())
    for xval, label, color, dash in [
        (mu,       f"Mean {mu:.2f}",       "#ef4444", "solid"),
        (mu-std,   f"−1σ  {mu-std:.2f}",  "#f59e0b", "dash"),
        (mu+std,   f"+1σ  {mu+std:.2f}",  "#f59e0b", "dash"),
    ]:
        fig.add_vline(x=xval, line=dict(color=color, width=1.5, dash=dash),
                      annotation_text=label, annotation_position="top right",
                      annotation_font_size=10)
    return _to_json(fig)


# ── AQI Category Bar ──────────────────────────────────────────────────────────
def _aqi_bar(df, params):
    title = params.get("title", "AQI Category Distribution")
    if "AQI_Category" not in df.columns:
        raise ValueError("AQI_Category column not found. Run cleaning first.")
    counts = df["AQI_Category"].value_counts()
    colors = {
        "Good": "#00e400", "Moderate": "#ffff00",
        "Unhealthy for Sensitive": "#ff7e00", "Unhealthy": "#ff0000",
        "Very Unhealthy": "#8f3f97", "Hazardous": "#7e0023",
    }
    fig = go.Figure(go.Bar(
        x=counts.index.tolist(), y=counts.values.tolist(),
        marker_color=[colors.get(c, "#888") for c in counts.index]
    ))
    fig.update_layout(title=title, xaxis_title="Category", yaxis_title="Count", template="plotly_white")
    return _to_json(fig)


# ── Spatial Heatmap ───────────────────────────────────────────────────────────
def _spatial_heatmap(df, params):
    col   = params.get("column", "pm2.5")
    title = params.get("title", f"Spatial — {col}")
    df2 = df.copy()
    if "lat" not in df2.columns or "lon" not in df2.columns:
        raise ValueError("lat/lon columns required.")
    df2["lat"] = pd.to_numeric(df2["lat"], errors="coerce")
    df2["lon"] = pd.to_numeric(df2["lon"], errors="coerce")
    df2[col]   = pd.to_numeric(df2[col], errors="coerce")
    df2 = df2.dropna(subset=["lat","lon",col])
    fig = px.density_mapbox(
        df2, lat="lat", lon="lon", z=col, radius=20,
        center=dict(lat=df2["lat"].mean(), lon=df2["lon"].mean()),
        zoom=10, mapbox_style="open-street-map", title=title, opacity=0.7
    )
    return _to_json(fig)


# ── Time-of-week heatmap ──────────────────────────────────────────────────────
def _time_of_week(df, params):
    col   = params.get("column", "pm2.5")
    title = params.get("title", f"Time-of-Week — {col}")
    df2 = df.copy()
    df2[col] = pd.to_numeric(df2[col], errors="coerce")
    dt = pd.to_datetime(df2.get("datetime", df2.columns[0]), errors="coerce")
    df2["hour"] = dt.dt.hour
    df2["dow"]  = dt.dt.dayofweek
    pivot = df2.pivot_table(index="dow", columns="hour", values=col, aggfunc="mean")
    day_labels = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(range(24)),
        y=[day_labels[i] for i in pivot.index],
        colorscale="YlOrRd", colorbar=dict(title=col)
    ))
    fig.update_layout(title=title, xaxis_title="Hour", yaxis_title="Day of Week", template="plotly_white")
    return _to_json(fig)


# ── Area Plot ─────────────────────────────────────────────────────────────────
def _area(df, params):
    long_df, value_cols, dt_col, color, facet, palette = _time_series_frame(
        df, params, default_resample="D")
    kwargs = {
        "x": dt_col, "y": "_value",
        "title": params.get("title", "Area Plot"),
        "template": "plotly_white",
        "color": color or "_measure",
    }
    if len(value_cols) > 1:
        kwargs["line_group"] = "_measure"
    if facet:
        kwargs["facet_col"] = facet
    fig = px.area(long_df, color_discrete_sequence=palette, **kwargs)
    fig.update_layout(xaxis_title="Date", yaxis_title="Concentration")
    return _to_json(fig)


# ── PM Size Distribution (new) ────────────────────────────────────────────────
def _pm_size_dist(df, params):
    """
    Bar chart of mean PM fractions: PM1.0, PM2.5, PM4.0, PM10
    and optionally particle number concentrations (NC fractions).
    Useful for Sensirion / PMS5003 data.
    """
    title = params.get("title", "PM Size Distribution")
    df2 = df.copy()
    pm_cols   = [c for c in ["pm1.0","pm2.5","pm4.0","pm10"] if c in df2.columns]
    nc_cols   = [c for c in ["NC0.5","NC1.0","NC2.5","NC4.0","NC10"] if c in df2.columns]
    fig = go.Figure()
    if pm_cols:
        means = {c: float(pd.to_numeric(df2[c], errors="coerce").mean()) for c in pm_cols}
        fig.add_trace(go.Bar(x=list(means.keys()), y=list(means.values()),
                             name="Mass Conc. (μg/m³)", marker_color="#3b82f6"))
    if nc_cols:
        nc_means = {c: float(pd.to_numeric(df2[c], errors="coerce").mean()) for c in nc_cols}
        fig.add_trace(go.Bar(x=list(nc_means.keys()), y=list(nc_means.values()),
                             name="Number Conc. (#/cm³)", marker_color="#10b981",
                             yaxis="y2"))
        fig.update_layout(
            yaxis2=dict(title="Number Conc. (#/cm³)", overlaying="y", side="right")
        )
    fig.update_layout(title=title, xaxis_title="Size Fraction", yaxis_title="Mean Mass Conc. (μg/m³)",
                      template="plotly_white", barmode="group")
    return _to_json(fig)


# ── Multi-Pollutant Radar (new) ───────────────────────────────────────────────
def _radar(df, params):
    """
    Radar / spider chart showing normalised mean values across multiple pollutants.
    Useful for quick sensor capability overview.
    """
    title = params.get("title", "Multi-Pollutant Radar")
    cols  = params.get("columns", ["pm2.5","pm10","O3","CO","SO2","NOx","temperature","RH"])
    df2 = df.copy()
    available = [c for c in cols if c in df2.columns]
    if len(available) < 3:
        raise ValueError("Need at least 3 pollutant columns for radar chart.")
    means = {}
    for c in available:
        v = pd.to_numeric(df2[c], errors="coerce").mean()
        if pd.notna(v) and v > 0:
            means[c] = float(v)
    if not means:
        raise ValueError("No valid data for radar chart.")
    # Normalise 0–1 per pollutant (using max as reference)
    max_vals = list(means.values())
    overall_max = max(max_vals)
    norm_vals = [v / overall_max for v in means.values()]
    cats = list(means.keys())
    # Close the radar
    cats  += [cats[0]]
    norm_vals += [norm_vals[0]]
    fig = go.Figure(go.Scatterpolar(
        r=norm_vals, theta=cats, fill="toself",
        name="Relative Level", line=dict(color="#3b82f6")
    ))
    fig.update_layout(
        title=title,
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        template="plotly_white"
    )
    return _to_json(fig)


# ── CO2 + Met Dual-axis (new) ─────────────────────────────────────────────────
def _co2_met(df, params):
    """
    Dual-axis time series: CO2 (ppm) on left, temperature+RH on right.
    Designed for AirGradient / Sensirion data.
    """
    title  = params.get("title", "CO2 & Meteorological Overview")
    resamp = params.get("resample", "H")
    dt_col = "datetime" if "datetime" in df.columns else df.columns[0]
    df2 = df.copy()
    df2[dt_col] = pd.to_datetime(df2[dt_col], errors="coerce")
    df2 = df2.set_index(dt_col)

    fig = go.Figure()
    if "CO2" in df2.columns:
        s = pd.to_numeric(df2["CO2"], errors="coerce").resample(resamp).mean()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name="CO2 (ppm)",
                                 line=dict(color="#06b6d4", width=2)))
    if "temperature" in df2.columns:
        s = pd.to_numeric(df2["temperature"], errors="coerce").resample(resamp).mean()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name="Temp (°C)",
                                 line=dict(color="#ef4444", width=1.5), yaxis="y2"))
    if "RH" in df2.columns:
        s = pd.to_numeric(df2["RH"], errors="coerce").resample(resamp).mean()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name="RH (%)",
                                 line=dict(color="#8b5cf6", width=1.5, dash="dash"), yaxis="y2"))
    if "VOC" in df2.columns or "VOC_index" in df2.columns:
        vc = "VOC_index" if "VOC_index" in df2.columns else "VOC"
        s = pd.to_numeric(df2[vc], errors="coerce").resample(resamp).mean()
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=f"{vc}",
                                 line=dict(color="#f59e0b", width=1.5, dash="dot"), yaxis="y3"))

    fig.update_layout(
        title=title, template="plotly_white",
        yaxis =dict(title="CO2 (ppm)", side="left"),
        yaxis2=dict(title="Temp (°C) / RH (%)", side="right", overlaying="y"),
        yaxis3=dict(title="VOC Index", side="right", overlaying="y", anchor="free", position=0.95),
    )
    return _to_json(fig)


# ── NC Fraction Time Series (new for Sensirion/PMS5003) ──────────────────────
def _nc_timeseries(df, params):
    """Particle number concentration time series for all NC fractions."""
    nc_cols = [c for c in ["NC0.5", "NC1.0", "NC2.5", "NC4.0", "NC10"] if c in df.columns]
    if not nc_cols:
        raise ValueError("No NC fraction columns found (NC0.5, NC1.0, NC2.5, NC4.0, NC10).")
    long_df, _, dt_col, _, _, palette = _time_series_frame(
        df, params, value_cols=nc_cols, default_resample="H")
    fig = px.line(
        long_df, x=dt_col, y="_value", color="_measure",
        title=params.get("title", "Particle Number Concentrations"),
        template="plotly_white", color_discrete_sequence=palette,
        render_mode="svg",
    )
    fig.update_layout(xaxis_title="Date/Time", yaxis_title="Number Conc. (#/cm³)")
    return _to_json(fig)


# ── Exceedance Frequency Plot (new) ──────────────────────────────────────────
def _exceedance(df, params):
    """
    Cumulative frequency / exceedance curve for a given column.
    Shows what % of time exceeds each concentration level.
    """
    col   = params.get("column", "pm2.5")
    title = params.get("title", f"Exceedance Frequency — {col}")
    df2 = df.copy()
    df2[col] = pd.to_numeric(df2[col], errors="coerce").dropna()
    s = df2[col].sort_values()
    exceedance_pct = 100 * (1 - np.arange(1, len(s) + 1) / len(s))
    who_lines = {"pm2.5": 15, "pm10": 45, "o3": 60, "so2": 40, "no2": 25, "co": 4}
    epa_lines = {"pm2.5": 35, "pm10": 150, "o3": 70, "so2": 75, "no2": 100}
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s.values, y=exceedance_pct, mode="lines",
                             name="Exceedance %", line=dict(color="#3b82f6", width=2)))
    col_key = col.lower().replace(".", "")
    if col_key in who_lines:
        fig.add_vline(x=who_lines[col_key], line=dict(color="#10b981", dash="dash", width=2),
                      annotation_text="WHO 24h", annotation_position="top right")
    if col_key in epa_lines:
        fig.add_vline(x=epa_lines[col_key], line=dict(color="#ef4444", dash="dot", width=2),
                      annotation_text="EPA 24h", annotation_position="top right")
    fig.update_layout(title=title, xaxis_title=col,
                      yaxis_title="Exceedance Frequency (%)", template="plotly_white")
    return _to_json(fig)


def _trend(df, params):
    col = params.get("column", "pm2.5")
    dt = next((c for c in df.columns if str(c).lower() in {"datetime", "timestamp", "date", "time"}), df.columns[0])
    if str(dt).lower() not in {"datetime", "timestamp", "date", "time", "utcdatetime", "captured_at", "created_at"}:
        raise ValueError("Trend analysis requires a recognized datetime column.")
    work = pd.DataFrame({"time": pd.to_datetime(df[dt], errors="coerce"),
                         "value": pd.to_numeric(df[col], errors="coerce")}).dropna()
    if len(work) < 3:
        raise ValueError("Trend analysis requires at least three valid dated observations.")
    work = work.sort_values("time")
    x = (work.time - work.time.min()).dt.total_seconds() / 86400
    fit_frame = work.iloc[np.linspace(0, len(work)-1, min(2000, len(work)), dtype=int)]
    fit_x = (fit_frame.time - work.time.min()).dt.total_seconds() / 86400
    fit = theilslopes(fit_frame.value.to_numpy(), fit_x.to_numpy())
    display = work.iloc[np.linspace(0, len(work)-1, min(10000, len(work)), dtype=int)]
    display_x = (display.time - work.time.min()).dt.total_seconds() / 86400
    fig = go.Figure([go.Scattergl(x=display.time, y=display.value, mode="markers", name="Observations",
                                marker={"size": 5, "opacity": .45}),
                     go.Scatter(x=display.time, y=fit.intercept + fit.slope*display_x, mode="lines",
                                name=f"Theil–Sen trend ({fit.slope:.3g}/day; fit n={len(fit_frame):,})")])
    fig.update_layout(title=params.get("title", f"Trend Analysis — {col}"), xaxis_title="Date/Time",
                      yaxis_title=col, template="plotly_white")
    return _to_json(fig)


def _percentile(df, params):
    col = params.get("column", "pm2.5")
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty: raise ValueError("Selected column has no valid numeric observations.")
    pct = np.arange(1, 100)
    vals = np.percentile(s, pct)
    fig = go.Figure(go.Scatter(x=pct, y=vals, mode="lines", name=col))
    fig.update_layout(title=params.get("title", f"Percentile Plot — {col}"), xaxis_title="Percentile",
                      yaxis_title=col, template="plotly_white")
    return _to_json(fig)


def _wind_columns(df, params):
    low = {str(c).lower(): c for c in df.columns}
    wd = params.get("wd_col") or next((low[k] for k in ("wind_direction", "wind_direc", "winddirection", "wd") if k in low), None)
    ws = params.get("ws_col") or next((low[k] for k in ("windspeed", "wind_speed", "ws") if k in low), None)
    return ws, wd


def _wind_sector(direction, angle=22.5):
    angle = min(45.0, max(5.0, float(angle)))
    return (np.floor((direction + angle / 2) / angle) * angle) % 360


def _polar_environment(df, params, mode):
    col = params.get("column", "pm2.5")
    ws, wd = _wind_columns(df, params)
    if not wd or wd not in df:
        raise ValueError("Select an available wind direction column.")
    needs_speed = mode == "polar"
    if needs_speed and (not ws or ws not in df):
        raise ValueError("Select an available wind speed column.")
    value_col = params.get("column", "pm2.5")
    if value_col not in df: raise ValueError(f"Pollutant column '{value_col}' is unavailable.")
    data = pd.DataFrame({"wd": pd.to_numeric(df[wd], errors="coerce") % 360,
                         "ws": pd.to_numeric(df[ws], errors="coerce") if ws and ws in df else np.nan,
                         "value": pd.to_numeric(df[value_col], errors="coerce")})
    data = data.replace([np.inf, -np.inf], np.nan).dropna(subset=["wd", "value"])
    if needs_speed: data = data[data.ws >= 0].dropna(subset=["ws"])
    if data.empty: raise ValueError("No valid pollutant, wind speed and wind direction records were found.")

    if mode == "polar":
        # Bin in wind-vector space, weight each smoothed cell by its record count.
        # This yields a stable, interpretable surface without fitting beyond observed conditions.
        theta = np.deg2rad(data.wd.to_numpy())
        data["u"] = data.ws.to_numpy() * np.sin(theta)
        data["v"] = data.ws.to_numpy() * np.cos(theta)
        step = max(.2, float(params.get("wind_bin", .5)))
        data["ui"] = np.floor(data.u / step).astype(int)
        data["vi"] = np.floor(data.v / step).astype(int)
        cells = data.groupby(["ui", "vi"]).value.agg(["mean", "count"]).reset_index()
        lim = max(step, float(data.ws.quantile(.995)))
        n = int(np.clip(np.ceil(2 * lim / step) + 1, 20, 180))
        edges = np.linspace(-lim, lim, n + 1)
        sums = np.zeros((n, n)); weights = np.zeros((n, n))
        centers = (edges[:-1] + edges[1:]) / 2
        ix = np.clip(np.searchsorted(edges, (cells.ui + .5) * step) - 1, 0, n-1)
        iy = np.clip(np.searchsorted(edges, (cells.vi + .5) * step) - 1, 0, n-1)
        np.add.at(sums, (iy, ix), cells["mean"].to_numpy() * cells["count"].to_numpy())
        np.add.at(weights, (iy, ix), cells["count"].to_numpy())
        smooth_num = gaussian_filter(sums, sigma=max(.8, float(params.get("smooth", 1.4))))
        smooth_den = gaussian_filter(weights, sigma=max(.8, float(params.get("smooth", 1.4))))
        surface = np.divide(smooth_num, smooth_den, out=np.full_like(smooth_num, np.nan), where=smooth_den >= .5)
        surface[smooth_den < .5] = np.nan
        fig = go.Figure(go.Contour(x=centers, y=centers, z=surface, connectgaps=False,
            colorscale="Turbo", colorbar={"title": value_col}, contours={"coloring": "heatmap", "showlines": True},
            hovertemplate="u=%{x:.1f} m/s<br>v=%{y:.1f} m/s<br>Mean %{z:.3g}<extra></extra>"))
        fig.update_layout(title=params.get("title", f"Polar Plot — {value_col}"), template="plotly_white",
            xaxis={"title": "West ← eastward wind component → East", "scaleanchor": "y", "scaleratio": 1},
            yaxis={"title": "South ← wind component → North"},
            annotations=[{"text": "Wind direction is from; radius encodes wind speed. Smoothed binned means; sparse cells remain blank.",
                          "xref": "paper", "yref": "paper", "x": 0, "y": -0.18, "showarrow": False, "font": {"size": 10}}])
        return _to_json(fig)

    data["sector"] = _wind_sector(data.wd, float(params.get("angle", 22.5)))
    if mode == "cpf":
        threshold = float(params["threshold"]) if params.get("threshold") not in (None, "") else float(data.value.quantile(.75))
        by_sector = data.assign(hit=data.value >= threshold).groupby("sector").hit.agg(["mean", "count"])
        by_sector = by_sector[by_sector["count"] >= int(params.get("min_count", 3))]
        fig = go.Figure(go.Barpolar(theta=by_sector.index, r=by_sector["mean"], name="P(concentration ≥ threshold)",
                                    customdata=by_sector["count"], hovertemplate="From %{theta:.0f}°<br>CPF %{r:.1%}<br>n=%{customdata}<extra></extra>"))
        fig.update_layout(title=params.get("title", f"CPF — {value_col} ≥ {threshold:g}"),
                          polar={"radialaxis": {"range": [0, 1], "tickformat": ".0%"}})
    elif mode == "pollutionrose":
        quantiles = np.linspace(0, 1, int(params.get("pollutant_bins", 5)) + 1)
        edges = np.unique(data.value.quantile(quantiles).to_numpy())
        if len(edges) < 3:
            epsilon = max(abs(float(data.value.min())) * 1e-9, 1e-9)
            edges = np.linspace(data.value.min(), data.value.max() + epsilon, max(3, len(quantiles)))
        labels = [f"{edges[i]:.3g}–{edges[i+1]:.3g}" for i in range(len(edges)-1)]
        data["pollutant_range"] = pd.cut(data.value, edges, labels=labels, include_lowest=True, duplicates="drop")
        summary = data.groupby(["sector", "pollutant_range"], observed=False).size().unstack(fill_value=0)
        summary = summary.div(len(data)).mul(100)
        fig = go.Figure()
        for label in labels:
            if label in summary:
                fig.add_trace(go.Barpolar(theta=summary.index, r=summary[label], name=label, opacity=.92,
                                          width=float(params.get("angle", 22.5))))
        fig.update_layout(title=params.get("title", f"Pollution Rose — {value_col}"), barmode="stack",
            polar={"angularaxis": {"direction": "clockwise", "rotation": 90}, "radialaxis": {"title": "% of all records", "ticksuffix": "%"}},
            legend_title=f"{value_col} range")
    else:
        dt = _datetime_column(df)
        if mode == "annulus" and (dt not in df or str(dt).lower() not in {"datetime", "timestamp", "date", "time", "utcdatetime", "captured_at", "created_at"}):
            raise ValueError("Annulus plots require a recognized datetime column.")
        time = pd.to_datetime(df[dt], errors="coerce") if dt in df else pd.Series(pd.NaT, index=df.index)
        period = params.get("period", "hour")
        keys = {
            "hour": time.dt.hour,
            "weekday": time.dt.dayofweek,
            "season": time.dt.month.map({12:"Winter",1:"Winter",2:"Winter",3:"Spring",4:"Spring",5:"Spring",6:"Summer",7:"Summer",8:"Summer",9:"Autumn",10:"Autumn",11:"Autumn"}),
            "month": time.dt.month,
            "trend": time.dt.year,
        }
        data["period"] = keys.get(period, keys["hour"]).reindex(data.index)
        data = data.dropna(subset=["period"])
        summary = data.groupby(["period", "sector"], observed=True).value.agg(["mean", "count"])
        period_order = sorted(summary.index.get_level_values(0).unique(), key=str)
        if period == "weekday": period_order = [d for d in range(7) if d in period_order]
        elif period == "season": period_order = [s for s in ("Winter", "Spring", "Summer", "Autumn") if s in period_order]
        elif period in ("hour", "month", "trend"): period_order = sorted(period_order)
        min_count = int(params.get("min_count", 1))
        fig = go.Figure()
        for ring, label in enumerate(period_order, 1):
            band = summary.xs(label, level="period")
            band = band[band["count"] >= min_count]
            if band.empty: continue
            fig.add_trace(go.Scatterpolar(theta=band.index, r=np.full(len(band), ring), mode="markers",
                marker={"size": 15, "color": band["mean"], "colorscale": "Turbo", "cmin": float(data.value.min()),
                        "cmax": float(data.value.max()), "showscale": ring == 1, "colorbar": {"title": value_col}},
                customdata=np.column_stack([band["mean"], band["count"]]),
                hovertemplate="From %{theta:.0f}°<br>Mean %{customdata[0]:.3g}<br>n=%{customdata[1]}<extra></extra>",
                name=str(label)))
        ticktext = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if period == "weekday" else [str(x) for x in period_order]
        fig.update_layout(title=params.get("title", f"Polar Annulus — {value_col} by {period}"), template="plotly_white",
            polar={"angularaxis": {"direction": "clockwise", "rotation": 90},
                   "radialaxis": {"tickvals": list(range(1, len(period_order)+1)), "ticktext": ticktext, "showticklabels": True, "title": period.title()}})
    return _to_json(fig)


def _polar(df, params): return _polar_environment(df, params, "polar")
def _pollutionrose(df, params): return _polar_environment(df, params, "pollutionrose")
def _annulus(df, params): return _polar_environment(df, params, "annulus")
def _cpf(df, params): return _polar_environment(df, params, "cpf")
def _bivariate_polar(df, params): return _polar_environment(df, params, "polar")


# ── Extended plot catalogue (SVG / heatmap based, no WebGL) ───────────────────
from scipy.stats import gaussian_kde, probplot
from scipy.cluster.vq import kmeans2


_MAX_PLOT_ROWS = 50_000
_MAX_PAIR_DIMS = 8
_MAX_PAIR_POINTS = 2_000
_KNOWN_DT = {"datetime", "timestamp", "date", "time", "utcdatetime", "captured_at", "created_at"}
_CHART_COLORS = [
    "#5B8FF9", "#61DDAA", "#65789B", "#F6BD16", "#7262FD",
    "#78D3F8", "#9661BC", "#F6903D", "#008685", "#F08BB4",
]
_NUMERIC_DEFAULTS = (
    "pm2.5", "pm10", "NO2", "O3", "CO", "SO2", "CO2", "VOC",
    "temperature", "RH", "pm1.0", "NOx", "windspeed", "pressure",
)
_MONTH_TO_SEASON = {
    1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn",
    11: "Autumn", 12: "Winter",
}
_TEMPORAL_KEYS = {
    "hour", "hours", "day", "date", "daily", "weekday", "dow", "month",
    "monthly", "season", "seasons", "year", "annual", "week", "weekly",
}


def _rgba(hex_color, alpha):
    """Convert a #RRGGBB colour to an rgba() string for Plotly fills."""
    text = str(hex_color).lstrip("#")
    if len(text) != 6:
        return str(hex_color)
    try:
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return str(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def _clean_numeric(values):
    """Numeric numpy vector with non-finite values removed."""
    series = pd.to_numeric(pd.Series(values), errors="coerce")
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    return series.to_numpy(dtype=float)


def _sample_rows(df, cap=_MAX_PLOT_ROWS):
    """Deterministic bounded sample so large uploads stay responsive."""
    if len(df) <= cap:
        return df.copy()
    index = np.linspace(0, len(df) - 1, int(cap), dtype=int)
    return df.iloc[index].copy()


def _selected_columns(df, params, default_candidates, limit=None):
    """Resolve a list of (numeric) columns from params with clear errors."""
    raw = params.get("columns") or params.get("cols")
    if not raw:
        single = params.get("column") or params.get("value_col")
        raw = [single] if single else list(default_candidates)
    if isinstance(raw, str):
        raw = [raw]
    columns = []
    for col in raw:
        if col in df.columns and col not in columns:
            columns.append(col)
    if limit:
        columns = columns[:int(limit)]
    if not columns:
        raise ValueError(
            "None of the requested columns are available. "
            f"Available columns: {list(df.columns)[:20]}"
        )
    return columns


def _temporal_series(df, period="hour", dt_col=None):
    """Derive a categorical period label from a recognised datetime column."""
    dt_col = dt_col or _datetime_column(df)
    dt = pd.to_datetime(df[dt_col], errors="coerce")
    key = str(period).lower()
    if key in ("hour", "hours"):
        return dt.dt.hour
    if key in ("day", "date", "daily"):
        return dt.dt.strftime("%Y-%m-%d")
    if key in ("weekday", "dow"):
        return dt.dt.day_name()
    if key in ("month", "monthly"):
        return dt.dt.month_name()
    if key in ("season", "seasons"):
        return dt.dt.month.map(_MONTH_TO_SEASON)
    if key in ("year", "annual"):
        return dt.dt.year
    if key in ("week", "weekly"):
        return dt.dt.strftime("%G-W%V")
    return dt


def _resolve_group(df, params, preferred=(), default=None, required=False):
    """Resolve an optional/required grouping column, deriving temporal keys."""
    col = params.get("group_by") or params.get("color_col") or params.get("category_col")
    if isinstance(col, (list, tuple)):
        col = col[0] if col else None
    work = df
    if not col:
        col = next((c for c in preferred if c in df.columns), default)
    if not col:
        if required:
            raise ValueError(
                "A grouping column is required. Supply 'group_by' with one of: "
                f"{list(df.columns)[:20]}"
            )
        return work, None
    if str(col) in _TEMPORAL_KEYS and col not in work.columns:
        work = work.copy()
        work[col] = _temporal_series(work, str(col))
    if col not in work.columns:
        if required:
            raise ValueError(
                f"Grouping column '{col}' was not found. "
                f"Available columns: {list(df.columns)[:20]}"
            )
        return df, None
    return work, col


def _numeric_frame(df, columns, sample=True):
    """Copy, coerce selected columns to numeric and drop incomplete rows."""
    work = _sample_rows(df) if sample else df.copy()
    for col in columns:
        if col not in work.columns:
            raise ValueError(f"Selected column '{col}' was not found.")
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=columns)
    return work


def _density_curve(values, grid, bw_adjust=1.0):
    """Gaussian KDE with a deterministic normal fallback for degenerate data."""
    vector = _clean_numeric(pd.Series(values))
    if vector.size == 0:
        return np.zeros_like(grid, dtype=float)
    scale = max(0.05, float(bw_adjust))
    if vector.size < 3 or np.ptp(vector) <= 0:
        center = float(vector[0])
        spread = max(abs(center) * 0.02, 1e-6) * scale
        return np.exp(-0.5 * ((grid - center) / spread) ** 2)
    try:
        kde = gaussian_kde(vector)
        kde.set_bandwidth(kde.factor * scale)
        return np.asarray(kde(grid), dtype=float)
    except Exception:
        mu = float(np.mean(vector))
        sd = max(float(np.std(vector)), 1e-6) * scale
        return np.exp(-0.5 * ((grid - mu) / sd) ** 2)


def _autocorrelation(values, lags):
    """Centred autocorrelation function for lags 0..lags."""
    vector = _clean_numeric(pd.Series(values))
    if vector.size < 3:
        return np.full(int(lags) + 1, np.nan)
    centred = vector - vector.mean()
    denominator = float(np.dot(centred, centred))
    if denominator <= 0:
        return np.full(int(lags) + 1, np.nan)
    n = len(centred)
    result = np.full(int(lags) + 1, np.nan)
    for lag in range(int(lags) + 1):
        if lag == 0:
            result[lag] = 1.0
        elif lag < n:
            result[lag] = float(np.dot(centred[:-lag], centred[lag:]) / denominator)
    return result


def _infer_period(times):
    """Guess a sensible seasonal period from the median sampling interval."""
    unique = pd.Series(times).dropna().drop_duplicates().sort_values()
    if len(unique) < 2:
        return 24
    hours = unique.diff().dropna().dt.total_seconds().median() / 3600.0
    if hours <= 2:
        return 24
    if hours <= 25:
        return 7
    if hours <= 24 * 45:
        return 12
    return 7


def _hierarchy_frame(df, params):
    """Aggregated frame + path used by treemap and sunburst generators."""
    path = params.get("path") or params.get("group_by") or params.get("columns")
    if isinstance(path, str):
        path = [path]
    work = _sample_rows(df)
    path = [c for c in (path or []) if c in work.columns]
    if not path:
        if "location" in work.columns:
            path = ["location"]
        else:
            work = work.copy()
            work["Period"] = _temporal_series(work, "month").astype(str)
            path = ["Period"]
    value_col = (params.get("values") or params.get("value_col")
                 or params.get("column") or "pm2.5")
    if value_col not in work.columns:
        raise ValueError(f"Value column '{value_col}' was not found.")
    for col in path:
        work[col] = work[col].astype("string").fillna("Missing").astype(str)
    work["_value"] = pd.to_numeric(work[value_col], errors="coerce")
    work = work.dropna(subset=["_value"])
    if work.empty:
        raise ValueError(f"Column '{value_col}' has no usable numeric values.")
    aggfunc = params.get("aggfunc", "mean")
    try:
        grouped = work.groupby(path, dropna=False)["_value"].agg(aggfunc).reset_index()
    except Exception:
        grouped = work.groupby(path, dropna=False)["_value"].mean().reset_index()
    grouped = grouped.dropna(subset=["_value"])
    if grouped.empty:
        raise ValueError("No aggregated hierarchy values are available.")
    values = grouped["_value"].to_numpy(dtype=float)
    positive = values[np.isfinite(values) & (values > 0)]
    floor = float(np.min(positive)) if positive.size else 1.0
    sizes = np.where(np.isfinite(values) & (values > 0), values, floor)
    grouped["_size"] = np.clip(sizes, max(floor * 1e-6, 1e-12), None)
    return grouped, path, value_col


def _category_summary(df, params, preferred=("location",)):
    """Aggregated, sorted category summary for ranked bar/dot charts."""
    value_col = params.get("value_col") or params.get("column") or "pm2.5"
    if value_col not in df.columns:
        raise ValueError(f"Selected value column '{value_col}' was not found.")
    work, cat_col = _resolve_group(df, params, preferred=preferred,
                                   default="location", required=True)
    frame = work[[cat_col, value_col]].copy()
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        raise ValueError(f"Column '{value_col}' has no usable numeric values.")
    aggfunc = params.get("aggfunc", "mean")
    try:
        summary = frame.groupby(cat_col, dropna=False)[value_col].agg(aggfunc).reset_index()
    except Exception:
        summary = frame.groupby(cat_col, dropna=False)[value_col].mean().reset_index()
    summary = summary.sort_values(value_col, ascending=True)
    top_n = int(params.get("top_n", 15))
    if top_n > 0:
        summary = summary.tail(top_n)
    return summary, cat_col, value_col


def _ranked_y(summary):
    """Numeric y positions and matching labels for horizontal ranked charts."""
    labels = [str(value) for value in summary.iloc[:, 0].tolist()]
    return np.arange(len(labels), dtype=float), labels


def _ridgeline(df, params):
    """Overlapping kernel-density ridges for several numeric measures."""
    cols = _selected_columns(
        df, params,
        ("pm2.5", "pm10", "NO2", "O3", "CO2", "temperature"), limit=12,
    )
    data = _sample_rows(df)
    bw = float(params.get("bw_adjust", 1.0))
    step = float(params.get("spacing", 1.0))
    pooled = np.concatenate([_clean_numeric(data[col]) for col in cols])
    if pooled.size == 0:
        raise ValueError("The selected columns have no usable numeric values.")
    low, high = np.quantile(pooled, [0.001, 0.999])
    if not np.isfinite(low) or not np.isfinite(high) or low == high:
        low, high = float(low) - 1.0, float(high) + 1.0
    grid = np.linspace(low, high, 200)
    fig = go.Figure()
    for index, col in enumerate(cols):
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        density = _density_curve(data[col], grid, bw)
        peak = float(np.max(density)) if density.size else 0.0
        peak = peak if np.isfinite(peak) and peak > 0 else 1.0
        baseline = np.full_like(grid, index * step, dtype=float)
        curve = baseline + density / peak * step * 0.9
        fig.add_trace(go.Scatter(
            x=grid, y=baseline, mode="lines", showlegend=False, hoverinfo="skip",
            line=dict(color="#9aa4b2", width=1),
        ))
        fig.add_trace(go.Scatter(
            x=grid, y=curve, mode="lines", name=str(col), fill="tonexty",
            fillcolor=_rgba(color, 0.55), line=dict(color=color, width=2),
        ))
    fig.update_layout(
        title=params.get("title", "Ridgeline — " + ", ".join(map(str, cols))),
        xaxis_title="Value", yaxis_title="Density (offset)", template="plotly_white",
        height=max(420, 90 * len(cols)), hovermode="x",
    )
    return _to_json(fig)


def _density(df, params):
    """Kernel-density curves by column and optional group."""
    cols = _selected_columns(df, params, ("pm2.5", "pm10", "temperature"), limit=8)
    data = _sample_rows(df)
    work, group_col = _resolve_group(data, params, preferred=(), default=None)
    groups = [(None, work)] if group_col is None else list(work.groupby(group_col, dropna=False))[:12]
    pooled = np.concatenate([_clean_numeric(part[col]) for _, part in groups for col in cols])
    if pooled.size == 0:
        raise ValueError("The selected columns have no usable numeric values.")
    low, high = np.quantile(pooled, [0.001, 0.999])
    if low == high:
        low, high = low - 1.0, high + 1.0
    grid = np.linspace(low, high, 220)
    bw = float(params.get("bw_adjust", 1.0))
    fig = go.Figure()
    for col_index, col in enumerate(cols):
        color = _CHART_COLORS[col_index % len(_CHART_COLORS)]
        for group_index, (name, part) in enumerate(groups):
            density = _density_curve(part[col], grid, bw)
            label = str(col) if name is None else f"{col} · {name}"
            fig.add_trace(go.Scatter(
                x=grid, y=density, mode="lines", name=label, fill="tozeroy",
                fillcolor=_rgba(color, 0.22),
                line=dict(color=color, width=2, dash="solid" if group_index == 0 else "dot"),
                hovertemplate=f"{label}<br>Value %{{x:.3g}}<br>Density %{{y:.3g}}<extra></extra>",
            ))
    fig.update_layout(
        title=params.get("title", "Density Plot"), xaxis_title="Value",
        yaxis_title="Density", template="plotly_white",
    )
    return _to_json(fig)


def _ecdf(df, params):
    """Empirical cumulative distribution functions (step curves)."""
    cols = _selected_columns(df, params, (params.get("column") or "pm2.5",), limit=8)
    data = _sample_rows(df)
    work, group_col = _resolve_group(data, params, preferred=(), default=None)
    groups = [(None, work)] if group_col is None else list(work.groupby(group_col, dropna=False))[:12]
    fig = go.Figure()
    color_index = 0
    for col in cols:
        for name, part in groups:
            values = np.sort(_clean_numeric(part[col]))
            if values.size == 0:
                continue
            cdf = np.arange(1, values.size + 1) / values.size
            color = _CHART_COLORS[color_index % len(_CHART_COLORS)]
            color_index += 1
            label = str(col) if name is None else f"{col} · {name}"
            fig.add_trace(go.Scatter(
                x=values, y=cdf, mode="lines", name=label, line_shape="hv",
                line=dict(color=color, width=2),
                hovertemplate=f"{label}<br>≤ %{{x:.3g}}: %{{y:.1%}}<extra></extra>",
            ))
    if not fig.data:
        raise ValueError("The selected columns have no usable numeric values.")
    for level in (0.25, 0.5, 0.75):
        fig.add_hline(y=level, line=dict(color="#e5e7eb", width=1),
                      annotation_text=f"{level:.0%}", annotation_position="left")
    fig.update_layout(
        title=params.get("title", "Empirical CDF"), xaxis_title="Value",
        yaxis_title="Cumulative probability", yaxis=dict(range=[0, 1.02]),
        template="plotly_white",
    )
    return _to_json(fig)


def _beeswarm(df, params):
    """Swarm-style jittered points for a numeric measure by category."""
    col = params.get("column") or params.get("value_col") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    work, group_col = _resolve_group(df, params, preferred=("location",),
                                     default="location", required=True)
    max_points = max(5, int(params.get("points_per_group", 400)))
    rng = np.random.default_rng(2026)
    work = work.copy()
    work["_value"] = pd.to_numeric(work[col], errors="coerce")
    work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=["_value"])
    if work.empty:
        raise ValueError(f"Column '{col}' has no usable numeric values.")
    fig = go.Figure()
    names = sorted([str(name) for name in work[group_col].dropna().unique()])
    for group_index, name in enumerate(names):
        part = work[work[group_col].astype(str) == name].sort_values("_value")
        values = part["_value"].to_numpy(dtype=float)
        if values.size > max_points:
            values = values[np.linspace(0, values.size - 1, max_points, dtype=int)]
        n = values.size
        offsets = np.zeros(n, dtype=float)
        order = np.argsort(values)
        for position, original_index in enumerate(order):
            direction = 1.0 if position % 2 == 0 else -1.0
            magnitude = ((position // 2) + 1) / max(n / 2.0, 1.0) * 0.32
            offsets[original_index] = direction * magnitude
        jitter = rng.normal(0, 0.025, n)
        x = np.full(n, group_index, dtype=float) + offsets + jitter
        fig.add_trace(go.Scatter(
            x=x, y=values, mode="markers", name=name,
            marker=dict(size=6, opacity=0.65,
                        color=_CHART_COLORS[group_index % len(_CHART_COLORS)]),
            customdata=np.full(n, name),
            hovertemplate=f"{name}<br>{col}: %{{y:.3g}}<extra></extra>",
        ))
    fig.update_layout(
        title=params.get("title", f"Beeswarm — {col}"),
        xaxis=dict(title=str(group_col), tickmode="array", tickvals=list(range(len(names))),
                   ticktext=names, zeroline=False),
        yaxis_title=str(col), template="plotly_white",
    )
    return _to_json(fig)


def _pairplot(df, params):
    """Scatter-plot matrix bounded to eight dimensions and 2k sampled points."""
    cols = _selected_columns(
        df, params,
        ("pm2.5", "pm10", "NO2", "O3", "temperature", "RH", "CO2", "VOC"),
        limit=_MAX_PAIR_DIMS,
    )
    if len(cols) < 2:
        raise ValueError("A pairplot needs at least two numeric columns.")
    data = _numeric_frame(df, cols)
    if len(data) > _MAX_PAIR_POINTS:
        data = data.iloc[np.linspace(0, len(data) - 1, _MAX_PAIR_POINTS, dtype=int)]
    n = len(cols)
    fig = make_subplots(rows=n, cols=n, horizontal_spacing=0.04, vertical_spacing=0.04)
    for row in range(n):
        for col_idx in range(n):
            a, b = cols[row], cols[col_idx]
            axis = row * n + col_idx + 1
            if row == col_idx:
                fig.add_trace(go.Histogram(
                    x=data[a], nbinsx=20, showlegend=False,
                    marker=dict(color=_CHART_COLORS[row % len(_CHART_COLORS)]),
                ), row=row + 1, col=col_idx + 1)
            elif row > col_idx:
                fig.add_trace(go.Scatter(
                    x=data[b], y=data[a], mode="markers", showlegend=False,
                    marker=dict(size=3, opacity=0.45,
                                color=_CHART_COLORS[row % len(_CHART_COLORS)]),
                    hovertemplate=f"{b}: %{{x:.3g}}<br>{a}: %{{y:.3g}}<extra></extra>",
                ), row=row + 1, col=col_idx + 1)
            else:
                coefficient = data[a].corr(data[b])
                text = "r = n/a" if pd.isna(coefficient) else f"r = {coefficient:.2f}"
                fig.add_annotation(
                    x=0.5, y=0.5, xref=f"x{axis} domain", yref=f"y{axis} domain",
                    text=text, showarrow=False, font=dict(size=11, color="#4b5563"),
                )
            if row == n - 1:
                fig.update_xaxes(title_text=str(b), row=row + 1, col=col_idx + 1)
            if col_idx == 0:
                fig.update_yaxes(title_text=str(a), row=row + 1, col=col_idx + 1)
    fig.update_layout(
        title=params.get("title", "Pairplot"), template="plotly_white",
        height=max(460, 145 * n), showlegend=False,
    )
    return _to_json(fig)


def _correlogram(df, params):
    """Autocorrelation correlogram with a 95% white-noise reference band."""
    cols = _selected_columns(df, params, (params.get("column") or "pm2.5",), limit=4)
    data = _sample_rows(df)
    lags = int(np.clip(int(params.get("lags", 24)), 1, 120))
    fig = go.Figure()
    longest = 0
    for col_index, col in enumerate(cols):
        values = _clean_numeric(data[col])
        if values.size < 3:
            continue
        longest = max(longest, values.size)
        acf = _autocorrelation(values, lags)
        color = _CHART_COLORS[col_index % len(_CHART_COLORS)]
        for lag in range(lags + 1):
            if not np.isfinite(acf[lag]):
                continue
            fig.add_trace(go.Scatter(
                x=[lag, lag], y=[0, acf[lag]], mode="lines", showlegend=False,
                line=dict(color=color, width=2), hoverinfo="skip",
            ))
        fig.add_trace(go.Scatter(
            x=list(range(lags + 1)), y=acf, mode="markers", name=str(col),
            marker=dict(color=color, size=7),
        ))
    if not fig.data:
        raise ValueError("At least three numeric observations are required for a correlogram.")
    confidence = 1.96 / np.sqrt(max(longest, 2))
    fig.add_hline(y=0, line=dict(color="#9aa4b2", width=1))
    fig.add_hline(y=confidence, line=dict(color="#ef4444", width=1, dash="dash"),
                  annotation_text="95%", annotation_position="top left")
    fig.add_hline(y=-confidence, line=dict(color="#ef4444", width=1, dash="dash"))
    fig.update_layout(
        title=params.get("title", "Correlogram — autocorrelation"),
        xaxis_title="Lag", yaxis_title="Autocorrelation", template="plotly_white",
    )
    return _to_json(fig)


def _hexbin(df, params):
    """SVG hexagonal binning: hexagon markers coloured by record count."""
    numeric_candidates = [c for c in _NUMERIC_DEFAULTS if c in df.columns]
    numeric_candidates += [c for c in df.columns
                           if pd.api.types.is_numeric_dtype(df[c]) and c not in numeric_candidates]
    y_col = params.get("y_col") or params.get("column")
    if not y_col:
        y_col = next((c for c in ("pm10", "pm2.5") if c in numeric_candidates),
                     numeric_candidates[0] if numeric_candidates else None)
    x_col = params.get("x_col") or next(
        (c for c in ("pm2.5", "pm10", "temperature") if c in numeric_candidates and c != y_col),
        next((c for c in numeric_candidates if c != y_col), None))
    if not x_col or not y_col or x_col not in df.columns or y_col not in df.columns:
        raise ValueError(
            f"Available numeric x_col/y_col are required (got '{x_col}' / '{y_col}')."
        )
    data = _sample_rows(df)

    def _axis_values(series, col):
        if str(col).lower() in _KNOWN_DT:
            parsed = pd.to_datetime(series, errors="coerce")
            return (parsed.astype("int64").to_numpy(dtype=float) / 1e9)[parsed.notna().to_numpy()]
        return _clean_numeric(series)

    x = _axis_values(data[x_col], x_col)
    y = _axis_values(data[y_col], y_col)
    length = min(len(x), len(y))
    if length == 0:
        raise ValueError("The selected columns have no numeric pairs to bin.")
    x, y = x[:length], y[:length]
    bins = int(np.clip(int(params.get("bins", 24)), 6, 80))
    span_x = max(float(np.ptp(x)), 1e-12)
    span_y = max(float(np.ptp(y)), 1e-12)
    radius = max(span_x / (np.sqrt(3) * bins), span_y / (1.5 * bins), 1e-12)
    width = np.sqrt(3) * radius
    vstep = 1.5 * radius
    x0, y0 = float(np.min(x)), float(np.min(y))
    column0 = np.round((x - x0) / width).astype(int)
    candidates = np.column_stack([column0 - 1, column0, column0 + 1])
    offsets = np.where(candidates % 2 == 0, 0.0, 0.75 * radius)
    rows = np.round((y[:, None] - y0 - offsets) / vstep).astype(int)
    center_x = x0 + candidates * width
    center_y = y0 + offsets + rows * vstep
    distance = (x[:, None] - center_x) ** 2 + (y[:, None] - center_y) ** 2
    best = np.argmin(distance, axis=1)
    positions = np.arange(length)
    chosen = np.column_stack([center_x[positions, best], center_y[positions, best]])
    centers, counts = np.unique(np.round(chosen, 9), axis=0, return_counts=True)
    fig = go.Figure(go.Scatter(
        x=centers[:, 0], y=centers[:, 1], mode="markers",
        marker=dict(
            symbol="hexagon",
            size=float(params.get("marker_size", 16)),
            color=counts, colorscale="Turbo",
            colorbar=dict(title="Count"), line=dict(width=0),
        ),
        customdata=counts,
        hovertemplate=f"{x_col}: %{{x:.3g}}<br>{y_col}: %{{y:.3g}}<br>n=%{{customdata}}<extra></extra>",
        name="Hex bins",
    ))
    fig.update_layout(
        title=params.get("title", f"Hexbin — {x_col} vs {y_col}"),
        xaxis_title=str(x_col), yaxis_title=str(y_col), template="plotly_white",
    )
    return _to_json(fig)


def _connected_scatter(df, params):
    """XY points joined with lines per group (e.g. station trajectories)."""
    y_col = params.get("y_col") or params.get("column") or "pm2.5"
    if y_col not in df.columns:
        raise ValueError(f"Selected y_col '{y_col}' was not found.")
    x_col = params.get("x_col")
    if not x_col:
        x_col = next((c for c in df.columns
                      if str(c).lower() in _KNOWN_DT and c != y_col), None)
        if x_col is None:
            x_col = next((c for c in df.columns
                          if c != y_col and pd.api.types.is_numeric_dtype(df[c])), None)
    if not x_col or x_col not in df.columns:
        raise ValueError("Select an available x_col for the connected scatter.")
    work, group_col = _resolve_group(df, params, preferred=("location",),
                                     default="location", required=True)
    work = work.copy()
    if str(x_col).lower() in _KNOWN_DT:
        work[x_col] = pd.to_datetime(work[x_col], errors="coerce")
    work[y_col] = pd.to_numeric(work[y_col], errors="coerce")
    work = work.dropna(subset=[x_col, y_col])
    fig = go.Figure()
    plotted = 0
    for name, part in work.groupby(group_col, dropna=False):
        part = _sample_rows(part.sort_values(x_col))
        if part.empty:
            continue
        plotted += len(part)
        fig.add_trace(go.Scatter(
            x=part[x_col], y=part[y_col], mode="lines+markers", name=str(name),
            line=dict(width=1.5, color=_CHART_COLORS[len(fig.data) % len(_CHART_COLORS)]),
            marker=dict(size=4),
            customdata=np.full(len(part), str(name)),
            hovertemplate=f"{name}<br>{x_col}: %{{x}}<br>{y_col}: %{{y:.3g}}<extra></extra>",
        ))
    if not plotted:
        raise ValueError("No valid paired observations are available for the connected scatter.")
    fig.update_layout(
        title=params.get("title", f"Connected Scatter — {y_col} by {group_col}"),
        xaxis_title=str(x_col), yaxis_title=str(y_col), template="plotly_white",
    )
    return _to_json(fig)


def _streamgraph(df, params):
    """Centred stacked area streamgraph built from SVG scatter traces."""
    local = dict(params)
    for key in ("color_col", "facet_col"):
        local.pop(key, None)
    long_df, value_cols, dt_col, _, _, palette = _time_series_frame(
        df, local, default_resample="D")
    long_df = long_df.sort_values(dt_col)
    pivot = long_df.pivot_table(index=dt_col, columns="_measure", values="_value",
                                aggfunc="mean").fillna(0.0).sort_index()
    if pivot.empty:
        raise ValueError("No usable values for the streamgraph.")
    values = pivot.to_numpy(dtype=float)
    lower = np.cumsum(values, axis=1) - values
    upper = lower + values
    lower = lower - (np.cumsum(values, axis=1)[:, -1:] / 2.0)
    upper = upper - (np.cumsum(values, axis=1)[:, -1:] / 2.0)
    fig = go.Figure()
    for index, measure in enumerate(pivot.columns):
        color = palette[index % len(palette)] if palette else _CHART_COLORS[index % len(_CHART_COLORS)]
        fig.add_trace(go.Scatter(
            x=pivot.index, y=lower[:, index], mode="lines", showlegend=False,
            hoverinfo="skip", line=dict(width=0),
        ))
        fig.add_trace(go.Scatter(
            x=pivot.index, y=upper[:, index], mode="lines", name=str(measure),
            fill="tonexty", fillcolor=_rgba(color, 0.75),
            line=dict(color=color, width=1),
        ))
    fig.update_layout(
        title=params.get("title", "Streamgraph"), xaxis_title="Date/Time",
        yaxis_title="Centred stacked value", template="plotly_white",
    )
    return _to_json(fig)


def _rolling_trend(df, params):
    """Raw observations with a rolling mean and ±k·rolling-sd envelope."""
    cols = _selected_columns(df, params, ("pm2.5",), limit=4)
    local = dict(params)
    local["columns"] = cols
    for key in ("color_col", "facet_col"):
        local.pop(key, None)
    long_df, value_cols, dt_col, _, _, palette = _time_series_frame(df, local)
    long_df = long_df.sort_values(dt_col)
    window = params.get("window", 24)
    if isinstance(window, str) and not window.strip().isdigit():
        pass
    else:
        window = max(1, int(window))
    k = float(params.get("k", 1.96))
    fig = go.Figure()
    for index, measure in enumerate(value_cols):
        part = long_df[long_df["_measure"] == measure]
        series = part.set_index(dt_col)["_value"].sort_index()
        rolling = series.rolling(window=window, min_periods=1)
        mean = rolling.mean()
        spread = rolling.std().fillna(0.0) * k
        color = palette[index % len(palette)] if palette else _CHART_COLORS[index % len(_CHART_COLORS)]
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines", name=f"{measure} (raw)",
            line=dict(color="#9aa4b2", width=1), opacity=0.7,
        ))
        fig.add_trace(go.Scatter(
            x=mean.index, y=(mean + spread).values, mode="lines", showlegend=False,
            line=dict(width=0), hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=mean.index, y=(mean - spread).values, mode="lines",
            name=f"{measure} ±{k:g}σ", fill="tonexty",
            fillcolor=_rgba(color, 0.18), line=dict(width=0),
        ))
        fig.add_trace(go.Scatter(
            x=mean.index, y=mean.values, mode="lines", name=f"{measure} rolling mean",
            line=dict(color=color, width=2),
        ))
    fig.update_layout(
        title=params.get("title", "Rolling Trend"), xaxis_title="Date/Time",
        yaxis_title="Value", template="plotly_white",
    )
    return _to_json(fig)


def _seasonal_decomposition(df, params):
    """Classical moving-average decomposition: observed/trend/seasonal/residual."""
    col = params.get("column") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    dt_col = _datetime_column(df)
    if str(dt_col).lower() not in _KNOWN_DT:
        raise ValueError("Seasonal decomposition requires a recognised datetime column.")
    work = pd.DataFrame({
        "time": pd.to_datetime(df[dt_col], errors="coerce"),
        "value": pd.to_numeric(df[col], errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("time")
    if len(work) < 8:
        raise ValueError("Seasonal decomposition needs at least eight valid dated observations.")
    raw_period = params.get("period")
    if raw_period in (None, "", "auto"):
        raw_period = _infer_period(work["time"])
    period = int(np.clip(int(raw_period), 2, max(2, len(work) // 2)))
    series = work.set_index("time")["value"]
    trend = series.rolling(window=period, center=True,
                           min_periods=max(2, period // 2)).mean()
    detrended = series - trend
    phase = np.arange(len(detrended)) % period
    seasonal = pd.Series(
        detrended.groupby(phase).transform("mean").to_numpy(),
        index=detrended.index,
    )
    residual = series - trend - seasonal
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.045,
        subplot_titles=("Observed", f"Trend ( centred {period}-window )",
                        "Seasonal", "Residual"),
    )
    for row, (label, values, color) in enumerate((
        ("Observed", series, "#5B8FF9"),
        ("Trend", trend, "#ef4444"),
        ("Seasonal", seasonal, "#61DDAA"),
        ("Residual", residual, "#F6BD16"),
    ), start=1):
        fig.add_trace(go.Scatter(
            x=values.index, y=values.values, mode="lines", name=label,
            line=dict(color=color, width=1.6),
        ), row=row, col=1)
        fig.update_yaxes(title_text=label, row=row, col=1)
    fig.update_layout(
        title=params.get("title", f"Seasonal Decomposition — {col} (period={period})"),
        template="plotly_white", height=740, showlegend=False,
    )
    return _to_json(fig)


def _treemap(df, params):
    """Hierarchical treemap aggregated from categorical path columns."""
    grouped, path, value_col = _hierarchy_frame(df, params)
    fig = px.treemap(
        grouped, path=path, values="_size", color="_value",
        color_continuous_scale="Turbo", custom_data=path + ["_value"],
    )
    hover = "<br>".join(
        [f"{c}: %{{customdata[{i}]}}" for i, c in enumerate(path)]
        + [f"{value_col}: %{{customdata[{len(path)}]:.3g}}<extra></extra>"]
    )
    fig.update_traces(hovertemplate=hover)
    fig.update_layout(
        title=params.get("title", f"Treemap — {value_col}"), template="plotly_white",
    )
    return _to_json(fig)


def _sunburst(df, params):
    """Hierarchical sunburst aggregated from categorical path columns."""
    grouped, path, value_col = _hierarchy_frame(df, params)
    fig = px.sunburst(
        grouped, path=path, values="_size", color="_value",
        color_continuous_scale="Turbo", custom_data=path + ["_value"],
    )
    hover = "<br>".join(
        [f"{c}: %{{customdata[{i}]}}" for i, c in enumerate(path)]
        + [f"{value_col}: %{{customdata[{len(path)}]:.3g}}<extra></extra>"]
    )
    fig.update_traces(hovertemplate=hover)
    fig.update_layout(
        title=params.get("title", f"Sunburst — {value_col}"), template="plotly_white",
    )
    return _to_json(fig)


def _waffle(df, params):
    """100-cell waffle chart of category proportions."""
    work, col = _resolve_group(df, params, preferred=("AQI_Category", "location"),
                               default=None, required=True)
    counts = work[col].astype("string").fillna("Missing").value_counts()
    top_n = max(1, int(params.get("top_n", 8)))
    counts = counts.head(top_n)
    if counts.empty:
        raise ValueError(f"Column '{col}' has no values to chart.")
    proportions = counts / float(counts.sum())
    cells = np.floor(proportions.to_numpy() * 100).astype(int)
    remainder = 100 - int(cells.sum())
    if remainder > 0:
        order = np.argsort(-(proportions.to_numpy() * 100 - cells))
        for index in range(remainder):
            cells[order[index % len(order)]] += 1
    labels = []
    for category, count in zip(counts.index, cells):
        labels.extend([str(category)] * int(count))
    rng = np.random.default_rng(2026)
    rng.shuffle(labels)
    labels = np.asarray(labels)
    x_grid = np.array([i % 10 + 0.5 for i in range(100)], dtype=float)
    y_grid = np.array([9 - i // 10 + 0.5 for i in range(100)], dtype=float)
    fig = go.Figure()
    for index, category in enumerate(counts.index):
        mask = labels == str(category)
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        fig.add_trace(go.Scatter(
            x=x_grid[mask], y=y_grid[mask], mode="markers",
            name=f"{category} ({cells[index]}%)",
            marker=dict(symbol="square", size=30, color=color,
                        line=dict(color="white", width=1)),
            hovertemplate=f"{category}<extra></extra>",
        ))
    fig.update_layout(
        title=params.get("title", f"Waffle Chart — {col}"), template="plotly_white",
        xaxis=dict(range=[0, 10], showticklabels=False, showgrid=False, zeroline=False),
        yaxis=dict(range=[0, 10], showticklabels=False, showgrid=False, zeroline=False,
                   scaleanchor="x", scaleratio=1),
        height=520,
    )
    return _to_json(fig)


def _ordered_bar(df, params):
    """Horizontal bars ordered by the aggregated numeric value."""
    summary, cat_col, value_col = _category_summary(df, params)
    y_positions, labels = _ranked_y(summary)
    values = summary[value_col].to_numpy(dtype=float)
    fig = go.Figure(go.Bar(
        x=values, y=y_positions, orientation="h",
        marker=dict(color=[_CHART_COLORS[i % len(_CHART_COLORS)]
                           for i in range(len(values))]),
        text=np.round(values, 3), textposition="outside",
        customdata=np.arange(len(values)),
        hovertemplate=f"{cat_col}: %{{y}}<br>{value_col}: %{{x:.3g}}<extra></extra>",
    ))
    fig.update_layout(
        title=params.get("title", f"Ordered Bar — {value_col} by {cat_col}"),
        xaxis_title=str(value_col), yaxis=dict(tickmode="array", tickvals=y_positions,
                                               ticktext=labels, automargin=True),
        template="plotly_white", height=max(360, 32 * len(values)),
    )
    return _to_json(fig)


def _lollipop(df, params):
    """Lollipop chart (stem plus dot) sorted by aggregated value."""
    summary, cat_col, value_col = _category_summary(df, params)
    y_positions, labels = _ranked_y(summary)
    values = summary[value_col].to_numpy(dtype=float)
    fig = go.Figure()
    for y_pos, value in zip(y_positions, values):
        fig.add_trace(go.Scatter(
            x=[0, value], y=[y_pos, y_pos], mode="lines", showlegend=False,
            line=dict(color="#9aa4b2", width=2), hoverinfo="skip",
        ))
    fig.add_trace(go.Scatter(
        x=values, y=y_positions, mode="markers", name=str(value_col),
        marker=dict(size=12, color=_CHART_COLORS[0],
                    line=dict(color="white", width=1)),
        hovertemplate=f"{cat_col}: %{{y}}<br>{value_col}: %{{x:.3g}}<extra></extra>",
    ))
    fig.update_layout(
        title=params.get("title", f"Lollipop — {value_col} by {cat_col}"),
        xaxis_title=str(value_col), yaxis=dict(tickmode="array", tickvals=y_positions,
                                               ticktext=labels, automargin=True),
        template="plotly_white", height=max(360, 32 * len(values)), showlegend=False,
    )
    return _to_json(fig)


def _cleveland_dot(df, params):
    """Cleveland dot plot: minimal sorted dots with a reference grid."""
    summary, cat_col, value_col = _category_summary(df, params)
    y_positions, labels = _ranked_y(summary)
    values = summary[value_col].to_numpy(dtype=float)
    fig = go.Figure(go.Scatter(
        x=values, y=y_positions, mode="markers", name=str(value_col),
        marker=dict(size=10, color=_CHART_COLORS[1],
                    line=dict(color="white", width=1)),
        hovertemplate=f"{cat_col}: %{{y}}<br>{value_col}: %{{x:.3g}}<extra></extra>",
    ))
    fig.update_layout(
        title=params.get("title", f"Cleveland Dot — {value_col} by {cat_col}"),
        xaxis_title=str(value_col), yaxis=dict(tickmode="array", tickvals=y_positions,
                                               ticktext=labels, automargin=True),
        template="plotly_white", height=max(360, 32 * len(values)),
        xaxis=dict(gridcolor="#e5e7eb", zeroline=True, zerolinecolor="#9aa4b2"),
    )
    return _to_json(fig)


def _density_map(df, params):
    """2-D density heatmap on lon/lat coordinates (no WebGL or tiles)."""
    lat_col = params.get("lat_col") or ("lat" if "lat" in df.columns else None)
    lon_col = params.get("lon_col") or ("lon" if "lon" in df.columns else None)
    if not lat_col or not lon_col or lat_col not in df.columns or lon_col not in df.columns:
        raise ValueError("Available 'lat' and 'lon' columns are required for a density map.")
    value_col = params.get("column") or params.get("z_col")
    data = _sample_rows(df)
    kwargs = dict(
        x=lon_col, y=lat_col,
        nbinsx=int(np.clip(int(params.get("bins", 30)), 5, 120)),
        nbinsy=int(np.clip(int(params.get("bins", 30)), 5, 120)),
        color_continuous_scale="Turbo",
    )
    if value_col and value_col in data.columns:
        data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
        kwargs["z"] = value_col
        kwargs["histfunc"] = params.get("histfunc", "avg")
    fig = px.density_heatmap(data, **kwargs)
    fig.update_layout(
        title=params.get("title", "Spatial Density Heatmap"), template="plotly_white",
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1, title_text="Latitude")
    fig.update_xaxes(title_text="Longitude")
    return _to_json(fig)


def _cluster_map(df, params):
    """Spatial k-means clustering shown as a coloured lon/lat scatter."""
    lat_col = params.get("lat_col") or ("lat" if "lat" in df.columns else None)
    lon_col = params.get("lon_col") or ("lon" if "lon" in df.columns else None)
    if not lat_col or not lon_col or lat_col not in df.columns or lon_col not in df.columns:
        raise ValueError("Available 'lat' and 'lon' columns are required for a cluster map.")
    data = _sample_rows(df)
    lon = pd.to_numeric(data[lon_col], errors="coerce")
    lat = pd.to_numeric(data[lat_col], errors="coerce")
    valid = lon.notna() & lat.notna()
    lon, lat = lon[valid].to_numpy(dtype=float), lat[valid].to_numpy(dtype=float)
    if lon.size == 0:
        raise ValueError("No valid latitude/longitude coordinates were found.")
    coords = np.column_stack([lon, lat])
    scale = coords.std(axis=0)
    scale[scale <= 0] = 1.0
    normalized = coords / scale
    k = int(np.clip(int(params.get("clusters") or params.get("k") or 3),
                    1, min(12, len(normalized))))
    try:
        _, labels = kmeans2(normalized, k, minit="++", seed=2026)
    except Exception:
        try:
            _, labels = kmeans2(normalized, k, minit="random", seed=2026)
        except Exception:
            labels = np.zeros(len(normalized), dtype=int)
    frame = pd.DataFrame({lon_col: lon, lat_col: lat,
                          "cluster": [f"C{label + 1}" for label in labels]})
    size_col = params.get("size_col") or params.get("column")
    kwargs = dict(
        x=lon_col, y=lat_col, color="cluster", hover_name="cluster",
        color_discrete_sequence=_CHART_COLORS, render_mode="svg",
        title=params.get("title", f"Cluster Map — {k} spatial clusters"),
        template="plotly_white",
    )
    if size_col and size_col in data.columns:
        size_values = pd.to_numeric(data.loc[valid, size_col], errors="coerce").to_numpy(dtype=float)
        frame["_size"] = np.nan_to_num(size_values, nan=0.0, posinf=0.0, neginf=0.0).clip(min=0)
        kwargs["size"] = "_size"
    fig = px.scatter(frame, **kwargs)
    fig.update_yaxes(scaleanchor="x", scaleratio=1, title_text="Latitude")
    fig.update_xaxes(title_text="Longitude")
    return _to_json(fig)


def _heatmap(df, params):
    """Generic binned heatmap of a numeric value across two axes."""
    value_col = params.get("column") or params.get("value_col") or "pm2.5"
    if value_col not in df.columns:
        raise ValueError(f"Selected value column '{value_col}' was not found.")
    data = _sample_rows(df)
    x_col = params.get("x_col") or params.get("x")
    y_col = params.get("y_col") or params.get("y")
    if not x_col:
        x_col = "hour" if "hour" in data.columns else "_x"
        if x_col not in data.columns:
            data[x_col] = _temporal_series(data, "hour")
    elif x_col not in data.columns:
        raise ValueError(f"x_col '{x_col}' was not found.")
    if not y_col:
        y_col = "location" if "location" in data.columns else "_y"
        if y_col not in data.columns:
            data[y_col] = _temporal_series(data, "month")
    elif y_col not in data.columns:
        raise ValueError(f"y_col '{y_col}' was not found.")
    bins = int(np.clip(int(params.get("bins", 24)), 2, 120))
    for axis in (x_col, y_col):
        if data[axis].nunique(dropna=True) <= bins:
            continue
        numeric = pd.to_numeric(data[axis], errors="coerce")
        if numeric.notna().mean() >= 0.8:
            data[axis] = pd.qcut(numeric, q=min(bins, numeric.nunique()),
                                 duplicates="drop").astype(str)
        else:
            frequent = data[axis].astype(str).value_counts().head(bins).index
            data[axis] = data[axis].astype(str).where(
                data[axis].astype(str).isin(frequent), "Other")
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    aggfunc = params.get("aggfunc", "mean")
    try:
        pivot = data.pivot_table(index=y_col, columns=x_col, values=value_col,
                                 aggfunc=aggfunc)
    except Exception:
        pivot = data.pivot_table(index=y_col, columns=x_col, values=value_col,
                                 aggfunc="mean")
    if pivot.empty:
        raise ValueError("No values available for the heatmap.")
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[str(c) for c in pivot.columns],
        y=[str(r) for r in pivot.index],
        colorscale=params.get("colorscale", "Turbo"),
        colorbar=dict(title=str(value_col)), hoverongaps=False,
    ))
    fig.update_layout(
        title=params.get("title", f"Heatmap — {value_col} by {y_col} / {x_col}"),
        xaxis_title=str(x_col), yaxis_title=str(y_col), template="plotly_white",
    )
    return _to_json(fig)


def _temporal_geospatial(df, params):
    """Small-multiple station maps for successive time periods."""
    lat_col = params.get("lat_col") or ("lat" if "lat" in df.columns else None)
    lon_col = params.get("lon_col") or ("lon" if "lon" in df.columns else None)
    if not lat_col or not lon_col or lat_col not in df.columns or lon_col not in df.columns:
        raise ValueError("Available 'lat' and 'lon' columns are required.")
    value_col = params.get("column") or "pm2.5"
    if value_col not in df.columns:
        raise ValueError(f"Selected column '{value_col}' was not found.")
    period = params.get("period") or "hour"
    data = _sample_rows(df)
    data["_period"] = _temporal_series(data, period).astype(str)
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    data = data.dropna(subset=[lat_col, lon_col, value_col, "_period"])
    if data.empty:
        raise ValueError("No valid temporal/geospatial observations were found.")
    data["_lat"] = pd.to_numeric(data[lat_col], errors="coerce").round(3)
    data["_lon"] = pd.to_numeric(data[lon_col], errors="coerce").round(3)
    grouped = data.groupby(["_period", "_lat", "_lon"], dropna=False).agg(
        value=(value_col, "mean"), count=(value_col, "size")).reset_index()
    periods = sorted(grouped["_period"].unique(), key=str)
    max_panels = max(1, int(params.get("max_panels", 12)))
    if len(periods) > max_panels:
        selected = np.linspace(0, len(periods) - 1, max_panels, dtype=int)
        periods = [periods[i] for i in selected]
    rows = int(np.ceil(np.sqrt(len(periods))))
    cols = int(np.ceil(len(periods) / rows))
    fig = make_subplots(
        rows=rows, cols=cols, subplot_titles=periods,
        shared_xaxes=True, shared_yaxes=True,
    )
    for index, label in enumerate(periods):
        part = grouped[grouped["_period"] == label]
        row, col = divmod(index, cols)
        fig.add_trace(go.Scatter(
            x=part["_lon"], y=part["_lat"], mode="markers", name=str(label),
            marker=dict(
                size=np.clip(np.sqrt(part["count"].to_numpy()) * 4, 7, 24),
                color=part["value"], colorscale="Turbo", cmin=float(grouped["value"].min()),
                cmax=float(grouped["value"].max()), showscale=index == 0,
                colorbar=dict(title=str(value_col)) if index == 0 else None,
                opacity=0.9,
            ),
            customdata=np.column_stack([part["value"], part["count"]]),
            hovertemplate="lat %{y:.4f}<br>lon %{x:.4f}<br>" + str(value_col)
                          + ": %{customdata[0]:.3g}<br>n=%{customdata[1]}<extra></extra>",
        ), row=row + 1, col=col + 1)
    fig.update_layout(
        title=params.get("title", f"Temporal Geospatial — {value_col} by {period}"),
        template="plotly_white", height=max(420, 260 * rows), showlegend=False,
    )
    return _to_json(fig)


def _uncertainty_bands(df, params):
    """Mean trend with ±k·sd bands from groups or a rolling window."""
    col = params.get("column") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    dt_col = _datetime_column(df)
    if str(dt_col).lower() not in _KNOWN_DT:
        raise ValueError("Uncertainty bands require a recognised datetime column.")
    work, group_col = _resolve_group(df, params, preferred=("location",), default=None)
    base = pd.DataFrame({
        "time": pd.to_datetime(df[dt_col], errors="coerce"),
        "value": pd.to_numeric(df[col], errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan)
    if group_col:
        base["group"] = work[group_col].astype("string").fillna("Missing").astype(str)
    base = _sample_rows(base).dropna(subset=["time", "value"]).sort_values("time")
    if base.empty:
        raise ValueError(f"Column '{col}' has no usable dated observations.")
    window = params.get("window", 24)
    if not isinstance(window, str):
        window = max(1, int(window))
    k = float(params.get("k", 1.96))
    if group_col:
        stats = base.groupby("time")["value"].agg(["mean", "std", "count"]).reset_index()
    else:
        stats = base.set_index("time")["value"].rolling(
            window=window, min_periods=1).agg(["mean", "std", "count"]).reset_index()
    mean = stats["mean"].to_numpy(dtype=float)
    spread = stats["std"].fillna(0.0).to_numpy(dtype=float) * k
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=base["time"], y=base["value"], mode="markers", name="Observations",
        marker=dict(size=4, color="#9aa4b2", opacity=0.35),
    ))
    fig.add_trace(go.Scatter(
        x=stats["time"], y=mean + spread, mode="lines", showlegend=False,
        line=dict(width=0), hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=stats["time"], y=mean - spread, mode="lines", name=f"Mean ±{k:g}sd",
        fill="tonexty", fillcolor=_rgba("#5B8FF9", 0.2), line=dict(width=0),
    ))
    fig.add_trace(go.Scatter(
        x=stats["time"], y=mean, mode="lines", name="Central tendency",
        line=dict(color="#5B8FF9", width=2),
    ))
    fig.update_layout(
        title=params.get("title", f"Uncertainty Bands — {col}"),
        xaxis_title="Date/Time", yaxis_title=str(col), template="plotly_white",
    )
    return _to_json(fig)


def _confidence_intervals(df, params):
    """Bootstrap confidence intervals of the mean by category."""
    col = params.get("column") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    work, group_col = _resolve_group(df, params, preferred=("location",),
                                     default="location", required=True)
    level = float(params.get("ci", 95))
    alpha = 1.0 - level / 100.0
    resamples = int(np.clip(int(params.get("resamples", 400)), 50, 2000))
    rng = np.random.default_rng(2026)
    records = []
    for name, part in work.groupby(group_col, dropna=False):
        values = _clean_numeric(part[col])
        if values.size == 0:
            continue
        if values.size > 500:
            values = values[np.linspace(0, values.size - 1, 500, dtype=int)]
        means = rng.choice(values, size=(resamples, values.size), replace=True).mean(axis=1)
        records.append((str(name), float(values.mean()), values.size,
                        float(np.quantile(means, alpha / 2)),
                        float(np.quantile(means, 1 - alpha / 2))))
    if not records:
        raise ValueError(f"Column '{col}' has no usable numeric values.")
    summary = pd.DataFrame(records, columns=["group", "mean", "n", "lower", "upper"])
    summary = summary.sort_values("mean").reset_index(drop=True)
    fig = go.Figure(go.Scatter(
        x=summary["mean"], y=np.arange(len(summary)), mode="markers",
        marker=dict(size=10, color=_CHART_COLORS[0]),
        error_x=dict(
            type="data", symmetric=False,
            array=(summary["upper"] - summary["mean"]).to_numpy(),
            arrayminus=(summary["mean"] - summary["lower"]).to_numpy(),
            thickness=1.5, width=3,
        ),
        customdata=np.column_stack([summary["lower"], summary["upper"], summary["n"]]),
        hovertemplate="%{y}<br>mean %{x:.3g}<br>" + f"{level:g}% CI [%{{customdata[0]:.3g}}, %{{customdata[1]:.3g}}]"
                      + "<br>n=%{customdata[2]}<extra></extra>",
    ))
    fig.update_layout(
        title=params.get("title", f"{level:g}% Bootstrap Confidence Intervals — {col}"),
        xaxis_title=str(col),
        yaxis=dict(tickmode="array", tickvals=list(range(len(summary))),
                   ticktext=summary["group"].tolist(), automargin=True),
        template="plotly_white", height=max(360, 34 * len(summary)),
    )
    return _to_json(fig)


def _anomaly_detection(df, params):
    """Flag anomalies in a time series using rolling, z-score or IQR rules."""
    col = params.get("column") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    dt_col = _datetime_column(df)
    work = pd.DataFrame({
        "time": pd.to_datetime(df[dt_col], errors="coerce"),
        "value": pd.to_numeric(df[col], errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("time")
    if work.empty:
        raise ValueError(f"Column '{col}' has no usable values for anomaly detection.")
    method = str(params.get("method", "rolling")).lower()
    default_threshold = 1.5 if method == "iqr" else 3.0
    threshold = float(params.get("threshold", default_threshold))
    values = work["value"].to_numpy(dtype=float)
    if method == "iqr":
        q1, q3 = np.quantile(values, [0.25, 0.75])
        iqr = q3 - q1
        lower = np.full(values.size, q1 - threshold * iqr)
        upper = np.full(values.size, q3 + threshold * iqr)
        center = np.full(values.size, float(np.median(values)))
    elif method == "zscore":
        mu, sd = float(np.mean(values)), max(float(np.std(values)), 1e-12)
        lower = np.full(values.size, mu - threshold * sd)
        upper = np.full(values.size, mu + threshold * sd)
        center = np.full(values.size, mu)
    else:
        window = params.get("window", 24)
        if not isinstance(window, str):
            window = max(1, int(window))
        rolling = work["value"].rolling(window=window, min_periods=1)
        mean = rolling.mean().to_numpy(dtype=float)
        spread = rolling.std().fillna(0.0).to_numpy(dtype=float) * threshold
        lower, upper, center = mean - spread, mean + spread, mean
    flags = (values < lower) | (values > upper)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=work["time"], y=values, mode="lines", name=str(col),
        line=dict(color="#5B8FF9", width=1.4),
    ))
    fig.add_trace(go.Scatter(
        x=work["time"][flags], y=values[flags], mode="markers", name="Anomaly",
        marker=dict(size=8, color="#ef4444", line=dict(color="white", width=1)),
    ))
    fig.add_trace(go.Scatter(
        x=work["time"], y=upper, mode="lines", name=f"Upper ({method})",
        line=dict(color="#f59e0b", width=1, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=work["time"], y=lower, mode="lines", name=f"Lower ({method})",
        line=dict(color="#f59e0b", width=1, dash="dash"), fill="tonexty",
        fillcolor=_rgba("#f59e0b", 0.08),
    ))
    fig.update_layout(
        title=params.get("title", f"Anomaly Detection — {col} ({int(flags.sum())} flagged)"),
        xaxis_title="Date/Time", yaxis_title=str(col), template="plotly_white",
    )
    return _to_json(fig)


def _residual_diagnostics(df, params):
    """Four-panel residual diagnostics around a centred moving-average fit."""
    col = params.get("column") or "pm2.5"
    if col not in df.columns:
        raise ValueError(f"Selected column '{col}' was not found.")
    dt_col = _datetime_column(df)
    work = pd.DataFrame({
        "time": pd.to_datetime(df[dt_col], errors="coerce"),
        "value": pd.to_numeric(df[col], errors="coerce"),
    }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("time")
    if len(work) < 8:
        raise ValueError("Residual diagnostics need at least eight valid observations.")
    window = params.get("window", 24)
    if not isinstance(window, str):
        window = max(2, int(window))
    fitted = work["value"].rolling(window=window, center=True, min_periods=1).mean()
    residual = (work["value"] - fitted).to_numpy(dtype=float)
    fitted_values = fitted.to_numpy(dtype=float)
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("Residual over time", "Residual distribution",
                        "Residual vs fitted", "Normal Q-Q"),
    )
    fig.add_trace(go.Scatter(
        x=work["time"], y=residual, mode="lines+markers", name="Residual",
        marker=dict(size=3), line=dict(color="#5B8FF9", width=1),
    ), row=1, col=1)
    fig.add_hline(y=0, line=dict(color="#9aa4b2", width=1), row=1, col=1)
    fig.add_trace(go.Histogram(x=residual, nbinsx=40, name="Residual count",
                               marker_color="#61DDAA"), row=1, col=2)
    fig.add_trace(go.Scatter(
        x=fitted_values, y=residual, mode="markers", name="Residual vs fitted",
        marker=dict(size=4, color="#F6BD16", opacity=0.65),
    ), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="#9aa4b2", width=1), row=2, col=1)
    try:
        (osm, osr), (slope, intercept, _) = probplot(residual, dist="norm")
        fig.add_trace(go.Scatter(
            x=osm, y=osr, mode="markers", name="Q-Q",
            marker=dict(size=4, color="#7262FD"),
        ), row=2, col=2)
        line_x = np.array([float(np.min(osm)), float(np.max(osm))])
        fig.add_trace(go.Scatter(
            x=line_x, y=intercept + slope * line_x, mode="lines", name="Normal",
            line=dict(color="#ef4444", dash="dash", width=1.5),
        ), row=2, col=2)
    except Exception:
        fig.add_annotation(x=0.5, y=0.5, xref="x4 domain", yref="y4 domain",
                           text="Q-Q unavailable", showarrow=False)
    fig.update_layout(
        title=params.get("title", f"Residual Diagnostics — {col}"),
        template="plotly_white", height=660, showlegend=False,
    )
    fig.update_xaxes(title_text="Date/Time", row=1, col=1)
    fig.update_yaxes(title_text="Residual", row=1, col=1)
    fig.update_xaxes(title_text="Residual", row=1, col=2)
    fig.update_xaxes(title_text="Fitted", row=2, col=1)
    fig.update_yaxes(title_text="Residual", row=2, col=1)
    fig.update_xaxes(title_text="Theoretical quantiles", row=2, col=2)
    fig.update_yaxes(title_text="Ordered residual", row=2, col=2)
    return _to_json(fig)


def _forecast_verification(df, params):
    """Observed vs forecast traces and summary error metrics."""
    obs_col = params.get("observed_col") or params.get("obs_col") or "pm2.5"
    if obs_col not in df.columns:
        raise ValueError(f"Observed column '{obs_col}' was not found.")
    pred_col = (params.get("predicted_col") or params.get("pred_col")
                or params.get("mod_col"))
    if pred_col and pred_col not in df.columns:
        raise ValueError(f"Predicted column '{pred_col}' was not found.")
    if not pred_col:
        pred_col = next((c for c in ("prediction", "predicted", "forecast", "yhat",
                                     "predicted_value", "modelled") if c in df.columns), None)
    data = _sample_rows(df)
    observed = pd.to_numeric(data[obs_col], errors="coerce")
    naive = pred_col is None
    if naive:
        predicted = observed.shift(1)
        pred_label = "lag-1 naive forecast"
        note = " (no forecast column found; naive lag-1 baseline used)"
    else:
        predicted = pd.to_numeric(data[pred_col], errors="coerce")
        pred_label = str(pred_col)
        note = ""
    valid = observed.notna() & predicted.notna() & np.isfinite(observed) & np.isfinite(predicted)
    obs = observed[valid].to_numpy(dtype=float)
    pred = predicted[valid].to_numpy(dtype=float)
    if obs.size == 0:
        raise ValueError("No paired observed/forecast values are available.")
    error = pred - obs
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(error ** 2)))
    bias = float(np.mean(error))
    ss_res = float(np.sum(error ** 2))
    ss_tot = float(np.sum((obs - obs.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    nonzero = obs != 0
    mape = float(np.mean(np.abs(error[nonzero] / obs[nonzero])) * 100) if nonzero.any() else float("nan")
    dt_col = _datetime_column(df)
    if str(dt_col).lower() in _KNOWN_DT and dt_col in data.columns:
        times = pd.to_datetime(data.loc[valid, dt_col], errors="coerce")
    else:
        times = pd.Series(np.arange(len(data))[valid.to_numpy()])
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32],
        vertical_spacing=0.09,
        subplot_titles=("Observed vs forecast", "Forecast error"),
    )
    fig.add_trace(go.Scatter(
        x=times, y=obs, mode="lines+markers", name="Observed",
        marker=dict(size=3), line=dict(color="#5B8FF9", width=1.5),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=times, y=pred, mode="lines", name=pred_label,
        line=dict(color="#F6BD16", width=1.5, dash="dash"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=times, y=error, mode="markers", name="Error",
        marker=dict(size=4, color="#ef4444", opacity=0.7),
    ), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="#9aa4b2", width=1), row=2, col=1)
    r2_text = "n/a" if not np.isfinite(r2) else f"{r2:.3f}"
    mape_text = "n/a" if not np.isfinite(mape) else f"{mape:.1f}%"
    metrics = (f"n={obs.size:,} · MAE={mae:.3g} · RMSE={rmse:.3g} · "
               f"bias={bias:+.3g} · R²={r2_text} · MAPE={mape_text}")
    fig.add_annotation(
        x=0, y=1.10, xref="paper", yref="paper", showarrow=False,
        text=metrics, align="left", font=dict(size=11, color="#374151"),
    )
    fig.update_layout(
        title=params.get("title", f"Forecast Verification{note}"),
        template="plotly_white", height=620,
    )
    fig.update_yaxes(title_text=str(obs_col), row=1, col=1)
    fig.update_yaxes(title_text="Error", row=2, col=1)
    return _to_json(fig)


def _sensor_qc(df, params):
    """Per-column completeness/outlier rates plus a daily completeness map."""
    numeric = [c for c in df.columns
               if pd.api.types.is_numeric_dtype(df[c]) and c not in ("lat", "lon")]
    defaults = [c for c in _NUMERIC_DEFAULTS if c in numeric] or numeric
    cols = _selected_columns(df, params, tuple(defaults), limit=16)
    data = _sample_rows(df)
    records = []
    for col in cols:
        series = pd.to_numeric(data[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid = series.notna().sum()
        completeness = 100.0 * valid / max(len(series), 1)
        clean = series.dropna()
        if clean.empty:
            outlier = 0.0
            repeat = 0.0
        else:
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr = q3 - q1
            outlier = 100.0 * float(((clean < q1 - 1.5 * iqr) |
                                     (clean > q3 + 1.5 * iqr)).mean())
            repeat = 100.0 * float((clean.diff() == 0).mean())
        records.append((str(col), completeness, outlier, repeat))
    stats = pd.DataFrame(records, columns=["column", "complete", "outlier", "repeat"])
    dt_col = _datetime_column(df)
    has_dates = str(dt_col).lower() in _KNOWN_DT and dt_col in data.columns
    days = None
    if has_dates:
        dates = pd.to_datetime(data[dt_col], errors="coerce")
        if dates.notna().any():
            days = pd.DataFrame({"day": dates.dt.strftime("%Y-%m-%d")})
            for col in cols:
                days[col] = pd.to_numeric(data[col], errors="coerce").notna().astype(float) * 100
            days = days.groupby("day", as_index=False).mean()
            if len(days) > 90:
                days = days.iloc[np.linspace(0, len(days) - 1, 90, dtype=int)]
    if days is None or days.empty:
        fig = go.Figure()
        for column, color in (("complete", "#61DDAA"), ("outlier", "#ef4444"),
                              ("repeat", "#F6BD16")):
            fig.add_trace(go.Bar(x=stats["column"], y=stats[column], name=column,
                                 marker_color=color))
        fig.update_layout(barmode="group", yaxis=dict(range=[0, 100], title="%"),
                          title=params.get("title", "Sensor QC"), template="plotly_white")
        return _to_json(fig)
    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.42, 0.58], vertical_spacing=0.12,
        subplot_titles=("Completeness / outliers / repeated values",
                        "Daily completeness"),
    )
    for column, color in (("complete", "#61DDAA"), ("outlier", "#ef4444"),
                          ("repeat", "#F6BD16")):
        fig.add_trace(go.Bar(x=stats["column"], y=stats[column], name=column,
                             marker_color=color), row=1, col=1)
    matrix = days[cols].to_numpy(dtype=float).T
    fig.add_trace(go.Heatmap(
        z=matrix, x=days["day"].tolist(), y=[str(c) for c in cols],
        colorscale=[[0, "#ef4444"], [0.5, "#F6BD16"], [1, "#61DDAA"]],
        zmin=0, zmax=100, colorbar=dict(title="% complete"), hoverongaps=False,
    ), row=2, col=1)
    fig.update_layout(
        title=params.get("title", "Sensor QC Report"), template="plotly_white",
        barmode="group", height=720,
    )
    fig.update_yaxes(range=[0, 100], title_text="%", row=1, col=1)
    fig.update_yaxes(title_text="Column", row=2, col=1)
    return _to_json(fig)

# ── Dispatch ──────────────────────────────────────────────────────────────────
PLOT_MAP = {
    "timeseries":    _timeseries,
    "boxplot":       _boxplot,
    "violin":        _violin,
    "calendar":      _calendar,
    "diurnal":       _diurnal,
    "monthly":       _monthly,
    "windrose":      _windrose,
    "polar":         _polar,
    "scatter":       _scatter,
    "bubble":        _bubble,
    "correlation":   _correlation,
    "seasonal_box":  _seasonal_box,
    "histogram":     _histogram,
    "aqi_bar":       _aqi_bar,
    "spatial":       _spatial_heatmap,
    "time_of_week":  _time_of_week,
    "area":          _area,
    "pm_size_dist":  _pm_size_dist,
    "radar":         _radar,
    "co2_met":       _co2_met,
    "nc_timeseries": _nc_timeseries,
    "exceedance":    _exceedance,
    "trend":         _trend,
    "timevariation": _timevariation,
    "percentile":    _percentile,
    "pollutionrose": _pollutionrose,
    "annulus":       _annulus,
    "cpf":           _cpf,
    "bivariate_polar": _bivariate_polar,
    "ridgeline": _ridgeline,
    "density": _density,
    "ecdf": _ecdf,
    "beeswarm": _beeswarm,
    "pairplot": _pairplot,
    "correlogram": _correlogram,
    "hexbin": _hexbin,
    "connected_scatter": _connected_scatter,
    "streamgraph": _streamgraph,
    "rolling_trend": _rolling_trend,
    "seasonal_decomposition": _seasonal_decomposition,
    "treemap": _treemap,
    "sunburst": _sunburst,
    "waffle": _waffle,
    "ordered_bar": _ordered_bar,
    "lollipop": _lollipop,
    "cleveland_dot": _cleveland_dot,
    "density_map": _density_map,
    "cluster_map": _cluster_map,
    "heatmap": _heatmap,
    "temporal_geospatial": _temporal_geospatial,
    "uncertainty_bands": _uncertainty_bands,
    "confidence_intervals": _confidence_intervals,
    "anomaly_detection": _anomaly_detection,
    "residual_diagnostics": _residual_diagnostics,
    "forecast_verification": _forecast_verification,
    "sensor_qc": _sensor_qc,
}


def generate_plot(df: pd.DataFrame, plot_type: str, params: dict) -> str:
    if plot_type not in PLOT_MAP:
        raise ValueError(f"Unknown plot type '{plot_type}'. Available: {list(PLOT_MAP)}")
    return PLOT_MAP[plot_type](df, params)
