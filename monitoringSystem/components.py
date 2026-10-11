from datetime import datetime, timedelta, timezone
from itertools import count

import dash_mantine_components as dmc
from dash import dcc
from figures import GRAPH_CONFIG, sparkline
from theme import SENSOR_COLORS, icon

# every dcc.Graph gets a {"type": "graph"} id so app.py can re-template all of
# them when the colour scheme changes (the pattern the DMC docs recommend)
_graph_ids = count()


# ------------------------------------------------------------ primitives ---

def brand():
    logo = dmc.Group(
        [
            dmc.ThemeIcon(icon("leaf", 20), size=36, radius="md", variant="light"),
            dmc.Stack(
                [
                    dmc.Text("CompostIQ", fw=700, size="md", lh=1.1),
                    # dropped on phones, where the header also holds the sign-in buttons
                    dmc.Text("Monitoring console", size="xs", c="dimmed", lh=1.1, visibleFrom="xs"),
                ],
                gap=2,
            ),
        ],
        gap="sm",
        wrap="nowrap",
    )
    # the logo holds no interactive children, so all of it can link home
    return dmc.Anchor(logo, href="/", underline="never", c="inherit", **{"aria-label": "CompostIQ home"})


def button(label, variant="filled", icon_name=None, component_id=None, **kwargs):
    props = {"variant": variant, "n_clicks": 0, **kwargs}
    if icon_name:
        props["leftSection"] = icon(icon_name, 16)
    if component_id is not None:
        props["id"] = component_id
    return dmc.Button(label, **props)


def linked_button(label, href, variant="filled", icon_name=None, slot="page", **kwargs):
    """A Button that navigates to `href`.

    dmc.Button has no href and must not be nested inside a link, so the
    target rides in the pattern-matching id and app.py's `navigate` callback
    moves the dcc.Location. `slot` keeps the id unique when the header and
    the page both link to the same place.
    """
    return button(label, variant, icon_name, component_id={"type": "nav-button", "href": href, "slot": slot}, **kwargs)


def page_header(eyebrow, title, description, action=None):
    return dmc.Group(
        [
            dmc.Stack(
                [
                    dmc.Text(eyebrow, size="xs", fw=700, tt="uppercase", c="dimmed"),
                    dmc.Title(title, order=1),
                    dmc.Text(description, c="dimmed"),
                ],
                gap=4,
            ),
            action,
        ],
        justify="space-between",
        align="flex-end",
        mb="lg",
    )


def section_header(title, action_label=None, action_href=None, action_id=None):
    action = None
    if action_href:
        action = dmc.Anchor(action_label, href=action_href, size="sm", fw=600)
    elif action_label:
        action = dmc.Button(action_label, id=action_id, variant="subtle", size="compact-sm", n_clicks=0)
    return dmc.Group([dmc.Title(title, order=3), action], justify="space-between", mb="sm")


def graph_id(name=None):
    return {"type": "graph", "index": name if name is not None else f"g{next(_graph_ids)}"}


def plot(figure, static=False, name=None):
    config = {**GRAPH_CONFIG, "staticPlot": static}
    height = figure.layout.height or 260
    return dcc.Graph(
        id=graph_id(name),
        figure=figure,
        config=config,
        responsive=True,
        style={"height": f"{height}px"},
    )


def online_badge():
    return dmc.Badge("Online", color="green", variant="dot", size="sm")


# ------------------------------------------------------------------ cards ---

def metric_card(metric):
    color = SENSOR_COLORS.get(metric["color"], metric["color"])
    return dmc.Card(
        [
            dmc.Group(
                [
                    dmc.Text(metric["label"], size="sm", c="dimmed", fw=500),
                    dmc.ThemeIcon(icon(metric["icon"], 16), variant="light", color=color, size="md"),
                ],
                justify="space-between",
            ),
            dmc.Group(
                [dmc.Text(metric["value"], fz=28, fw=700, lh=1.2), dmc.Text(metric["unit"], size="sm", c="dimmed")],
                gap=4,
                align="baseline",
                mt="xs",
            ),
            dmc.Text(metric["delta"], fw=600, c=color, size="xs"),
            # only cards with readings behind them get a line
            plot(sparkline(metric["values"], metric["color"]), static=True) if metric["values"] else None,
        ],
        padding="md",
    )


def _card_link(card, href):
    # a Card holds no interactive children here, so the whole card can be the link
    return dmc.Anchor(card, href=href, underline="never", c="inherit", className="ciq-card-link")


def bin_card(bin_data):
    device_label = "device" if bin_data["devices"] == 1 else "devices"
    card = dmc.Card(
        [
            dmc.CardSection(
                [
                    dmc.Group(
                        [
                            dmc.Stack(
                                [
                                    dmc.Text(bin_data["name"], fw=600),
                                    dmc.Text(f'{bin_data["location"]} · {bin_data["devices"]} {device_label}', size="xs", c="dimmed"),
                                ],
                                gap=0,
                            ),
                            dmc.ThemeIcon(icon("bin", 16), variant="light", size="md"),
                        ],
                        justify="space-between",
                        align="flex-start",
                    ),
                    plot(sparkline([48, 51, 50, 56, 54, 60, 63, 61, 68], "health"), static=True),
                ],
                p="md",
                withBorder=True,
            ),
            dmc.Group([dmc.Badge(bin_data["phase"], variant="light"), online_badge()], justify="space-between", mt="md"),
            dmc.Group(
                [dmc.Text("Health score", size="sm", c="dimmed"), dmc.Text(f'{bin_data["health"]}%', size="sm", fw=700)],
                justify="space-between",
                mt="sm",
            ),
            dmc.Progress(value=bin_data["health"], size="sm", mt=6),
        ],
        padding="md",
    )
    return _card_link(card, "/bin/live")

def user_bin_card(bin_data, devices):
    """A real bin from GET /bins, with the devices from GET /devices that are in it."""
    count = f"{len(devices)} device" if len(devices) == 1 else f"{len(devices)} devices"
    if not devices:
        status = dmc.Badge("No devices", color="gray", variant="light", size="sm")
    elif any(is_online(device) for device in devices):
        status = online_badge()
    else:
        status = dmc.Badge("Offline", color="gray", variant="dot", size="sm")
    card = dmc.Card(
        [
            dmc.CardSection(
                [
                    dmc.Group(
                        [
                            dmc.Stack(
                                [
                                    dmc.Text(bin_data["name"], fw=600),
                                    dmc.Text(" · ".join(part for part in (bin_data["location"], count) if part), size="xs", c="dimmed"),
                                ],
                                gap=0,
                            ),
                            dmc.ThemeIcon(icon("bin", 16), variant="light", size="md"),
                        ],
                        justify="space-between",
                        align="flex-start",
                    ),
                ],
                p="md",
                withBorder=True,
            ),
            dmc.Group(status, mt="md"),
        ],
        padding="md",
    )
    return _card_link(card, f"/bin/{bin_data['id']}/live")


def device_card(device):
    card = dmc.Card(
        [
            dmc.Group([dmc.ThemeIcon(icon("device", 18), variant="light", size="lg"), online_badge()], justify="space-between"),
            dmc.Text(device["name"], fw=600, size="lg", mt="md"),
            dmc.Text(f'Monitoring {device["location"]} · {device["model"]}', size="sm", c="dimmed"),
            dmc.Group(
                [
                    dmc.Text(device["reading"], fz=24, fw=700),
                    dmc.Group([dmc.Text("View telemetry", size="sm"), icon("arrow-right", 14)], gap=4, c="compost"),
                ],
                justify="space-between",
                mt="md",
            ),
        ],
        padding="md",
    )
    return _card_link(card, "/device/live")


# a device that checked in this recently counts as online
ONLINE_WITHIN = timedelta(minutes=10)


def time_ago(iso):
    """'12 s ago', '5 min ago', '3 h ago' or a date, from an ISO timestamp."""
    seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()))
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return datetime.fromisoformat(iso).strftime("%d %b %Y")


def is_online(device):
    seen = device.get("last_seen_at")
    return bool(seen) and datetime.now(timezone.utc) - datetime.fromisoformat(seen) < ONLINE_WITHIN


def _device_status(device):
    if not device["set_up"]:
        return dmc.Badge("Needs setup", color="yellow", variant="light", size="sm")
    if is_online(device):
        return online_badge()
    return dmc.Badge("Offline", color="gray", variant="dot", size="sm")


def paired_device_card(device):
    """A device from GET /devices: paired with a code, or registered with an API key."""
    registered = device["registration"] == "manual"
    where = device["bin"]["name"] if device["bin"] else "Not in a bin yet"
    seen = device.get("last_seen_at")
    # each kind is finished off by the wizard that made it
    setup_page = "/devices/register" if registered else "/devices/add"
    footer = (
        linked_button("Finish setup", f"{setup_page}?device={device['id']}", size="xs", slot=device["id"])
        if not device["set_up"]
        else dmc.Text(f"Last seen {time_ago(seen)}" if seen else "No readings yet", size="sm", c="dimmed")
    )
    if registered:
        # nothing was learnt from the hardware, so there is no model or hardware ID
        details = dmc.Text(where, size="sm", c="dimmed")
        identity = dmc.Badge("API key", leftSection=icon("key", 12), variant="light", color="gray", mt="xs")
    else:
        details = dmc.Text(f"{where} · {device['model'] or 'Unknown model'}", size="sm", c="dimmed")
        identity = dmc.Code(device["hardware_id"], mt="xs", w="fit-content")
    card = dmc.Card(
        [
            dmc.Group([dmc.ThemeIcon(icon("device", 18), variant="light", size="lg"), _device_status(device)], justify="space-between"),
            dmc.Text(device["name"] or "New device", fw=600, size="lg", mt="md"),
            details,
            identity,
            dmc.Group(footer, mt="md"),
        ],
        padding="md",
    )
    # a device still being set up has a "Finish setup" button instead, and a
    # card can't be a link with a button inside it
    return _card_link(card, f"/device/{device['id']}/live") if device["set_up"] else card


def alert_card(kind, title, detail, priority):
    """kind is an alert type from GET /alerts: too_hot, too_dry, too_wet or offline."""
    color = {"too_hot": "red", "offline": "gray"}.get(kind, "blue")
    icon_name = {"too_hot": "temperature", "offline": "device"}.get(kind, "droplet")
    return dmc.Card(
        dmc.Group(
            [
                dmc.ThemeIcon(icon(icon_name, 18), color=color, variant="light", size="lg"),
                dmc.Stack([dmc.Text(title, fw=600, size="sm"), dmc.Text(detail, size="sm", c="dimmed")], gap=2, flex=1),
                dmc.Badge(priority, color=color, variant="light"),
            ],
            wrap="nowrap",
            align="flex-start",
        ),
        padding="md",
    )


# ------------------------------------------------------------------ shell ---

NAV_GROUPS = [
    (
        "Workspace",
        [
            ("/dashboard", "home", "Overview"),
            ("/bins", "bin", "Compost bins"),
            ("/devices", "device", "Devices"),
        ],
    ),
    (
        "Quick actions",
        [
            ("/devices/add", "link", "Pair a device"),
            ("/devices/register", "key", "Register with API key"),
            ("/bins/new", "plus", "Create bin"),
        ],
    ),
]


def navbar():
    links = []
    for label, items in NAV_GROUPS:
        links.append(dmc.Text(label, size="xs", fw=700, tt="uppercase", c="dimmed", mt="md", mb=4, px="sm"))
        # active="exact" lets DMC highlight the current page without a callback
        links.extend(
            dmc.NavLink(label=text, href=href, leftSection=icon(icon_name, 18), active="exact", variant="light")
            for href, icon_name, text in items
        )
    return dmc.AppShellNavbar(
        [
            dmc.AppShellSection(links, grow=True),
            # filled by app.py's render_page with the signed-in user
            dmc.AppShellSection([dmc.Divider(mb="sm"), dmc.Box(id="navbar-account")]),
        ],
        p="md",
        id="navbar",
        display="none",
    )


def user_name(user):
    """What to call the user: their display name, or the start of their email."""
    return user.get("display_name") or user["email"].split("@")[0]


def navbar_account(user):
    """The navbar footer: who is signed in (links to /account) and Logout."""
    name = user_name(user)
    return dmc.Stack(
        [
            dmc.NavLink(
                label=dmc.Text(name, size="sm", fw=600, truncate="end"),
                description=dmc.Text(user["email"], size="xs", c="dimmed", truncate="end"),
                href="/account",
                leftSection=dmc.Avatar(name=name, color="compost", radius="xl"),
                active="exact",
                variant="light",
                **{"aria-label": "Account settings"},
            ),
            button("Log out", "default", icon_name="logout", component_id="logout-button", size="xs", fullWidth=True),
        ],
        gap="xs",
    )


def header_actions(public, signed_in):
    """The header's right-hand side, which depends on the shell and the user."""
    if not public:
        return [
            dmc.Badge("All systems online", color="green", variant="dot", visibleFrom="sm"),
            dmc.ActionIcon(icon("bell", 18), id="notification-button", variant="default", size="lg", n_clicks=0, **{"aria-label": "Notifications"}),
        ]
    if signed_in:
        return [linked_button("Go to dashboard", "/dashboard", slot="header")]
    return [
        linked_button("Sign in", "/login", "default", slot="header"),
        # phones only have room for one button; the sign-in page links to sign-up
        linked_button("Create account", "/register", slot="header", visibleFrom="xs"),
    ]


def header():
    return dmc.AppShellHeader(
        dmc.Group(
            [
                # the burger only shows in the private shell (app.py's toggle_navbar)
                dmc.Group([dmc.Burger(id="burger", size="sm", hiddenFrom="sm", opened=False, display="none"), brand()], gap="md", wrap="nowrap"),
                dmc.Group(
                    [
                        dmc.Group(id="header-actions", gap="sm", wrap="nowrap"),
                        dmc.ColorSchemeToggle(id="color-scheme-toggle", lightIcon=icon("sun", 18), darkIcon=icon("moon", 18), variant="default", size="lg"),
                    ],
                    gap="sm",
                    wrap="nowrap",
                ),
            ],
            justify="space-between",
            wrap="nowrap",
            h="100%",
            px="md",
        )
    )


# The public shell (/, /login, /register) is the same AppShell with the navbar
# collapsed away, so only the header is left. It is the starting state: a
# signed-out visitor never sees the private navigation, not even briefly.
NAVBAR = {"width": 260, "breakpoint": "sm", "collapsed": {"mobile": True, "desktop": True}}


def app_shell(page_root):
    """The one AppShell for the app; pages render into `page_root`."""
    return dmc.AppShell(
        [header(), navbar(), dmc.AppShellMain(dmc.Container(page_root, size="xl", px={"base": 0, "sm": "md"}))],
        header={"height": 64},
        navbar=NAVBAR,
        padding="md",
        id="appshell",
    )


def detail_tabs(kind, current):
    items = [("live", "Live telemetry"), ("history", "Historical stats"), ("settings", "Settings")]
    if kind == "bin":
        items = [("live", "Live telemetry"), ("maintenance", "Maintenance"), ("history", "Historical stats"), ("devices", "Devices"), ("settings", "Settings")]
    return dmc.Tabs(
        dmc.TabsList([dmc.TabsTab(label, value=key) for key, label in items]),
        id={"type": "detail-tabs", "kind": kind},
        value=current,
        mb="md",
    )
