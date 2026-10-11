"""
Plotly figure builders.

These take a run's DataFrame and hand back a figure - they never generate or
persist anything. The one rule they all follow is that stage colour is the
primary encoding: if a mark is coloured, its colour means "which stage".
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ui.theme import (
    PARAM_BY_KEY,
    rgba,
    PATHOGEN_KILL_TEMP,
    STAGE_NAMES,
    stage_color,
    stage_envelope,
)

# A 47-day cycle at a 5-minute interval is ~13k readings. Past a few thousand
# marks the chart stops getting more informative and starts getting slower, so
# charts (never the saved file) thin out beyond this.
MAX_CHART_POINTS = 4000


def stage_segments(df):
    """Contiguous stretches of a single stage, in time order."""
    if df.empty:
        return []

    boundaries = df["stage_id"].ne(df["stage_id"].shift()).cumsum()
    segments = []
    for _, chunk in df.groupby(boundaries, sort=True):
        stage_id = int(chunk["stage_id"].iloc[0])
        segments.append({
            "stage_id": stage_id,
            "name": STAGE_NAMES.get(stage_id, chunk["stage_name"].iloc[0]),
            "start": chunk["timestamp"].iloc[0],
            "end": chunk["timestamp"].iloc[-1],
            "rows": int(len(chunk)),
            "days": (chunk["timestamp"].iloc[-1] - chunk["timestamp"].iloc[0]).total_seconds() / 86400,
        })
    return segments


def thin_for_chart(df, max_points=MAX_CHART_POINTS):
    """Even stride, but always keep the first and last row of every stage so
    the coloured blocks still start and stop in the right place."""
    if len(df) <= max_points:
        return df, False

    stride = len(df) // max_points + 1
    keep = pd.Series(False, index=df.index)
    keep.iloc[::stride] = True
    keep.iloc[-1] = True

    edges = df["stage_id"].ne(df["stage_id"].shift())
    keep = keep | edges | edges.shift(-1, fill_value=False)

    return df[keep], True


def _bar_width_ms(df):
    """Bar width in milliseconds, so the blocks butt up against each other
    instead of leaving a gap wherever a reading was dropped."""
    if len(df) < 2:
        return 30 * 60 * 1000
    deltas = df["timestamp"].diff().dropna()
    if deltas.empty:
        return 30 * 60 * 1000
    return float(deltas.median().total_seconds() * 1000)


def empty_figure(template, message="No data"):
    fig = go.Figure()
    fig.update_layout(
        template=template,
        height=280,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": message,
            "xref": "paper", "yref": "paper",
            "x": 0.5, "y": 0.5,
            "showarrow": False,
            "font": {"size": 13, "color": "#868E96"},
        }],
    )
    return fig


def temperature_bar_figure(df, template, height=380):
    """The hero chart: temperature over time as a solid band of bars, each one
    coloured by the stage it belongs to."""
    if df.empty:
        return empty_figure(template, "Generate a cycle to see temperature over time")

    plot_df, thinned = thin_for_chart(df)
    width = _bar_width_ms(plot_df)

    fig = go.Figure()
    seen = set()
    for segment in stage_segments(plot_df):
        chunk = plot_df[
            (plot_df["timestamp"] >= segment["start"]) & (plot_df["timestamp"] <= segment["end"])
        ]
        stage_id = segment["stage_id"]
        fig.add_trace(go.Bar(
            x=chunk["timestamp"],
            y=chunk["temperature_c"],
            width=width,
            name=segment["name"],
            legendgroup=str(stage_id),
            showlegend=stage_id not in seen,
            marker={"color": stage_color(stage_id), "line": {"width": 0}},
            hovertemplate="%{y:.1f} °C<br><i>" + segment["name"] + "</i><extra></extra>",
        ))
        seen.add(stage_id)

    fig.add_hline(
        y=PATHOGEN_KILL_TEMP,
        line={"dash": "dot", "width": 1.5, "color": "#FA5252"},
        annotation={"text": "55 °C pathogen-kill target", "font": {"size": 10}},
        annotation_position="top left",
    )

    fig.update_layout(
        template=template,
        height=height,
        barmode="overlay",
        bargap=0,
        yaxis={"title": "Temperature (°C)", "rangemode": "tozero"},
        xaxis={"title": None},
        legend={
            "orientation": "h",
            "yanchor": "bottom", "y": 1.02,
            "xanchor": "left", "x": 0,
            "title": None,
        },
        margin={"l": 56, "r": 24, "t": 48, "b": 40},
    )

    if thinned:
        fig.add_annotation(
            text="chart thinned for display · saved file keeps every reading",
            xref="paper", yref="paper", x=1, y=-0.16,
            xanchor="right", showarrow=False,
            font={"size": 10, "color": "#868E96"},
        )

    return fig


def _add_stage_bands(fig, segments, row=None, col=None, label=False):
    """Soft vertical blocks behind a line chart, one per stage."""
    for segment in segments:
        kwargs = {
            "x0": segment["start"], "x1": segment["end"],
            "fillcolor": stage_color(segment["stage_id"]),
            "opacity": 0.13,
            "line_width": 0,
            "layer": "below",
        }
        if row is not None:
            kwargs["row"] = row
            kwargs["col"] = col
        if label:
            kwargs["annotation_text"] = segment["name"]
            kwargs["annotation_position"] = "top left"
            kwargs["annotation_font_size"] = 10
        fig.add_vrect(**kwargs)


def _envelope_traces(segments, param_key):
    """The min/max band composting_stages.py says this sensor should sit in,
    stepped stage by stage. Returns (x, lower, upper) or None."""
    xs, lows, highs = [], [], []
    for segment in segments:
        envelope = stage_envelope(segment["stage_id"], param_key)
        if envelope is None:
            return None
        for point in (segment["start"], segment["end"]):
            xs.append(point)
            lows.append(envelope[0])
            highs.append(envelope[1])
    if not xs:
        return None
    return xs, lows, highs


def parameter_figure(df, param_key, template, height=300, show_envelope=True, title=None):
    """One sensor over time, drawn over its stage bands and expected envelope."""
    param = PARAM_BY_KEY[param_key]
    if df.empty:
        return empty_figure(template, "No data for %s" % param["label"])

    plot_df, _ = thin_for_chart(df)
    segments = stage_segments(plot_df)

    fig = go.Figure()

    envelope = _envelope_traces(segments, param_key) if show_envelope else None
    if envelope:
        xs, lows, highs = envelope
        fig.add_trace(go.Scatter(
            x=xs, y=highs, mode="lines", line={"width": 0},
            hoverinfo="skip", showlegend=False, name="expected max",
        ))
        fig.add_trace(go.Scatter(
            x=xs, y=lows, mode="lines", line={"width": 0},
            fill="tonexty", fillcolor="rgba(134,142,150,0.16)",
            hoverinfo="skip", name="Expected band",
        ))

    fig.add_trace(go.Scatter(
        x=plot_df["timestamp"],
        y=plot_df[param_key],
        mode="lines",
        name=param["label"],
        line={"color": param["color"], "width": 1.8},
        hovertemplate="%%{y:.%df} %s<extra></extra>" % (param["decimals"], param["unit"]),
    ))

    _add_stage_bands(fig, segments)

    fig.update_layout(
        template=template,
        height=height,
        showlegend=False,
        title={"text": title, "font": {"size": 13}} if title else None,
        yaxis={"title": param["axis"]},
        xaxis={"title": None},
        margin={"l": 56, "r": 24, "t": 36 if title else 20, "b": 36},
    )
    return fig


def all_parameters_figure(df, param_keys, template, row_height=150):
    """Every remaining sensor stacked on a shared time axis."""
    if df.empty:
        return empty_figure(template, "No data")

    plot_df, _ = thin_for_chart(df)
    segments = stage_segments(plot_df)

    fig = make_subplots(
        rows=len(param_keys), cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=[PARAM_BY_KEY[k]["label"] for k in param_keys],
    )

    for index, key in enumerate(param_keys, start=1):
        param = PARAM_BY_KEY[key]
        fig.add_trace(
            go.Scatter(
                x=plot_df["timestamp"],
                y=plot_df[key],
                mode="lines",
                name=param["label"],
                line={"color": param["color"], "width": 1.5},
                hovertemplate="%%{y:.%df} %s<extra></extra>" % (param["decimals"], param["unit"]),
            ),
            row=index, col=1,
        )
        _add_stage_bands(fig, segments, row=index, col=1)
        fig.update_yaxes(title_text=param["unit"] or "rel.", row=index, col=1)

    fig.update_layout(
        template=template,
        height=row_height * len(param_keys) + 60,
        showlegend=False,
        margin={"l": 56, "r": 24, "t": 40, "b": 36},
    )
    for annotation in fig.layout.annotations:
        annotation.font.size = 12
    return fig


def stage_timeline_figure(df, template, height=96):
    """A single proportional bar showing how long each stage ran for."""
    if df.empty:
        return empty_figure(template, "")

    segments = stage_segments(df)
    fig = go.Figure()
    for segment in segments:
        fig.add_trace(go.Bar(
            x=[max(segment["days"], 0.01)],
            y=["cycle"],
            orientation="h",
            name=segment["name"],
            marker={"color": stage_color(segment["stage_id"]), "line": {"width": 0}},
            text="%s<br>%.1f d" % (segment["name"], segment["days"]),
            textposition="inside",
            insidetextanchor="middle",
            textfont={"size": 10, "color": "#1A1B1E"},
            hovertemplate="%s<br>%.2f days · %d readings<extra></extra>" % (
                segment["name"], segment["days"], segment["rows"],
            ),
        ))

    fig.update_layout(
        template=template,
        height=height,
        barmode="stack",
        showlegend=False,
        bargap=0.35,
        xaxis={"title": "Days elapsed", "showgrid": False, "zeroline": False},
        yaxis={"visible": False},
        margin={"l": 8, "r": 24, "t": 8, "b": 34},
    )
    return fig


def sparkline_figure(df, param_key, template, height=44):
    """Thumbnail trend for a stat card - no axes, no hover chrome."""
    param = PARAM_BY_KEY[param_key]
    if df.empty:
        return empty_figure(template, "")

    plot_df, _ = thin_for_chart(df, max_points=400)

    fig = go.Figure(go.Scatter(
        x=plot_df["timestamp"],
        y=plot_df[param_key],
        mode="lines",
        line={"color": param["color"], "width": 1.6},
        fill="tozeroy",
        fillcolor=rgba(param["color"], 0.14),
        hoverinfo="skip",
    ))
    fig.update_layout(
        template=template,
        height=height,
        showlegend=False,
        hovermode=False,
        xaxis={"visible": False, "fixedrange": True},
        yaxis={"visible": False, "fixedrange": True,
               "range": [plot_df[param_key].min(), plot_df[param_key].max() * 1.05]},
        margin={"l": 0, "r": 0, "t": 4, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig
