"""
Design tokens for the monitoring dashboard.

The palette, fonts and component defaults mirror simulator/ui/theme.py so the
device (simulator) and the cloud dashboard read as one product. The two apps
are deployed separately - the simulator runs on the user's own machine - so
the values are kept in step by hand rather than imported across folders.
"""
import dash_mantine_components as dmc
import plotly.io as pio
from dash_iconify import DashIconify

# ----------------------------------------------------------------- colours ---

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

# One accent per sensor - the same hues the simulator uses, so a moisture line
# is the same blue on the device and in the cloud.
SENSOR_COLORS = {
    "temperature": "#F76707",
    "moisture": "#228BE6",
    "oxygen": "#12B886",
    "co2": "#7048E8",
    "nh3": "#E64980",
    "health": "#6db223",
}

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
FONT_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"

THEME = {
    "primaryColor": "compost",
    "primaryShade": {"light": 7, "dark": 5},
    "defaultRadius": "md",
    "fontFamily": FONT_STACK,
    "fontFamilyMonospace": "ui-monospace, SFMono-Regular, Menlo, monospace",
    "headings": {"fontFamily": FONT_STACK, "fontWeight": "600"},
    "colors": {"compost": COMPOST_GREEN, "dark": EARTH_DARK},
    "components": {
        "Card": {"defaultProps": {"withBorder": True, "shadow": "none", "radius": "md"}},
        "Paper": {"defaultProps": {"radius": "md"}},
        "Button": {"defaultProps": {"radius": "md"}},
    },
}


# ------------------------------------------------------------------ charts ---

LIGHT_TEMPLATE = "mantine_light"
DARK_TEMPLATE = "mantine_dark"


def register_figure_templates():
    """DMC's Mantine-matched Plotly templates, tuned to sit inside a Card.

    Backgrounds are transparent so a chart takes on its card's colour in both
    schemes; app.py swaps light/dark with a Patch when the toggle changes.
    """
    dmc.add_figure_templates(default=LIGHT_TEMPLATE)
    for name in (LIGHT_TEMPLATE, DARK_TEMPLATE):
        layout = pio.templates[name].layout
        layout.paper_bgcolor = "rgba(0,0,0,0)"
        layout.plot_bgcolor = "rgba(0,0,0,0)"
        layout.margin = {"l": 44, "r": 16, "t": 12, "b": 34}
        layout.hoverlabel = {"font": {"size": 12}}
        layout.xaxis.showgrid = False


def figure_template(color_scheme):
    return pio.templates[DARK_TEMPLATE if color_scheme == "dark" else LIGHT_TEMPLATE]


def rgba(hex_color, alpha):
    """Plotly only takes 6-digit hex, so translucent fills go via rgba()."""
    hex_color = hex_color.lstrip("#")
    red, green, blue = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red},{green},{blue},{alpha})"


# ------------------------------------------------------------------- icons ---

# Short names used across the app -> Tabler icons via DashIconify, the icon
# approach the DMC docs recommend. Tabler matches the simulator's glyphs.
_ICONS = {
    "leaf": "tabler:leaf",
    "home": "tabler:home",
    "bin": "tabler:box",
    "device": "tabler:cpu",
    "plus": "tabler:plus",
    "link": "tabler:link",
    "key": "tabler:key",
    "bell": "tabler:bell",
    "alert": "tabler:alert-triangle",
    "arrow-right": "tabler:arrow-right",
    "temperature": "tabler:temperature",
    "droplet": "tabler:droplet",
    "wind": "tabler:wind",
    "heart": "tabler:heart-rate-monitor",
    "download": "tabler:download",
    "check": "tabler:check",
    "sun": "tabler:sun",
    "moon": "tabler:moon-stars",
    "mail": "tabler:mail",
    "logout": "tabler:logout",
    "alert-circle": "tabler:alert-circle",
    "trash": "tabler:trash",
    "plug": "tabler:plug",
    "chart": "tabler:chart-line",
    "map": "tabler:map-2",
}


def icon(name, size=18, **kwargs):
    """A Tabler icon that takes its colour from the surrounding text."""
    return DashIconify(icon=_ICONS[name], width=size, height=size, **kwargs)
