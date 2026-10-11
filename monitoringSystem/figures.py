import plotly.graph_objects as go
from data import HISTORICAL, TELEMETRY
from theme import SENSOR_COLORS, rgba

GRAPH_CONFIG = {
    "responsive": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
}

# 55 °C sustained is the EPA Class A pathogen-kill target, the same line the
# simulator draws on its temperature chart.
PATHOGEN_KILL_TEMP = 55


def sparkline(values, color="health", height=50):
    resolved = SENSOR_COLORS.get(color, color)
    figure = go.Figure(
        go.Scatter(
            y=values,
            mode="lines",
            line={"color": resolved, "width": 2, "shape": "spline"},
            fill="tozeroy",
            fillcolor=rgba(resolved, 0.10),
            hoverinfo="skip",
        )
    )
    figure.update_layout(
        height=height,
        margin={"l": 0, "r": 0, "t": 2, "b": 0},
        showlegend=False,
        xaxis={"visible": False, "fixedrange": True},
        yaxis={"visible": False, "fixedrange": True},
    )
    return figure


def telemetry_figure(hours=24):
    count = 6 if hours == 6 else 24
    x = TELEMETRY["time"][-count:]
    figure = go.Figure()
    traces = [
        ("Temperature", TELEMETRY["temperature"], SENSOR_COLORS["temperature"], "°C"),
        ("Moisture", TELEMETRY["moisture"], SENSOR_COLORS["moisture"], "%"),
        ("CO₂ (×15)", [value * 15 for value in TELEMETRY["co2"]], SENSOR_COLORS["co2"], "%"),
    ]
    for name, values, color, unit in traces:
        figure.add_trace(
            go.Scatter(
                x=x,
                y=values[-count:],
                name=name,
                mode="lines",
                line={"color": color, "width": 2, "shape": "spline"},
                hovertemplate=f"%{{y:.1f}}{unit}<extra>{name}</extra>",
            )
        )
    figure.update_layout(
        height=280,
        hovermode="x unified",
        legend={"orientation": "h", "x": 0, "y": 1.14},
    )
    return figure


def readings_figure(readings, device_names):
    """Temperature and moisture from GET /bins/{id}/records or /devices/{id}/records.

    A bin can hold several sensors: each gets its own lines, or they would zigzag
    between the two. device_names maps a device id to its name for the legend.
    """
    figure = go.Figure()
    device_ids = list(dict.fromkeys(r["device_id"] for r in readings))
    for number, device_id in enumerate(device_ids):
        rows = [r for r in readings if r["device_id"] == device_id]
        suffix = f" · {device_names.get(device_id) or 'Sensor'}" if len(device_ids) > 1 else ""
        for name, key, color, unit in (
            ("Temperature", "temperature", SENSOR_COLORS["temperature"], "°C"),
            ("Moisture", "moisture_percent", SENSOR_COLORS["moisture"], "%"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=[r["timestamp"] for r in rows],
                    y=[r[key] for r in rows],
                    name=name + suffix,
                    mode="lines",
                    # the second sensor's lines are dotted
                    line={"color": color, "width": 2, "dash": "dot" if number else "solid"},
                    hovertemplate=f"%{{y:.1f}}{unit}<extra>{name + suffix}</extra>",
                )
            )
    if not readings:
        figure.add_annotation(text="No readings in this period", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
    figure.update_layout(height=280, hovermode="x unified", legend={"orientation": "h", "x": 0, "y": 1.14})
    return figure


def phase_history_figure(history):
    """Daily average and peak temperature from GET /bins/{id}/history."""
    color = SENSOR_COLORS["temperature"]
    days = [day["day"] for day in history]
    figure = go.Figure(
        [
            go.Scatter(
                x=days,
                y=[day["avg_temp"] for day in history],
                mode="lines+markers",
                name="Daily average",
                line={"color": color, "width": 2},
                fill="tozeroy",
                fillcolor=rgba(color, 0.12),
                hovertemplate="%{x}<br>%{y:.1f}°C<extra>Average</extra>",
            ),
            go.Scatter(
                x=days,
                y=[day["max_temp"] for day in history],
                mode="lines",
                name="Daily peak",
                line={"color": color, "width": 1.5, "dash": "dot"},
                hovertemplate="%{x}<br>%{y:.1f}°C<extra>Peak</extra>",
            ),
        ]
    )
    if not history:
        figure.add_annotation(text="No readings in this period", showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5)
    figure.add_hline(
        y=PATHOGEN_KILL_TEMP,
        line={"color": "#fa5252", "dash": "dot", "width": 1},
        annotation_text="Pathogen-kill target",
        annotation_position="top left",
        annotation_font={"color": "#fa5252", "size": 10},
    )
    figure.update_layout(height=260, legend={"orientation": "h", "x": 0, "y": 1.14})
    return figure


def health_figure():
    color = SENSOR_COLORS["health"]
    figure = go.Figure(
        go.Scatter(
            x=HISTORICAL["dates"],
            y=HISTORICAL["health"],
            mode="lines",
            line={"color": color, "width": 2.5, "shape": "spline"},
            fill="tozeroy",
            fillcolor=rgba(color, 0.10),
            hovertemplate="%{x}<br>Health %{y:.0f}/100<extra></extra>",
        )
    )
    figure.update_layout(height=260, showlegend=False)
    figure.update_yaxes(range=[40, 100])
    return figure
