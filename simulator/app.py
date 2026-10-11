"""
CompostIQ simulator UI.

Left panel configures a run, the button generates it, and every generated
cycle is written into simulator/Data/ and gets its own tab across the top of
the right panel. The generator itself is untouched by this file - we only
call generate_bin_data() and hand the result to run_store.

Run it with:  python simulator/app.py
"""
import os
import random
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.abspath(os.path.dirname(os.path.abspath(__file__))), ".env"))

url = os.getenv("API_URL")
key = os.getenv("SIMULATOR_API_KEY")


# generator.py and composting_stages.py import each other by bare name, so the
# simulator folder has to be importable regardless of where this was launched
THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
if THIS_FOLDER not in sys.path:
    sys.path.insert(0, THIS_FOLDER)

import dash_mantine_components as dmc
import requests
from dash import ALL, Dash, Input, Output, State, callback, ctx, dcc, html, no_update

import generator
import run_store
from ui import charts, layout
from ui.theme import THEME, figure_template, icon, register_figure_templates

# generator column -> API field. Only mapped columns are sent, so stage_id and
# stage_name (the ML ground-truth labels) never leave the saved JSON: a real
# sensor cannot measure them
FIELD_MAP = {
    "timestamp": "timestamp",
    "temperature_c": "temperature",
    "moisture_pct": "moisture_percent",
    "o2_pct": "o2_percent",
    "co2_pct": "co2_percent",
    "nh3_relative": "nh3_ratio",
}
BATCH_SIZE = 500
# the generator's timestamps are naive; without an offset Postgres would read
# them in the VM's timezone and shift every reading. Lombok is fixed UTC+8
SITE_TIMEZONE = ZoneInfo("Asia/Makassar")

# apply the stored light/dark choice before the first paint, so there is no
# flash of the wrong theme on reload
dmc.pre_render_color_scheme()
register_figure_templates()

app = Dash(__name__, title="CompostIQ Simulator", suppress_callback_exceptions=True)
server = app.server


app.layout = dmc.MantineProvider(
    # the "dmc" class maps Dash 4 core-component colours onto the Mantine
    # theme (see assets/simulator.css); MantineProvider itself takes no class
    html.Div(
        [
            dmc.AppShell(
                [
                    layout.header(),
                    layout.control_panel(date.today().isoformat()),
                    layout.main_panel(),
                ],
                header={"height": 64},
                navbar={"width": 340, "breakpoint": "sm", "collapsed": {"mobile": True}},
                padding="md",
                id="appshell",
            ),
            dcc.Store(id="run-index", data=[]),
        ],
        className="dmc",
    ),
    theme=THEME,
)


# ------------------------------------------------------------- helpers -----

def parse_optional_number(value):
    """NumberInput hands back '' when it is empty, not None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_start_time(value):
    """DatePickerInput gives 'YYYY-MM-DD'. Fall back to today."""
    if not value:
        return datetime.combine(date.today(), datetime.min.time())
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return datetime.combine(date.today(), datetime.min.time())


def next_cycle_label(runs):
    """Lowest unused 'Cycle N', so deleting run 2 frees that number up."""
    used = set()
    for meta in runs:
        label = meta.get("label", "")
        if label.startswith("Cycle "):
            try:
                used.add(int(label.split(" ", 1)[1]))
            except (ValueError, IndexError):
                pass
    number = 1
    while number in used:
        number += 1
    return "Cycle %d" % number


def status_message(text, color):
    return dmc.Text(text, size="xs", c=color, ta="center")


def pick_active_tab(runs, current):
    run_ids = [meta["run_id"] for meta in runs]
    if current in run_ids:
        return current
    return run_ids[-1] if run_ids else None


def build_readings(run_id):
    """A saved run as the API's payload: mapped fields only, offset-aware times."""
    df = run_store.load_dataframe(run_id)
    if df.empty:
        return []

    missing = [column for column in FIELD_MAP if column not in df.columns]
    if missing:
        raise KeyError("this run is missing columns: %s" % ", ".join(missing))

    readings = df[list(FIELD_MAP)].rename(columns=FIELD_MAP)
    readings["timestamp"] = df["timestamp"].dt.tz_localize(SITE_TIMEZONE).map(lambda t: t.isoformat())
    return readings.to_dict(orient="records")


def batch_readings(readings):
    """Split readings into API-sized chunks. Nothing is sent from here."""
    return [readings[start:start + BATCH_SIZE] for start in range(0, len(readings), BATCH_SIZE)]


def upload_cycle(run_id):
    """Send a saved cycle to the API, one batch per request.

    Returns (number of readings sent, error message or None). Stops at the
    first failed batch; batches sent before it are already stored.
    """
    readings = build_readings(run_id)
    sent = 0

    for batch in batch_readings(readings):
        try:
            response = requests.post(
                url + "/records",
                json=batch,
                headers={"x-key": key},
                timeout=30,
            )
        except requests.exceptions.RequestException:
            return sent, "Could not reach the API at %s. Is the SSH tunnel open?" % url

        if not response.ok:
            return sent, "The API returned %d: %s" % (response.status_code, response.text[:200])

        sent += len(batch)

    return sent, None


def upload_alert(title, text, color):
    return dmc.Alert(text, title=title, color=color, variant="light",
                     withCloseButton=True, icon=icon("cloud-upload", 18))


# ----------------------------------------------------------- callbacks -----

@callback(
    Output("cfg-seed", "value"),
    Input("cfg-seed-random", "n_clicks"),
    prevent_initial_call=True,
)
def randomise_seed(n_clicks):
    return random.randint(0, 999999)


@callback(
    Output("cfg-start-stage", "description"),
    Input("cfg-start-stage", "value"),
)
def describe_start_stage(value):
    from ui.theme import STAGE_BLURBS
    try:
        return STAGE_BLURBS[int(value)]
    except (TypeError, ValueError, KeyError):
        return ""


@callback(
    Output("run-index", "data"),
    Output("run-tabs-list", "children"),
    Output("run-tabs", "value"),
    Output("generate-status", "children"),
    Input("generate-button", "n_clicks"),
    Input({"type": "delete-run", "index": ALL}, "n_clicks"),
    State("cfg-start-stage", "value"),
    State("cfg-start-date", "value"),
    State("cfg-duration", "value"),
    State("cfg-seed", "value"),
    State("cfg-interval", "value"),
    State({"type": "cfg-noise", "field": ALL}, "value"),
    State({"type": "cfg-noise", "field": ALL}, "id"),
    State("run-tabs", "value"),
)
def manage_runs(generate_clicks, delete_clicks, start_stage, start_date, duration,
                seed, interval, noise_values, noise_ids, active_run):
    """Single owner of the run list: generates, deletes, and rebuilds the tabs.

    Keeping all three in one callback avoids two callbacks racing to decide
    which tab should be selected afterwards.
    """
    trigger = ctx.triggered_id
    message = None

    # n_clicks is 0 on a freshly rendered button, so a re-render never counts
    # as a press - only a real click gets past these guards
    if trigger == "generate-button" and generate_clicks:
        runs = run_store.list_runs()
        try:
            meta = generate_run(runs, start_stage, start_date, duration, seed,
                                interval, noise_values, noise_ids)
        except Exception as error:  # surfaced in the panel rather than the console
            runs = run_store.list_runs()
            return (runs, [layout.run_tab(m) for m in runs],
                    pick_active_tab(runs, active_run),
                    status_message("Generation failed: %s" % error, "red"))

        if meta is None:
            return (runs, [layout.run_tab(m) for m in runs],
                    pick_active_tab(runs, active_run),
                    status_message("Those settings produced no readings.", "orange"))

        active_run = meta["run_id"]
        message = status_message(
            "Saved {:,} readings to Data/{}.json".format(meta["summary"]["rows"], meta["run_id"]),
            "teal",
        )

    elif isinstance(trigger, dict) and trigger.get("type") == "delete-run":
        # a freshly rendered button reports n_clicks=0; only a real press deletes
        if ctx.triggered and ctx.triggered[0]["value"]:
            run_store.delete_run(trigger["index"])
            message = status_message("Deleted that cycle.", "dimmed")
            if active_run == trigger["index"]:
                active_run = None

    runs = run_store.list_runs()
    return runs, [layout.run_tab(meta) for meta in runs], pick_active_tab(runs, active_run), message


def generate_run(runs, start_stage, start_date, duration, seed, interval, noise_values, noise_ids):
    """Turn the left-panel settings into a saved run. Returns its meta dict."""
    try:
        start_stage_id = int(start_stage)
    except (TypeError, ValueError):
        start_stage_id = 0

    max_days = parse_optional_number(duration)
    interval_minutes = parse_optional_number(interval) or generator.SAMPLE_INTERVAL_MINUTES

    # a blank seed still gets one, so the run stays reproducible from its meta
    seed_value = parse_optional_number(seed)
    seed_value = random.randint(0, 999999) if seed_value is None else int(seed_value)

    noise = {}
    for field_id, value in zip(noise_ids, noise_values):
        parsed = parse_optional_number(value)
        if parsed is not None:
            noise[field_id["field"]] = parsed

    df = generator.generate_bin_data(
        start_time=parse_start_time(start_date),
        seed=seed_value,
        start_stage_id=start_stage_id,
        max_days=max_days,
        sample_interval_minutes=interval_minutes,
        noise=noise,
    )
    if df.empty:
        return None

    config = {
        "start_stage_id": start_stage_id,
        "start_date": str(start_date)[:10] if start_date else None,
        "duration_days": max_days,
        "seed": seed_value,
        "sample_interval_minutes": interval_minutes,
        "noise": noise,
    }
    return run_store.save_run(generator.dataframe_to_records(df), config, next_cycle_label(runs))


@callback(
    Output("panel", "style"),
    Output("empty-state", "style"),
    Output("cycle-summary", "children"),
    Output("stat-cards", "children"),
    Output("temp-graph", "figure"),
    Output("timeline-graph", "figure"),
    Output("param-graph", "figure"),
    Output("run-action-status", "children"),
    Input("run-tabs", "value"),
    Input("param-selector", "value"),
    Input("color-scheme-toggle", "computedColorScheme"),
    Input("run-index", "data"),
)
def render_panel(run_id, param_key, color_scheme, runs):
    hidden = ({"display": "none"}, {"display": "block"},
              no_update, no_update, no_update, no_update, no_update, None)

    if not run_id or not runs:
        return hidden

    meta = next((m for m in runs if m["run_id"] == run_id), None)
    if meta is None:
        return hidden

    template = figure_template(color_scheme)
    df = run_store.load_dataframe(run_id)
    if df.empty:
        # the meta sidecar is there but the readings are not - fall back to the
        # empty state rather than dividing by a cycle that has no rows
        return hidden

    if param_key == "all":
        detail = charts.all_parameters_figure(df, layout.SECONDARY_PARAMS, template)
    else:
        detail = charts.parameter_figure(df, param_key, template)

    return (
        {"display": "block"},
        {"display": "none"},
        layout.cycle_summary(meta, df),
        layout.stat_cards(df, template),
        charts.temperature_bar_figure(df, template),
        charts.stage_timeline_figure(df, template),
        detail,
        None,
    )


@callback(
    Output("download-run-file", "data"),
    Input({"type": "download-run", "index": ALL}, "n_clicks"),
    State("run-index", "data"),
    prevent_initial_call=True,
)
def download_run(_clicks, runs):
    """Send the selected cycle's readings to the browser as a .json file."""
    trigger = ctx.triggered_id
    if not isinstance(trigger, dict) or not (ctx.triggered and ctx.triggered[0]["value"]):
        return no_update

    run_id = trigger["index"]
    path = run_store.data_path(run_id)
    if not os.path.exists(path):
        return no_update

    meta = next((m for m in (runs or []) if m["run_id"] == run_id), None)
    label = (meta or {}).get("label", run_id).lower().replace(" ", "-")
    return dcc.send_file(path, filename="compostiq-%s-%s.json" % (label, run_id))


@callback(
    Output("run-action-status", "children", allow_duplicate=True),
    Input({"type": "upload-run", "index": ALL}, "n_clicks"),
    State("run-index", "data"),
    prevent_initial_call=True,
)
def cloud_upload(_clicks, _runs):
    """Upload the clicked cycle's readings to the ingest API."""
    trigger = ctx.triggered_id
    if not isinstance(trigger, dict) or not (ctx.triggered and ctx.triggered[0]["value"]):
        return no_update

    if not url or not key:
        return upload_alert("Upload not configured",
                            "Set API_URL and SIMULATOR_API_KEY in simulator/.env, "
                            "then restart the simulator.", "orange")

    sent, error = upload_cycle(trigger["index"])

    if error:
        message = "%s %d readings were sent before it stopped." % (error, sent)
        if sent > 0:
            message += " Uploading this cycle again will duplicate them."
        return upload_alert("Upload failed", message, "red")

    return upload_alert("Uploaded", "Sent %d readings to %s." % (sent, url), "teal")


if __name__ == "__main__":
    app.run(debug=True, port=8051)
