"""
Design tokens for the simulator UI.

Everything visual that more than one module needs - stage colours, per-sensor
accents, the Mantine theme object, the Plotly templates and the inline icon
set - is defined once here so the left panel, the charts and the stat cards
can never drift apart.
"""
from urllib.parse import quote

import dash_mantine_components as dmc
import plotly.io as pio
from dash import html

from composting_stages import STAGES

# ---------------------------------------------------------------- colours ---

# A thermal ramp: cool green as the pile wakes up, through amber to a hot
# orange at peak, back down through blue as it cools, finishing on the deep
# green of finished compost. Chosen to stay legible on both backgrounds.
STAGE_COLORS = {
    0: "#94D82D",  # Early Mesophilic
    1: "#FCC419",  # Active Thermophilic
    2: "#F76707",  # Peak Decomposition
    3: "#4DABF7",  # Cooling
    4: "#37B24D",  # Maturation
}

STAGE_NAMES = {stage["id"]: stage["name"] for stage in STAGES}
STAGE_LOOKUP = {stage["id"]: stage for stage in STAGES}

STAGE_BLURBS = {
    0: "Pile wakes up, temperature climbing off ambient.",
    1: "Thermophiles take over, heat rising fast.",
    2: "Hottest, most active phase - pathogen kill window.",
    3: "Activity falls away, temperature drifts back down.",
    4: "Curing. Readings settle towards ambient.",
}

# The sanitation target the whole cycle is really aiming at: EPA Class A
# asks for 55C sustained. Drawn as a reference line on the temperature chart.
PATHOGEN_KILL_TEMP = 55.0


def stage_color(stage_id):
    return STAGE_COLORS.get(int(stage_id), "#868E96")


def rgba(hex_color, alpha):
    """Plotly only takes 6-digit hex, so translucent fills go via rgba()."""
    hex_color = hex_color.lstrip("#")
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)
    return "rgba(%d,%d,%d,%s)" % (red, green, blue, alpha)


# ------------------------------------------------------------- parameters ---

# One entry per sensor column. `chart` picks how the secondary panel draws it,
# `good` is the healthy operating band quoted in the stat card.
PARAMETERS = [
    {
        "key": "temperature_c",
        "label": "Temperature",
        "short": "Temp",
        "unit": "°C",
        "color": "#F76707",
        "decimals": 1,
        "icon": "temperature",
        "axis": "Temperature (°C)",
    },
    {
        "key": "moisture_pct",
        "label": "Moisture",
        "short": "Moisture",
        "unit": "%",
        "color": "#228BE6",
        "decimals": 1,
        "icon": "droplet",
        "axis": "Moisture (% by weight)",
    },
    {
        "key": "o2_pct",
        "label": "Oxygen",
        "short": "O₂",
        "unit": "%",
        "color": "#12B886",
        "decimals": 2,
        "icon": "wind",
        "axis": "O₂ (%)",
    },
    {
        "key": "co2_pct",
        "label": "Carbon dioxide",
        "short": "CO₂",
        "unit": "%",
        "color": "#7048E8",
        "decimals": 2,
        "icon": "cloud",
        "axis": "CO₂ (%)",
    },
    {
        "key": "nh3_relative",
        "label": "Ammonia",
        "short": "NH₃",
        "unit": "",
        "color": "#E64980",
        "decimals": 3,
        "icon": "flask",
        "axis": "NH₃ (relative, 0-1)",
    },
]

PARAM_BY_KEY = {p["key"]: p for p in PARAMETERS}

# maps a sensor column onto the envelope key in composting_stages.STAGES, so
# the stat cards can say "inside the band for this stage" without a second
# hardcoded copy of those numbers
STAGE_RANGE_KEYS = {
    "temperature_c": "temp_range",
    "moisture_pct": "moisture_range",
    "o2_pct": "o2_range",
    "co2_pct": "co2_range",
    "nh3_relative": "nh3_relative",
}


def stage_envelope(stage_id, param_key):
    """The (min, max) this sensor is expected to sit in during that stage."""
    stage = STAGE_LOOKUP.get(int(stage_id))
    range_key = STAGE_RANGE_KEYS.get(param_key)
    if stage is None or range_key is None:
        return None
    return stage[range_key]


# ----------------------------------------------------------------- theme ----

# Primary accent: a compost green, generated as a 10-shade Mantine scale.
COMPOST_GREEN = [
    "#f3faeb", "#e6f3d7", "#cbe8ab", "#afdc7c", "#98d256",
    "#89cc3d", "#81c930", "#6db223", "#5f9e1a", "#4e880c",
]

# A slightly warmer set of darks than Mantine's default, so the dark theme
# reads as soil rather than slate.
EARTH_DARK = [
    "#c9c9c6", "#b8b8b4", "#928e8a", "#6d6863", "#4d4842",
    "#3b3630", "#2f2a25", "#232019", "#1a1712", "#12100c",
]

THEME = {
    "primaryColor": "compost",
    "primaryShade": {"light": 7, "dark": 5},
    "defaultRadius": "md",
    "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    "fontFamilyMonospace": "ui-monospace, SFMono-Regular, Menlo, monospace",
    "headings": {
        "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        "fontWeight": "600",
    },
    "colors": {
        "compost": COMPOST_GREEN,
        "dark": EARTH_DARK,
    },
    "components": {
        "Card": {"defaultProps": {"withBorder": True, "shadow": "none", "radius": "md"}},
        "Paper": {"defaultProps": {"radius": "md"}},
        "Button": {"defaultProps": {"radius": "md"}},
    },
}


def register_figure_templates():
    """Mantine-matched Plotly templates, with margins tightened for cards."""
    dmc.add_figure_templates(default="mantine_light")
    for name in ("mantine_light", "mantine_dark"):
        template = pio.templates[name]
        template.layout.margin = dict(l=56, r=24, t=32, b=40)
        template.layout.hovermode = "x unified"
        template.layout.hoverlabel = dict(font_size=12)
        template.layout.xaxis.showgrid = False


def figure_template(color_scheme):
    return "mantine_dark" if color_scheme == "dark" else "mantine_light"


# ----------------------------------------------------------------- icons ----

# Tabler-style glyphs, inlined as CSS masks so the icon inherits `currentColor`
# and the app pulls in no icon package and makes no network request.
_ICON_PATHS = {
    "temperature": '<path d="M10 13.5a4 4 0 1 0 4 0v-8.5a2 2 0 0 0 -4 0z"/><path d="M10 9h4"/>',
    "droplet": '<path d="M7.5 19.4a6.5 6.5 0 0 0 9 0c2.6 -2.1 3.3 -5.7 1.6 -8.5l-4.9 -7.3a1.4 1.4 0 0 0 -2.3 0l-4.9 7.3c-1.7 2.8 -1 6.4 1.5 8.5z"/>',
    "wind": '<path d="M5 8h8.5a2.5 2.5 0 1 0 -2.34 -3.24"/><path d="M3 12h15.5a2.5 2.5 0 1 1 -2.34 3.24"/><path d="M4 16h5.5a2.5 2.5 0 1 1 -2.34 3.24"/>',
    "cloud": '<path d="M6.657 18c-2.572 0 -4.657 -2.007 -4.657 -4.483c0 -2.475 2.085 -4.482 4.657 -4.482c.393 -1.762 1.794 -3.2 3.675 -3.773c1.88 -.572 3.956 -.193 5.444 1c1.489 1.19 2.162 3.007 1.766 4.769h.99c1.913 0 3.464 1.56 3.464 3.486c0 1.927 -1.551 3.487 -3.465 3.487h-11.878"/>',
    "flask": '<path d="M9 3h6"/><path d="M10 3v6l-4.5 8a2 2 0 0 0 1.7 3h9.6a2 2 0 0 0 1.7 -3l-4.5 -8v-6"/><path d="M6.3 15h11.4"/>',
    "leaf": '<path d="M5 21c.5 -4.5 2.5 -8 7 -10"/><path d="M9 18c6.2 0 10.5 -3.3 11 -12v-2h-4c-9 0 -12 4 -12 9c0 1 0 3 2 5z"/>',
    "sliders": '<path d="M4 10a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/><path d="M6 4v4"/><path d="M6 12v8"/><path d="M16 14a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/><path d="M18 4v8"/><path d="M18 18v2"/>',
    "bolt": '<path d="M13 3v7h6l-8 11v-7h-6z"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M20 18v3"/><path d="M16 15v6"/><path d="M12 12v9"/><path d="M8 16v5"/>',
    "layers": '<path d="M12 3l9 5l-9 5l-9 -5z"/><path d="M3 12l9 5l9 -5"/><path d="M3 17l9 5l9 -5"/>',
    "dice": '<path d="M4 4m0 3a3 3 0 0 1 3 -3h10a3 3 0 0 1 3 3v10a3 3 0 0 1 -3 3h-10a3 3 0 0 1 -3 -3z"/><path d="M8.5 8.5h.01"/><path d="M15.5 15.5h.01"/><path d="M12 12h.01"/>',
    "clock": '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 7v5l3 3"/>',
    "sun": '<path d="M14.828 14.828a4 4 0 1 0 -5.656 -5.656a4 4 0 0 0 5.656 5.656"/><path d="M6.343 17.657l-1.414 1.414"/><path d="M6.343 6.343l-1.414 -1.414"/><path d="M17.657 6.343l1.414 -1.414"/><path d="M17.657 17.657l1.414 1.414"/><path d="M4 12h-2"/><path d="M12 4v-2"/><path d="M20 12h2"/><path d="M12 20v2"/>',
    "moon": '<path d="M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12"/><path d="M9 7v-3h6v3"/>',
    "cloud-upload": '<path d="M12 18.004h-4.343c-2.572 0 -4.657 -2.007 -4.657 -4.483c0 -2.475 2.085 -4.482 4.657 -4.482c.393 -1.762 1.794 -3.2 3.675 -3.773c1.88 -.572 3.956 -.193 5.444 1c1.489 1.19 2.162 3.007 1.766 4.769h.29c1.661 0 3.019 1.014 3.19 2.313"/><path d="M19 22v-6"/><path d="M22 19l-3 -3l-3 3"/>',
    "download": '<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2"/><path d="M7 11l5 5l5 -5"/><path d="M12 4v12"/>',
    "info": '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M12 9h.01"/><path d="M11 12h1v4h1"/>',
}


def _icon_uri(name):
    body = _ICON_PATHS[name]
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        "stroke='black' stroke-width='1.8' stroke-linecap='round' "
        "stroke-linejoin='round'>" + body + "</svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)


def icon(name, size=18, **kwargs):
    """An inline icon that takes its colour from the surrounding text."""
    uri = _icon_uri(name)
    style = {
        "width": size,
        "height": size,
        "WebkitMaskImage": "url(\"%s\")" % uri,
        "maskImage": "url(\"%s\")" % uri,
    }
    style.update(kwargs.pop("style", {}))
    return html.Span(className="sim-icon", style=style, **kwargs)
