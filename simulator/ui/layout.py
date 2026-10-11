"""
Layout builders for the simulator shell.

The page is a Mantine AppShell: a fixed header, a left navbar holding every
generation setting, and a main area whose top tab bar cycles through the
cycles you have generated. Nothing here calls the generator - callbacks in
app.py do that and feed the results back in.
"""
import dash_mantine_components as dmc
from dash import dcc, html

from composting_stages import STAGES
from ui import charts, stats
from ui.theme import PARAMETERS, STAGE_BLURBS, icon, stage_color

# Sensors the secondary panel can show. Temperature is deliberately absent -
# it already has the hero chart above.
SECONDARY_PARAMS = [p["key"] for p in PARAMETERS if p["key"] != "temperature_c"]

NOISE_FIELDS = [
    {"key": "temperature_c", "label": "Temperature", "step": 0.1, "max": 10, "decimals": 2},
    {"key": "moisture_pct", "label": "Moisture", "step": 0.1, "max": 10, "decimals": 2},
    {"key": "o2_pct", "label": "Oxygen", "step": 0.05, "max": 5, "decimals": 2},
    {"key": "co2_pct", "label": "CO₂", "step": 0.05, "max": 5, "decimals": 2},
    {"key": "nh3_relative", "label": "Ammonia", "step": 0.005, "max": 0.5, "decimals": 3},
]

INTERVAL_OPTIONS = [
    {"value": "5", "label": "5 minutes"},
    {"value": "10", "label": "10 minutes"},
    {"value": "15", "label": "15 minutes"},
    {"value": "30", "label": "30 minutes"},
    {"value": "60", "label": "1 hour"},
    {"value": "120", "label": "2 hours"},
]

# --- purely decorative. None of these reach generator.py. ---------------
FEEDSTOCK_OPTIONS = [
    "Mixed green waste",
    "Food scraps",
    "Manure + straw",
    "Garden trimmings",
    "Mixed municipal organics",
]

FEED_FREQUENCY_OPTIONS = ["One-off batch", "Daily", "Every 2 days", "Twice weekly", "Weekly"]


# ------------------------------------------------------------------ bits ---

def section_label(text, icon_name):
    return dmc.Group(
        [
            icon(icon_name, 15),
            dmc.Text(text, size="xs", fw=700, tt="uppercase", style={"letterSpacing": "0.06em"}),
        ],
        gap=8,
        c="dimmed",
    )


def stat_pill(label, value):
    return dmc.Stack(
        [
            dmc.Text(label, size="xs", c="dimmed", tt="uppercase", style={"letterSpacing": "0.05em"}),
            dmc.Text(value, size="sm", fw=600),
        ],
        gap=2,
    )


# ---------------------------------------------------------------- header ---

def header():
    return dmc.AppShellHeader(
        dmc.Group(
            [
                dmc.Group(
                    [
                        dmc.ThemeIcon(icon("leaf", 20), size=36, radius="md", variant="light"),
                        dmc.Stack(
                            [
                                dmc.Text("CompostIQ Simulator", fw=700, size="md", lh=1.1),
                                dmc.Text("Synthetic sensor cycles for the compost bin", size="xs", c="dimmed", lh=1.1),
                            ],
                            gap=2,
                        ),
                    ],
                    gap="sm",
                ),
                dmc.Group(
                    [
                        dmc.Badge("Mock data", variant="light", color="gray", size="sm"),
                        dmc.ColorSchemeToggle(
                            id="color-scheme-toggle",
                            lightIcon=icon("sun", 18),
                            darkIcon=icon("moon", 18),
                            variant="default",
                            size="lg",
                        ),
                    ],
                    gap="sm",
                ),
            ],
            justify="space-between",
            h="100%",
            px="lg",
        )
    )


# --------------------------------------------------------- control panel ---

def cycle_controls(today):
    return dmc.Stack(
        [
            section_label("Cycle", "layers"),
            dmc.Select(
                id="cfg-start-stage",
                label="Start stage",
                description=STAGE_BLURBS[0],
                data=[{"value": str(s["id"]), "label": s["name"]} for s in STAGES],
                value="0",
                allowDeselect=False,
                comboboxProps={"withinPortal": True},
            ),
            dmc.DatePickerInput(
                id="cfg-start-date",
                label="Start date",
                description="First timestamp in the generated series",
                value=today,
                valueFormat="D MMM YYYY",
                popoverProps={"withinPortal": True},
            ),
            dmc.NumberInput(
                id="cfg-duration",
                label="Duration",
                description="Leave blank to run the full cycle to maturation",
                placeholder="Full cycle",
                suffix=" days",
                min=0.5,
                max=120,
                step=1,
                decimalScale=1,
                value="",
            ),
            dmc.NumberInput(
                id="cfg-seed",
                label="Random seed",
                description="Same seed, same readings",
                value=42,
                min=0,
                max=999999,
                step=1,
                allowDecimal=False,
                rightSection=dmc.ActionIcon(
                    icon("dice", 16),
                    id="cfg-seed-random",
                    variant="subtle",
                    color="gray",
                    n_clicks=0,
                ),
                rightSectionProps={"style": {"pointerEvents": "all"}},
            ),
        ],
        gap="sm",
    )


def sampling_controls():
    return dmc.Stack(
        [
            section_label("Sampling & sensor noise", "sliders"),
            dmc.Select(
                id="cfg-interval",
                label="Sample interval",
                description="Gap between readings",
                data=INTERVAL_OPTIONS,
                value="30",
                allowDeselect=False,
                comboboxProps={"withinPortal": True},
            ),
            dmc.Accordion(
                [
                    dmc.AccordionItem(
                        [
                            dmc.AccordionControl("Per-sensor noise (σ)"),
                            dmc.AccordionPanel(
                                dmc.Stack(
                                    [
                                        dmc.NumberInput(
                                            id={"type": "cfg-noise", "field": field["key"]},
                                            label=field["label"],
                                            value=default,
                                            min=0,
                                            max=field["max"],
                                            step=field["step"],
                                            decimalScale=field["decimals"],
                                            size="xs",
                                        )
                                        for field, default in zip(NOISE_FIELDS, _noise_defaults())
                                    ],
                                    gap="xs",
                                    pt=4,
                                )
                            ),
                        ],
                        value="noise",
                    )
                ],
                variant="contained",
                radius="md",
                chevronPosition="right",
            ),
        ],
        gap="sm",
    )


def _noise_defaults():
    # imported lazily so ui/ stays importable without the generator on the path
    from generator import NOISE_DEFAULTS
    return [NOISE_DEFAULTS[field["key"]] for field in NOISE_FIELDS]


def operations_controls():
    """Feedstock and turning inputs. Cosmetic only - see the badge."""
    return dmc.Stack(
        [
            dmc.Group(
                [
                    section_label("Feedstock & operations", "leaf"),
                    dmc.Tooltip(
                        dmc.Badge("Display only", size="xs", variant="light", color="gray"),
                        label="Not wired to the generator yet — these do not affect the data",
                        withArrow=True,
                        w=240,
                        multiline=True,
                    ),
                ],
                justify="space-between",
                wrap="nowrap",
            ),
            dmc.Select(
                id="cfg-feedstock",
                label="Bulk material",
                data=FEEDSTOCK_OPTIONS,
                value=FEEDSTOCK_OPTIONS[0],
                allowDeselect=False,
                comboboxProps={"withinPortal": True},
            ),
            dmc.Select(
                id="cfg-feed-frequency",
                label="Material added",
                data=FEED_FREQUENCY_OPTIONS,
                value=FEED_FREQUENCY_OPTIONS[1],
                allowDeselect=False,
                comboboxProps={"withinPortal": True},
            ),
            dmc.NumberInput(
                id="cfg-mass",
                label="Material in bin",
                value=120,
                min=0,
                max=5000,
                step=10,
                suffix=" kg",
                thousandSeparator=True,
            ),
            dmc.NumberInput(
                id="cfg-turns",
                label="Turns over the period",
                value=4,
                min=0,
                max=60,
                step=1,
                allowDecimal=False,
            ),
            dmc.Stack(
                [
                    dmc.Switch(id="cfg-aeration", label="Forced aeration", checked=False, size="sm"),
                    dmc.Switch(id="cfg-leachate", label="Leachate capture", checked=True, size="sm"),
                ],
                gap="xs",
                pt=4,
            ),
        ],
        gap="sm",
    )


def control_panel(today):
    return dmc.AppShellNavbar(
        dmc.Stack(
            [
                dmc.ScrollArea(
                    dmc.Stack(
                        [
                            cycle_controls(today),
                            dmc.Divider(),
                            sampling_controls(),
                            dmc.Divider(),
                            operations_controls(),
                        ],
                        gap="lg",
                        p="md",
                    ),
                    type="hover",
                    offsetScrollbars=True,
                    style={"flex": 1, "minHeight": 0},
                ),
                dmc.Stack(
                    [
                        dmc.Button(
                            "Generate cycle",
                            id="generate-button",
                            leftSection=icon("bolt", 18),
                            fullWidth=True,
                            size="md",
                            n_clicks=0,
                        ),
                        html.Div(id="generate-status"),
                    ],
                    gap="xs",
                    p="md",
                    className="sim-navbar-footer",
                ),
            ],
            gap=0,
            h="100%",
            style={"minHeight": 0},
        ),
        id="navbar",
    )


# ------------------------------------------------------------ main panel ---

def run_tab(meta):
    """One tab in the top bar. The colour strip shows which stages it covers."""
    summary = meta.get("summary", {})
    stage_ids = [stage["stage_id"] for stage in summary.get("stages", [])]
    days = summary.get("days", 0) or 0

    strip = html.Span(
        [
            html.Span(
                className="sim-strip-block",
                style={"backgroundColor": stage_color(stage_id)},
            )
            for stage_id in stage_ids
        ],
        className="sim-strip",
    )

    return dmc.TabsTab(
        dmc.Group(
            [
                dmc.Text(meta.get("label", meta["run_id"]), size="sm", fw=600, lh=1.1),
                dmc.Text("%.1f d · %s" % (days, "{:,}".format(summary.get("rows", 0))),
                         size="xs", c="dimmed", lh=1.1),
            ],
            gap=2,
            align="flex-start",
            style={"flexDirection": "column"},
        ),
        value=meta["run_id"],
        leftSection=strip,
    )


def empty_state():
    return dmc.Paper(
        dmc.Stack(
            [
                dmc.ThemeIcon(icon("chart", 26), size=56, radius="xl", variant="light", color="gray"),
                dmc.Title("No cycles yet", order=3),
                dmc.Text(
                    "Set the pile up on the left, then press Generate cycle. "
                    "Each run is saved into simulator/Data/ and gets its own tab up here.",
                    c="dimmed",
                    ta="center",
                    maw=460,
                ),
            ],
            align="center",
            gap="sm",
            py=64,
        ),
        withBorder=True,
        className="sim-empty",
    )


def stage_legend():
    return dmc.Group(
        [
            dmc.Group(
                [
                    html.Span(
                        className="sim-swatch",
                        style={"backgroundColor": stage_color(stage["id"])},
                    ),
                    dmc.Text(stage["name"], size="xs", c="dimmed"),
                ],
                gap=6,
                wrap="nowrap",
            )
            for stage in STAGES
        ],
        gap="md",
        wrap="wrap",
    )


def parameter_stat_card(stat, figure):
    """One indicator tile: headline reading, trend, spread and a sparkline."""
    if stat["latest"] is None:
        return dmc.Card(dmc.Text(stat["label"], size="sm", c="dimmed"))

    delta = stat["delta"] or 0
    if abs(delta) < 10 ** -stat["decimals"]:
        trend_text, trend_color = "steady", "gray"
    elif delta > 0:
        trend_text, trend_color = "▲ %s" % stats.format_value(abs(delta), stat["decimals"]), "teal"
    else:
        trend_text, trend_color = "▼ %s" % stats.format_value(abs(delta), stat["decimals"]), "orange"

    band = stat["in_band"]
    band_badge = None
    if band is not None:
        band_badge = dmc.Tooltip(
            dmc.Badge(
                "%d%% in band" % round(band * 100),
                size="xs",
                variant="light",
                color="teal" if band >= 0.8 else ("yellow" if band >= 0.5 else "red"),
            ),
            label="Readings inside the range composting_stages.py expects for their own stage",
            withArrow=True,
            multiline=True,
            w=240,
        )

    return dmc.Card(
        dmc.Stack(
            [
                dmc.Group(
                    [
                        dmc.Group(
                            [
                                html.Span(
                                    className="sim-dot",
                                    style={"backgroundColor": stat["color"]},
                                ),
                                dmc.Text(stat["label"], size="xs", fw=600, c="dimmed"),
                            ],
                            gap=6,
                            wrap="nowrap",
                        ),
                        band_badge,
                    ],
                    justify="space-between",
                    wrap="nowrap",
                ),
                dmc.Group(
                    [
                        dmc.Text(
                            stats.format_value(stat["latest"], stat["decimals"], stat["unit"]),
                            fw=700,
                            size="xl",
                            lh=1.1,
                        ),
                        dmc.Text(trend_text, size="xs", c=trend_color, fw=600),
                    ],
                    gap="xs",
                    align="baseline",
                    wrap="nowrap",
                ),
                dcc.Graph(
                    figure=figure,
                    config={"displayModeBar": False, "staticPlot": True},
                    style={"height": 44},
                ),
                dmc.Group(
                    [
                        dmc.Text("min %s" % stats.format_value(stat["min"], stat["decimals"]), size="xs", c="dimmed"),
                        dmc.Text("avg %s" % stats.format_value(stat["mean"], stat["decimals"]), size="xs", c="dimmed"),
                        dmc.Text("max %s" % stats.format_value(stat["max"], stat["decimals"]), size="xs", c="dimmed"),
                    ],
                    justify="space-between",
                    gap=4,
                    wrap="nowrap",
                ),
            ],
            gap=8,
        ),
        p="sm",
        className="sim-stat-card",
    )


def stat_cards(df, template):
    return dmc.SimpleGrid(
        [
            parameter_stat_card(
                stats.parameter_stats(df, param["key"]),
                charts.sparkline_figure(df, param["key"], template),
            )
            for param in PARAMETERS
        ],
        cols={"base": 1, "sm": 2, "lg": 3, "xl": 5},
        spacing="sm",
    )


def cycle_summary(meta, df):
    """The strip directly under the tabs: what this cycle actually is."""
    cycle = stats.cycle_stats(df)
    config = meta.get("config", {})

    duration = config.get("duration_days")
    duration_text = "%.1f days" % cycle["days"] if cycle["days"] else "—"
    if duration is None:
        duration_text += " (full cycle)"

    kill = cycle["longest_hot_days"]
    kill_color = "teal" if kill >= stats.KILL_TARGET_DAYS else ("yellow" if kill > 0 else "gray")

    return dmc.Paper(
        dmc.Group(
            [
                dmc.Stack(
                    [
                        dmc.Group(
                            [
                                dmc.Title(meta.get("label", "Cycle"), order=4),
                                dmc.Badge(
                                    "seed %s" % config.get("seed", "—"),
                                    variant="light",
                                    color="gray",
                                    size="sm",
                                ),
                            ],
                            gap="xs",
                        ),
                        dmc.Text(
                            "%s → %s"
                            % (
                                cycle["start"].strftime("%d %b %Y, %H:%M") if cycle["start"] is not None else "—",
                                cycle["end"].strftime("%d %b %Y, %H:%M") if cycle["end"] is not None else "—",
                            ),
                            size="sm",
                            c="dimmed",
                        ),
                        dmc.Group(
                            [
                                dmc.Button(
                                    "Download JSON",
                                    # pattern-matching ids: these only exist once
                                    # a cycle is selected, so a plain string id
                                    # would make Dash complain on first load
                                    id={"type": "download-run", "index": meta["run_id"]},
                                    leftSection=icon("download", 16),
                                    variant="light",
                                    size="xs",
                                    n_clicks=0,
                                ),
                                dmc.Tooltip(
                                    dmc.Button(
                                        "Upload to API",
                                        id={"type": "upload-run", "index": meta["run_id"]},
                                        leftSection=icon("cloud-upload", 16),
                                        variant="default",
                                        size="xs",
                                        n_clicks=0,
                                    ),
                                    label="Sends this cycle's sensor readings to the API "
                                          "set in simulator/.env. Stage labels are not sent.",
                                    withArrow=True,
                                    multiline=True,
                                    w=260,
                                ),
                            ],
                            gap="xs",
                        ),
                    ],
                    gap=6,
                ),
                dmc.Group(
                    [
                        stat_pill("Readings", "{:,}".format(cycle["rows"])),
                        stat_pill("Duration", duration_text),
                        stat_pill("Interval", "%s min" % cycle["interval_minutes"]),
                        stat_pill("Peak temp", "%.1f °C" % cycle["peak_temp"]),
                        stat_pill("Stages", "%d of %d" % (len(cycle["stage_ids"]), len(STAGES))),
                    ],
                    gap="xl",
                    wrap="wrap",
                ),
                dmc.Group(
                    [
                        dmc.Tooltip(
                            dmc.RingProgress(
                                size=88,
                                thickness=9,
                                roundCaps=True,
                                sections=[{"value": cycle["kill_progress"], "color": kill_color}],
                                label=dmc.Stack(
                                    [
                                        dmc.Text("%.1fd" % kill, ta="center", fw=700, size="sm", lh=1),
                                        dmc.Text("≥55 °C", ta="center", size="10px", c="dimmed", lh=1),
                                    ],
                                    gap=2,
                                ),
                            ),
                            label="Longest unbroken stretch at or above 55 °C. "
                                  "EPA Class A pathogen reduction wants 3 consecutive days.",
                            withArrow=True,
                            multiline=True,
                            w=260,
                        ),
                        dmc.Tooltip(
                            dmc.ActionIcon(
                                icon("trash", 18),
                                # pattern-matching id: this button only exists
                                # once a cycle is selected, and a plain string
                                # id would make Dash complain on first load
                                id={"type": "delete-run", "index": meta["run_id"]},
                                variant="subtle",
                                color="red",
                                size="lg",
                                n_clicks=0,
                            ),
                            label="Delete this cycle from simulator/Data/",
                            withArrow=True,
                        ),
                    ],
                    gap="sm",
                    wrap="nowrap",
                ),
            ],
            justify="space-between",
            align="center",
            wrap="wrap",
            gap="lg",
        ),
        withBorder=True,
        p="md",
    )


def chart_card(title, subtitle, graph, extra=None):
    return dmc.Card(
        dmc.Stack(
            [
                dmc.Group(
                    [
                        dmc.Stack(
                            [
                                dmc.Text(title, fw=600, size="sm"),
                                dmc.Text(subtitle, size="xs", c="dimmed"),
                            ],
                            gap=2,
                        ),
                        extra,
                    ],
                    justify="space-between",
                    align="flex-start",
                    wrap="wrap",
                    gap="sm",
                ),
                graph,
            ],
            gap="sm",
        ),
        p="md",
    )


def main_panel():
    """Static skeleton. Callbacks fill in the children and figures."""
    graph_config = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}

    return dmc.AppShellMain(
        dmc.Stack(
            [
                dmc.Tabs(
                    dmc.TabsList(id="run-tabs-list", children=[]),
                    id="run-tabs",
                    value=None,
                    variant="default",
                    className="sim-run-tabs",
                ),
                html.Div(id="empty-state", children=empty_state()),
                html.Div(
                    id="panel",
                    style={"display": "none"},
                    children=dmc.Stack(
                        [
                            html.Div(id="cycle-summary"),
                            html.Div(id="run-action-status"),
                            dcc.Download(id="download-run-file"),
                            chart_card(
                                "Temperature over time",
                                "Every reading, coloured by the composting stage it falls in",
                                dcc.Graph(id="temp-graph", config=graph_config),
                                extra=stage_legend(),
                            ),
                            chart_card(
                                "Stage timeline",
                                "How long the pile spent in each stage",
                                dcc.Graph(
                                    id="timeline-graph",
                                    config={"displayModeBar": False},
                                    style={"height": 96},
                                ),
                            ),
                            html.Div(id="stat-cards"),
                            chart_card(
                                "Sensor detail",
                                "Line against the range composting_stages.py expects for each stage",
                                dcc.Graph(id="param-graph", config=graph_config),
                                extra=dmc.SegmentedControl(
                                    id="param-selector",
                                    value="moisture_pct",
                                    data=[
                                        {"value": key, "label": _param_label(key)}
                                        for key in SECONDARY_PARAMS
                                    ] + [{"value": "all", "label": "All"}],
                                    size="xs",
                                ),
                            ),
                        ],
                        gap="md",
                    ),
                ),
            ],
            gap="md",
        )
    )


def _param_label(key):
    for param in PARAMETERS:
        if param["key"] == key:
            return param["short"]
    return key
