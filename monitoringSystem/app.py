"""
CompostIQ monitoring dashboard.

One MantineProvider and one AppShell (header + navbar) wrap the whole app;
`render_page` swaps the page into `page-root` whenever the URL changes, and
decides who may see it: the public pages (/login, /register) get the
header only, everything else needs a sign-in and gets the navbar too.

The landing page is not part of the dashboard: it is the static website in
website/public, on compostiq.win. The dashboard's own "/" sends signed-out
visitors there and signed-in users to /dashboard.

Run it with:  python monitoringSystem/app.py
"""
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

import api_client
import auth
import dash_mantine_components as dmc
import flask
from api_client import ApiError, ApiUnavailable, NotAuthenticated
from components import NAVBAR, app_shell, header_actions, navbar_account
from dash import (
    ALL,
    Dash,
    Input,
    Output,
    Patch,
    State,
    callback,
    clientside_callback,
    ctx,
    dcc,
    html,
    no_update,
)
from figures import readings_figure
from pages import (
    REGISTER_INTRO,
    account_page,
    add_device_page,
    alerts_grid,
    bin_detail_page,
    bins_page,
    dashboard_page,
    detail_page,
    device_detail_page,
    devices_page,
    login_page,
    new_bin_page,
    not_found_page,
    register_device_page,
    register_page,
)
from settings import DASHBOARD_SECRET_KEY, SESSION_HOURS, WEBSITE_URL
from theme import FONT_URL, THEME, figure_template, register_figure_templates

logger = logging.getLogger("compostiq.dashboard")

# `python app.py` is local development; under gunicorn (the VM) this module
# is imported as "app", so the production rules below apply
LOCAL = __name__ == "__main__"

# apply the stored light/dark choice before the first paint, so there is no
# flash of the wrong theme on reload
dmc.pre_render_color_scheme()
register_figure_templates()

app = Dash(
    __name__,
    title="CompostIQ — Monitoring Console",
    external_stylesheets=[FONT_URL],
    suppress_callback_exceptions=True,
)
server = app.server


def configure_session(flask_server):
    """The signed session cookie that holds the user's token."""
    secret_key = DASHBOARD_SECRET_KEY
    if not secret_key:
        if not LOCAL:
            raise RuntimeError(
                "DASHBOARD_SECRET_KEY is not set. Add it to the repo-root .env "
                "(see .env.example) - the dashboard cannot sign sessions without it."
            )
        secret_key = secrets.token_urlsafe(48)
        logger.warning(
            "DASHBOARD_SECRET_KEY is not set: using a random key, so everyone is signed out "
            "whenever the dashboard restarts. Set it in monitoringSystem/.env (see .env.example)."
        )
    flask_server.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_NAME="compostiq_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # HTTPS only on the VM; plain http://127.0.0.1 needs it off
        SESSION_COOKIE_SECURE=not LOCAL,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )


configure_session(server)


@server.before_request
def leave_for_the_website():
    """Opening the dashboard's "/" directly: there is no landing page here."""
    if flask.request.path == "/" and flask.request.method in ("GET", "HEAD"):
        return flask.redirect(home())
    return None

app.layout = dmc.MantineProvider(
    # the "dmc" class maps Dash 4 core-component colours onto the Mantine
    # theme (see assets/styles.css); MantineProvider itself takes no class
    html.Div(
        [
            # "callback-nav": when a callback moves it (navigate, the detail
            # tabs), it announces the change like a link click does, so the
            # navbar's NavLinks re-check which one is active. With False the
            # address changes silently and the old link stays highlighted.
            dcc.Location(id="url", refresh="callback-nav"),
            # where a callback wants the browser to go next
            dcc.Store(id="redirect"),
            # which shell is showing: {"public": bool}. Never holds the token.
            dcc.Store(id="shell", data={"public": True}),
            # the DMC docs: exactly one container, in the top-level layout
            dmc.NotificationContainer(id="notify", position="top-right"),
            app_shell(html.Div(id="page-root")),
        ],
        className="dmc",
    ),
    theme=THEME,
)

PUBLIC_ROUTES = {"/login", "/register"}
AFTER_SIGN_IN = "/dashboard"


def home():
    """Where "/" leads: the dashboard if signed in, else the public website."""
    return AFTER_SIGN_IN if auth.is_signed_in() else WEBSITE_URL

# private pages; the two in USER_PAGES are built from the signed-in user
PRIVATE_ROUTES = {
    "/bins": bins_page,
    "/bins/new": new_bin_page,
    "/devices": devices_page,
    "/devices/add": add_device_page,
    "/devices/register": register_device_page,
}
USER_PAGES = {
    "/dashboard": dashboard_page,
    "/account": account_page,
}

SESSION_EXPIRED = "Your session expired, please sign in again."
API_DOWN = "Can’t reach CompostIQ right now. Try again in a moment."
TOO_MANY_ATTEMPTS = "Too many attempts, try again in a few minutes."
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_NAME = 80


def toast(message, color="compost", title=None):
    note = {"action": "show", "id": f"toast-{abs(hash(message))}", "message": message, "color": color, "autoClose": 4000}
    if title:
        note["title"] = title
    return [note]


def goto(target):
    """A value for the `redirect` store: a local path, with or without a
    query, or a full URL (the public website), which loads as a new page."""
    if target.startswith(("http://", "https://")):
        return {"href": target, "at": time.time()}
    pathname, _, query = target.partition("?")
    # the timestamp makes every redirect a new value, even to the same place
    return {"pathname": pathname, "search": f"?{query}" if query else "", "at": time.time()}


def login_url(next_path):
    return f"/login?{urlencode({'next': next_path})}"


def next_page(search):
    """Where to go after signing in: ?next= if it is a path on this site."""
    target = parse_qs((search or "").lstrip("?")).get("next", [""])[0]
    # a lone leading slash only - "//host" and "/\\host" would leave the site
    if not target.startswith("/") or target[1:2] in ("/", "\\") or target.split("?")[0] in PUBLIC_ROUTES:
        return AFTER_SIGN_IN
    return target


def _detail_route(pathname):
    """(kind, id, tab) for a detail URL such as /bin/<id>/live, else None.

    The mock cards still link to /bin/live and /device/live, which have no id.
    """
    parts = [part for part in pathname.split("/") if part]
    if not parts or parts[0] not in {"bin", "device"}:
        return None
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return None


def is_private(pathname):
    return pathname in USER_PAGES or pathname in PRIVATE_ROUTES or _detail_route(pathname) is not None


def private_page(pathname, user):
    if pathname in USER_PAGES:
        return USER_PAGES[pathname](user)
    if pathname in PRIVATE_ROUTES:
        return PRIVATE_ROUTES[pathname]()
    kind, item_id, tab = _detail_route(pathname)
    if item_id is None:
        # an old mock link such as /bin/live
        return detail_page(kind, None, tab)
    if kind == "bin":
        return bin_detail_page(item_id, tab)
    return device_detail_page(item_id, tab)


# ----------------------------------------------------------------- routing ---

@callback(
    Output("page-root", "children"),
    Output("header-actions", "children"),
    Output("navbar-account", "children"),
    Output("shell", "data"),
    Output("redirect", "data"),
    Output("notify", "sendNotifications"),
    Input("url", "pathname"),
)
def render_page(pathname):
    pathname = pathname or "/"

    def redirect(target, note=no_update):
        # leave the current page alone; the redirect renders the next one
        return no_update, no_update, no_update, no_update, goto(target), note

    def public(page, signed_in):
        return page, header_actions(True, signed_in), None, {"public": True}, no_update, no_update

    if pathname == "/":
        # reached from inside the app: the logo, "Back to home", signing out
        return redirect(home())

    if pathname in PUBLIC_ROUTES:
        if auth.is_signed_in():
            return redirect(AFTER_SIGN_IN)
        return public(login_page() if pathname == "/login" else register_page(), False)

    if not is_private(pathname):
        # an unknown route: a 404 in whichever shell the visitor is already in
        user = auth.current_user()
        if user is None:
            return public(not_found_page(False), False)
        return not_found_page(True), header_actions(False, True), navbar_account(user), {"public": False}, no_update, no_update

    # a private page: the guard. This is for the user's experience only -
    # the API checks the token on every call and is what keeps data private.
    if not auth.has_session():
        return redirect(login_url(pathname))
    note = no_update
    try:
        user = auth.current_user(refresh=True)
    except NotAuthenticated:
        return redirect(login_url(pathname), toast(SESSION_EXPIRED, "yellow", "Signed out"))
    except ApiUnavailable:
        # the pages still show mock data, so carry on with what the session knows
        user = auth.current_user()
        note = toast(API_DOWN, "red")
    return private_page(pathname, user), header_actions(False, True), navbar_account(user), {"public": False}, no_update, note


# Redirects run in the browser. A callback cannot send render_page's own
# input (url.pathname) back to it - Dash drops that as a circular chain - so
# this does what dcc.Link does: change the address, then tell dcc.Location to
# read it again. replaceState keeps the page being left out of the history,
# so Back does not bounce off the same redirect.
clientside_callback(
    """
    function (target) {
        if (target && target.href) {
            // leaving the dashboard, for the public website
            window.location.replace(target.href);
        } else if (target) {
            window.history.replaceState({}, "", target.pathname + target.search);
            window.dispatchEvent(new CustomEvent("_dashprivate_pushstate"));
        }
    }
    """,
    Input("redirect", "data"),
    prevent_initial_call=True,
)


@callback(
    Output("url", "pathname", allow_duplicate=True),
    Output("url", "search", allow_duplicate=True),
    Input({"type": "nav-button", "href": ALL, "slot": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def navigate(_clicks):
    # buttons fire with n_clicks=0 when a page first renders them - ignore that
    if not ctx.triggered_id or not ctx.triggered[0]["value"]:
        return no_update, no_update
    # a link may carry a query (/devices/add?device=...); without one, the
    # empty search drops a left-over ?next= from the sign-in page
    pathname, _, query = ctx.triggered_id["href"].partition("?")
    return pathname, f"?{query}" if query else ""


@callback(
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "detail-tabs", "kind": ALL}, "value"),
    State("url", "pathname"),
    prevent_initial_call=True,
)
def switch_detail_tab(values, pathname):
    if not ctx.triggered_id or not values or not values[0]:
        return no_update
    route = _detail_route(pathname or "")
    if route is None:
        return no_update
    kind, item_id, _ = route
    # keep the bin or device in the address; only the tab changes
    target = f"/{kind}/{item_id}/{values[0]}" if item_id else f"/{kind}/{values[0]}"
    return no_update if target == pathname else target


# ------------------------------------------------------------------- shell ---

@callback(
    Output("appshell", "navbar"),
    Output("navbar", "display"),
    Output("burger", "display"),
    Input("burger", "opened"),
    Input("shell", "data"),
)
def toggle_navbar(opened, shell):
    # the public shell has no navbar at all, and so no burger to open it;
    # display:none also keeps its links away from the Tab key
    public = shell["public"]
    hidden = "none" if public else None
    collapsed = {"mobile": public or not opened, "desktop": public}
    return {**NAVBAR, "collapsed": collapsed}, hidden, hidden


@callback(Output("burger", "opened"), Input("url", "pathname"), prevent_initial_call=True)
def close_navbar_on_navigate(_):
    return False


@callback(
    Output({"type": "graph", "index": ALL}, "figure"),
    Input("color-scheme-toggle", "computedColorScheme"),
    Input({"type": "graph", "index": ALL}, "id"),
)
def apply_figure_template(color_scheme, ids):
    # Patch only swaps the template, so figures are not rebuilt or resent.
    # With Patch the template object is required, not just its name.
    template = figure_template(color_scheme)
    patches = []
    for _ in ids:
        patch = Patch()
        patch["layout"]["template"] = template
        patches.append(patch)
    return patches


# ------------------------------------------------------------ live charts ---

def _register_live_range(kind):
    @callback(
        Output({"type": "graph", "index": f"{kind}-live-chart"}, "figure", allow_duplicate=True),
        Input({"type": "live-range", "kind": kind}, "value"),
        State("color-scheme-toggle", "computedColorScheme"),
        State("url", "pathname"),
        prevent_initial_call=True,
    )
    def update_range(value, color_scheme, pathname):
        item_id = _detail_route(pathname)[1]
        if item_id is None:
            return no_update
        hours = int(value or 24)
        try:
            if kind == "bin":
                readings = api_client.get_bin_records(item_id, hours)
            else:
                readings = api_client.get_device_records(item_id, hours)
            names = {d["id"]: d["name"] for d in api_client.list_devices()}
        except (NotAuthenticated, ApiError, ApiUnavailable):
            # keep the chart as it is; the next page load shows what's wrong
            return no_update
        figure = readings_figure(readings, names)
        figure.update_layout(template=figure_template(color_scheme))
        return figure


for _kind in ("bin", "device"):
    _register_live_range(_kind)


# ------------------------------------------------------- sign in / sign up ---

def _clicked():
    # a callback also fires when one of its inputs is first rendered (a page
    # or a modal opening). Only a real click or Enter key triggers it with a
    # count, so look at what triggered this call, not at every input.
    return any(item["value"] for item in ctx.triggered)


def _api_problem(error):
    """The message for an API failure that no form field explains."""
    if isinstance(error, ApiUnavailable):
        return API_DOWN
    if error.status == 429:
        return TOO_MANY_ATTEMPTS
    return "Something went wrong. Please try again."


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("login-error", "children"),
    Output("login-error", "hide"),
    Input("login-submit", "n_clicks"),
    Input("login-email", "n_submit"),
    Input("login-password", "n_submit"),
    State("login-email", "value"),
    State("login-password", "value"),
    State("url", "search"),
    running=[(Output("login-submit", "loading"), True, False)],
    prevent_initial_call=True,
)
def sign_in(n_clicks, email_enter, password_enter, email, password, search):
    if not _clicked():
        return no_update, no_update, no_update
    if not (email or "").strip() or not password:
        return no_update, "Enter your email and password.", False
    try:
        auth.login(email.strip(), password)
    except ApiError as error:
        # 401: the API answers the same for an unknown email and a wrong password
        message = "Email or password is incorrect." if error.status in (401, 422) else _api_problem(error)
        return no_update, message, False
    except ApiUnavailable as error:
        return no_update, _api_problem(error), False
    return goto(next_page(search)), no_update, True


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("register-error", "children"),
    Output("register-error", "hide"),
    Output("register-email", "error"),
    Output("register-name", "error"),
    Output("register-password", "error"),
    Output("register-confirm", "error"),
    Input("register-submit", "n_clicks"),
    Input("register-email", "n_submit"),
    Input("register-name", "n_submit"),
    Input("register-password", "n_submit"),
    Input("register-confirm", "n_submit"),
    State("register-email", "value"),
    State("register-name", "value"),
    State("register-password", "value"),
    State("register-confirm", "value"),
    running=[(Output("register-submit", "loading"), True, False)],
    prevent_initial_call=True,
)
def sign_up(n_clicks, enter_1, enter_2, enter_3, enter_4, email, name, password, confirm):
    if not _clicked():
        return (no_update,) * 7

    def answer(alert=None, email_error=None, name_error=None, password_error=None, confirm_error=None):
        return no_update, alert, alert is None, email_error, name_error, password_error, confirm_error

    email, name, password = (email or "").strip(), (name or "").strip(), password or ""
    errors = {}
    if not EMAIL_PATTERN.match(email):
        errors["email_error"] = "Enter a valid email address."
    if len(name) > MAX_NAME:
        errors["name_error"] = f"Keep it to {MAX_NAME} characters or fewer."
    if len(password) < MIN_PASSWORD:
        errors["password_error"] = f"Use at least {MIN_PASSWORD} characters."
    if (confirm or "") != password:
        errors["confirm_error"] = "The passwords don’t match."
    if errors:
        return answer(**errors)

    try:
        auth.register(email, password, name or None)
    except ApiError as error:
        if error.status == 409:
            taken = ["An account with this email already exists. ", dmc.Anchor("Sign in?", href="/login", fw=600, size="sm")]
            return answer(alert=taken)
        if error.status == 422:
            # the API is stricter than the quick checks above
            fields = error.fields
            return answer(
                email_error="Enter a valid email address." if "email" in fields else None,
                name_error="That name can’t be used." if "display_name" in fields else None,
                password_error=f"Use {MIN_PASSWORD} to 128 characters." if "password" in fields else None,
                alert=None if fields & {"email", "display_name", "password"} else _api_problem(error),
            )
        return answer(alert=_api_problem(error))
    except ApiUnavailable as error:
        return answer(alert=_api_problem(error))
    return goto(AFTER_SIGN_IN), None, True, None, None, None, None


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Input("logout-button", "n_clicks"),
    prevent_initial_call=True,
)
def sign_out(n_clicks):
    if not _clicked():
        return no_update, no_update
    auth.logout()
    return goto("/"), toast("You’ve been signed out.")


# ----------------------------------------------------------------- account ---

def _session_ended(pathname):
    """The `redirect` and `notify` values for a token the API turned away."""
    return goto(login_url(pathname or "/account")), toast(SESSION_EXPIRED, "yellow", "Signed out")


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Output("navbar-account", "children", allow_duplicate=True),
    Output("account-name", "error"),
    Input("account-name-save", "n_clicks"),
    Input("account-name", "n_submit"),
    State("account-name", "value"),
    State("url", "pathname"),
    running=[(Output("account-name-save", "loading"), True, False)],
    prevent_initial_call=True,
)
def save_display_name(n_clicks, enter, name, pathname):
    if not _clicked():
        return (no_update,) * 4
    name = (name or "").strip()
    if len(name) > MAX_NAME:
        return no_update, no_update, no_update, f"Keep it to {MAX_NAME} characters or fewer."
    try:
        user = auth.update_display_name(name or None)
    except NotAuthenticated:
        return *_session_ended(pathname), no_update, no_update
    except (ApiError, ApiUnavailable) as error:
        return no_update, toast(_api_problem(error), "red"), no_update, no_update
    return no_update, toast("Display name saved."), navbar_account(user), None


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Output("account-current-password", "error"),
    Output("account-new-password", "error"),
    Output("account-confirm-password", "error"),
    Output("account-current-password", "value"),
    Output("account-new-password", "value"),
    Output("account-confirm-password", "value"),
    Input("account-password-save", "n_clicks"),
    Input("account-current-password", "n_submit"),
    Input("account-new-password", "n_submit"),
    Input("account-confirm-password", "n_submit"),
    State("account-current-password", "value"),
    State("account-new-password", "value"),
    State("account-confirm-password", "value"),
    State("url", "pathname"),
    running=[(Output("account-password-save", "loading"), True, False)],
    prevent_initial_call=True,
)
def change_password(n_clicks, enter_1, enter_2, enter_3, current, new, confirm, pathname):
    if not _clicked():
        return (no_update,) * 8

    def answer(current_error=None, new_error=None, confirm_error=None, note=no_update):
        return no_update, note, current_error, new_error, confirm_error, no_update, no_update, no_update

    current, new = current or "", new or ""
    errors = {}
    if not current:
        errors["current_error"] = "Enter your current password."
    if len(new) < MIN_PASSWORD:
        errors["new_error"] = f"Use at least {MIN_PASSWORD} characters."
    if (confirm or "") != new:
        errors["confirm_error"] = "The passwords don’t match."
    if errors:
        return answer(**errors)

    try:
        auth.change_password(current, new)
    except NotAuthenticated:
        return *_session_ended(pathname), *((no_update,) * 6)
    except ApiError as error:
        if error.status == 401:
            return answer(current_error="That’s not your current password.")
        if error.status == 422:
            return answer(new_error=f"Use {MIN_PASSWORD} to 128 characters.")
        return answer(note=toast(_api_problem(error), "red"))
    except ApiUnavailable as error:
        return answer(note=toast(_api_problem(error), "red"))
    return no_update, toast("Password changed."), None, None, None, "", "", ""


@callback(
    Output("account-delete-modal", "opened"),
    Output("account-delete-password", "value", allow_duplicate=True),
    Output("account-delete-password", "error", allow_duplicate=True),
    Input("account-delete-open", "n_clicks"),
    Input("account-delete-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_delete_modal(open_clicks, cancel_clicks):
    if not _clicked():
        return no_update, no_update, no_update
    # always start (and leave) with an empty password box
    return ctx.triggered_id == "account-delete-open", "", None


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Output("account-delete-password", "error"),
    Input("account-delete-confirm", "n_clicks"),
    Input("account-delete-password", "n_submit"),
    State("account-delete-password", "value"),
    State("url", "pathname"),
    running=[(Output("account-delete-confirm", "loading"), True, False)],
    prevent_initial_call=True,
)
def delete_account(n_clicks, enter, password, pathname):
    if not _clicked():
        return no_update, no_update, no_update
    if not password:
        return no_update, no_update, "Enter your password to confirm."
    try:
        auth.delete_account(password)
    except NotAuthenticated:
        return *_session_ended(pathname), no_update
    except ApiError as error:
        if error.status == 401:
            return no_update, no_update, "That’s not your password."
        return no_update, toast(_api_problem(error), "red"), no_update
    except ApiUnavailable as error:
        return no_update, toast(_api_problem(error), "red"), no_update
    return goto("/"), toast("Your account and its data have been deleted.", title="Account deleted"), None


# ------------------------------------------------------- device setup form ---
# The device name + bin fields (pages._device_setup_fields) that both the
# pairing and the registration wizards end with. Their ids start "pair" / "reg".

HIDDEN = {"display": "none"}
SHOWN = {}
SETUP_ERRORS = ("name_error", "bin_error", "new_name_error", "country_error")


def _setup_error_outputs(prefix):
    return {
        "name_error": Output(f"{prefix}-device-name", "error"),
        "bin_error": Output(f"{prefix}-bin", "error"),
        "new_name_error": Output(f"{prefix}-new-bin-name", "error"),
        "country_error": Output(f"{prefix}-new-bin-country", "error"),
    }


def _setup_states(prefix):
    return {
        "name": State(f"{prefix}-device-name", "value"),
        "mode": State(f"{prefix}-bin-mode", "value"),
        "bin_id": State(f"{prefix}-bin", "value"),
        "new_name": State(f"{prefix}-new-bin-name", "value"),
        "new_location": State(f"{prefix}-new-bin-location", "value"),
        "country": State(f"{prefix}-new-bin-country", "value"),
    }


def _setup_errors(name, mode, bin_id, new_name, country):
    """The form's field errors (SETUP_ERRORS), all None when it can be sent."""
    errors = dict.fromkeys(SETUP_ERRORS)
    if not name:
        errors["name_error"] = "Give the device a name."
    elif len(name) > MAX_NAME:
        errors["name_error"] = f"Keep it to {MAX_NAME} characters or fewer."
    if mode == "new":
        if not new_name:
            errors["new_name_error"] = "Give the bin a name."
        if not country:
            errors["country_error"] = "Choose a country."
    elif not bin_id:
        errors["bin_error"] = "Choose a bin."
    return errors


def _chosen_bin(mode, bin_id, new_name, new_location, country):
    """The id of the bin the form picked, creating the bin first if it's new."""
    if mode == "new":
        return api_client.create_bin(new_name, country, new_location)["id"]
    return bin_id


def _bin_choices(bins):
    """(options, preselected bin, bin mode) for the form, from GET /bins."""
    options = [{"value": b["id"], "label": b["name"] or "Unnamed bin"} for b in bins]
    first = options[0]["value"] if len(options) == 1 else None
    return options, first, "existing" if options else "new"


def _first_reading(state):
    """"<device> is sending readings to <bin>." once the device has checked in
    since it was set up (state["seen_before"]), else None."""
    device = api_client.get_device(state["device_id"])
    seen = device.get("last_seen_at")
    before = state.get("seen_before")
    if not seen or (before and datetime.fromisoformat(seen) <= datetime.fromisoformat(before)):
        return None
    return f"{device['name']} is sending readings to {device['bin']['name']}."


def _register_setup_form(prefix):
    # an error clears as soon as the field is changed, rather than on the next submit
    for field in ("device-name", "bin", "new-bin-name", "new-bin-country"):
        clientside_callback(
            "function () { return null; }",
            Output(f"{prefix}-{field}", "error", allow_duplicate=True),
            Input(f"{prefix}-{field}", "value"),
            prevent_initial_call=True,
        )

    @callback(
        Output(f"{prefix}-bin", "style"),
        Output(f"{prefix}-new-bin", "style"),
        Input(f"{prefix}-bin-mode", "value"),
    )
    def show_bin_choice(mode):
        return (HIDDEN, SHOWN) if mode == "new" else (SHOWN, HIDDEN)


for _prefix in ("pair", "reg"):
    _register_setup_form(_prefix)


# ---------------------------------------------------------- pairing wizard ---
#
# 0 Pair     the dashboard issues a code; the page polls until the device uses it
# 1 Confirm  shows the hardware ID that used it; "not mine" unpairs it again
# 2 Set up   name + an existing or new bin
# 3 Done     polls until the device checks in after setup
#
# pair-state holds {code, device_id, seen_before}: nothing secret. The
# device's API key goes straight from the API to the device and never
# passes through the dashboard.

PAIR_PAGE = "/devices/add"

PAIR_OUTPUTS = {
    "active": Output("pair-stepper", "active", allow_duplicate=True),
    "state": Output("pair-state", "data", allow_duplicate=True),
    "poll_off": Output("pair-poll", "disabled", allow_duplicate=True),
    "empty_style": Output("pair-code-empty", "style", allow_duplicate=True),
    "live_style": Output("pair-code-live", "style", allow_duplicate=True),
    "code": Output("pair-code-value", "children", allow_duplicate=True),
    "expiry": Output("pair-code-expiry", "children", allow_duplicate=True),
    "status": Output("pair-code-status", "children", allow_duplicate=True),
    "loader_style": Output("pair-code-loader", "style", allow_duplicate=True),
    "hardware": Output("pair-device-hardware", "children", allow_duplicate=True),
    "model": Output("pair-device-model", "children", allow_duplicate=True),
    "firmware": Output("pair-device-firmware", "children", allow_duplicate=True),
    "name": Output("pair-device-name", "value", allow_duplicate=True),
    "done_title": Output("pair-done-title", "children", allow_duplicate=True),
    "done_text": Output("pair-done-text", "children", allow_duplicate=True),
    "redirect": Output("redirect", "data", allow_duplicate=True),
    "notify": Output("notify", "sendNotifications", allow_duplicate=True),
}


def pair_update(**values):
    return {key: values.get(key, no_update) for key in PAIR_OUTPUTS}


def pair_signed_out():
    redirect, note = _session_ended(PAIR_PAGE)
    return pair_update(redirect=redirect, notify=note, poll_off=True)


def _minutes_left(expires_at):
    left = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
    minutes = max(0, int(left.total_seconds() // 60))
    return "Expires in less than a minute." if minutes < 1 else f"Expires in {minutes} min."


def _show_device(device, state):
    """Move to the Confirm step for this device."""
    return pair_update(
        active=1,
        state={**(state or {}), "device_id": device["id"]},
        poll_off=True,
        hardware=device["hardware_id"],
        model=device["model"] or "Not reported",
        firmware=device["firmware_version"] or "Not reported",
    )


def _back_to_start(note=no_update):
    return pair_update(
        active=0, state=None, poll_off=True, empty_style=SHOWN, live_style=HIDDEN, notify=note,
    )


@callback(
    output=PAIR_OUTPUTS,
    inputs={"clicks": [Input("pair-start", "n_clicks"), Input("pair-restart", "n_clicks"), Input("pair-another", "n_clicks")]},
    prevent_initial_call=True,
)
def start_pairing(clicks):
    if not _clicked():
        return pair_update()
    try:
        code = api_client.create_pairing_code()
    except NotAuthenticated:
        return pair_signed_out()
    except (ApiError, ApiUnavailable) as error:
        return pair_update(notify=toast(_api_problem(error), "red"))
    return pair_update(
        active=0,
        state={"code": code["code"]},
        poll_off=False,
        empty_style=HIDDEN,
        live_style=SHOWN,
        code=code["code"],
        expiry=_minutes_left(code["expires_at"]),
        status="Waiting for your device to use the code…",
        loader_style=SHOWN,
        name="",
    )


@callback(
    output=PAIR_OUTPUTS,
    inputs={"_ticks": Input("pair-poll", "n_intervals")},
    state={"state": State("pair-state", "data"), "active": State("pair-stepper", "active")},
    prevent_initial_call=True,
)
def poll_pairing(_ticks, state, active):
    if not state:
        return pair_update(poll_off=True)
    try:
        if active == 3:
            return _check_first_reading(state)
        status = api_client.pairing_status(state["code"])
    except NotAuthenticated:
        return pair_signed_out()
    except ApiUnavailable:
        return pair_update(status="Can’t reach CompostIQ right now. Still trying…")
    except ApiError:
        return pair_update(poll_off=True, status="This code is no longer valid. Get a new code.", loader_style=HIDDEN)

    if status["status"] == "pending":
        return pair_update(expiry=_minutes_left(status["expires_at"]))
    if status["status"] == "expired":
        return pair_update(poll_off=True, expiry="", loader_style=HIDDEN,
                           status="This code has expired. Get a new code to try again.")
    if status["device"] is None:
        # used, but the device has since been unpaired or paired elsewhere
        return pair_update(poll_off=True, loader_style=HIDDEN,
                           status="The code was used, but that device isn’t paired with your account any more.")
    return _show_device(status["device"], state)


def _check_first_reading(state):
    online = _first_reading(state)
    if online is None:
        return pair_update()
    return pair_update(poll_off=True, done_title="Your device is online", done_text=online)


@callback(
    output=PAIR_OUTPUTS,
    inputs={"_ready": Input("pair-init", "data")},
    state={"search": State("url", "search")},
    prevent_initial_call="initial_duplicate",
)
def resume_setup(_ready, search):
    """/devices/add?device=<id>: finish setting up a device paired earlier."""
    device_id = parse_qs((search or "").lstrip("?")).get("device", [""])[0]
    if not device_id:
        return pair_update()
    try:
        device = api_client.get_device(device_id)
    except NotAuthenticated:
        return pair_signed_out()
    except (ApiError, ApiUnavailable) as error:
        message = "That device isn’t paired with your account." if isinstance(error, ApiError) and error.status in (404, 422) else _api_problem(error)
        return pair_update(notify=toast(message, "red"))
    if device["set_up"]:
        return pair_update(notify=toast(f"{device['name']} is already set up."))
    return _show_device(device, None)


@callback(
    output=PAIR_OUTPUTS,
    inputs={"_clicks": Input("pair-reject", "n_clicks")},
    state={"state": State("pair-state", "data")},
    prevent_initial_call=True,
)
def reject_device(_clicks, state):
    """Not the user's device: unpair it at once, so its new key stops working."""
    if not _clicked() or not state:
        return pair_update()
    try:
        api_client.unpair_device(state["device_id"])
    except NotAuthenticated:
        return pair_signed_out()
    except ApiError as error:
        if error.status != 404:
            return pair_update(notify=toast(_api_problem(error), "red"))
    except ApiUnavailable as error:
        return pair_update(notify=toast(_api_problem(error), "red"))
    return _back_to_start(toast("That device was unpaired. Get a new code to pair yours.", "yellow"))


@callback(
    Output("pair-stepper", "active", allow_duplicate=True),
    Output("pair-bin", "data"),
    Output("pair-bin", "value"),
    Output("pair-bin-mode", "value"),
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Input("pair-confirm", "n_clicks"),
    running=[(Output("pair-confirm", "loading"), True, False)],
    prevent_initial_call=True,
)
def confirm_registration(_clicks):
    if not _clicked():
        return (no_update,) * 6
    try:
        bins = api_client.list_bins()
    except NotAuthenticated:
        return no_update, no_update, no_update, no_update, *_session_ended(PAIR_PAGE)
    except (ApiError, ApiUnavailable) as error:
        return no_update, no_update, no_update, no_update, no_update, toast(_api_problem(error), "red")
    options, first, mode = _bin_choices(bins)
    return 2, options, first, mode, no_update, no_update


@callback(
    output={**PAIR_OUTPUTS, **_setup_error_outputs("pair")},
    inputs={"_clicks": Input("pair-finish", "n_clicks")},
    state={"state": State("pair-state", "data"), **_setup_states("pair")},
    running=[(Output("pair-finish", "loading"), True, False)],
    prevent_initial_call=True,
)
def finish_setup(_clicks, state, name, mode, bin_id, new_name, new_location, country):
    if not _clicked() or not state:
        return {**pair_update(), **dict.fromkeys(SETUP_ERRORS, no_update)}

    name, new_name = (name or "").strip(), (new_name or "").strip()
    errors = _setup_errors(name, mode, bin_id, new_name, country)

    def answer(**values):
        return {**pair_update(**values), **errors}

    if any(errors.values()):
        return answer()

    try:
        bin_id = _chosen_bin(mode, bin_id, new_name, new_location, country)
        device = api_client.set_up_device(state["device_id"], name, bin_id)
    except NotAuthenticated:
        return {**pair_signed_out(), **errors}
    except (ApiError, ApiUnavailable) as error:
        if isinstance(error, ApiError) and error.status == 404:
            return answer(notify=toast("That device or bin isn’t on your account any more.", "red"))
        return answer(notify=toast(_api_problem(error), "red"))

    return answer(
        active=3,
        state={**state, "seen_before": device.get("last_seen_at")},
        poll_off=False,
        done_title="Waiting for the first reading…",
        done_text=f"{device['name']} is set up in {device['bin']['name']}. It checks in every few seconds, so its first reading should arrive shortly.",
        notify=toast(f"{device['name']} is set up.", title="Device registered"),
    )


# ----------------------------------------------------- registration wizard ---
#
# For a device that can't run the pairing flow: no code, no confirm.
#
# 0 Set up   name + an existing or new bin; finishing registers the device
# 1 API key  shown this once, to copy onto the device
# 2 Done     polls until the first reading arrives
#
# Unlike pairing, the key passes through the dashboard. It is never put in a
# store: it only sits in the step-1 text, which is emptied when the user
# moves on. reg-state holds {device_id, seen_before, resumed}.
#
# /devices/register?device=<id> sets up a registered device again after its
# bin was deleted. It still has its key, so step 1 is skipped.

REG_PAGE = "/devices/register"

REG_OUTPUTS = {
    "active": Output("reg-stepper", "active", allow_duplicate=True),
    "state": Output("reg-state", "data", allow_duplicate=True),
    "poll_off": Output("reg-poll", "disabled", allow_duplicate=True),
    "intro": Output("reg-setup-intro", "children", allow_duplicate=True),
    "name": Output("reg-device-name", "value", allow_duplicate=True),
    "bins": Output("reg-bin", "data", allow_duplicate=True),
    "bin": Output("reg-bin", "value", allow_duplicate=True),
    "mode": Output("reg-bin-mode", "value", allow_duplicate=True),
    "create_label": Output("reg-create", "children", allow_duplicate=True),
    "key": Output("reg-key-value", "children", allow_duplicate=True),
    "done_title": Output("reg-done-title", "children", allow_duplicate=True),
    "done_text": Output("reg-done-text", "children", allow_duplicate=True),
    "redirect": Output("redirect", "data", allow_duplicate=True),
    "notify": Output("notify", "sendNotifications", allow_duplicate=True),
}


def reg_update(**values):
    return {key: values.get(key, no_update) for key in REG_OUTPUTS}


def reg_signed_out():
    redirect, note = _session_ended(REG_PAGE)
    return reg_update(redirect=redirect, notify=note, poll_off=True)


@callback(
    output=REG_OUTPUTS,
    inputs={"_ready": Input("reg-init", "data"), "_another": Input("reg-another", "n_clicks")},
    state={"search": State("url", "search")},
    prevent_initial_call="initial_duplicate",
)
def open_registration(_ready, _another, search):
    """A fresh form with the user's bins: when the page opens, and for "Register another"."""
    another = ctx.triggered_id == "reg-another"
    if another and not _clicked():
        return reg_update()
    try:
        bins = api_client.list_bins()
    except NotAuthenticated:
        return reg_signed_out()
    except (ApiError, ApiUnavailable) as error:
        return reg_update(notify=toast(_api_problem(error), "red"))
    options, first, mode = _bin_choices(bins)
    fresh = reg_update(active=0, state=None, poll_off=True, intro=REGISTER_INTRO, name="",
                       bins=options, bin=first, mode=mode, create_label="Register device", key="")

    # ?device= only counts as the page opens; "Register another" starts afresh
    device_id = "" if another else parse_qs((search or "").lstrip("?")).get("device", [""])[0]
    if not device_id:
        return fresh
    try:
        device = api_client.get_device(device_id)
    except NotAuthenticated:
        return reg_signed_out()
    except (ApiError, ApiUnavailable) as error:
        message = "That device isn’t on your account." if isinstance(error, ApiError) and error.status in (404, 422) else _api_problem(error)
        return {**fresh, "notify": toast(message, "red")}
    if device["set_up"]:
        return {**fresh, "notify": toast(f"{device['name']} is already set up.")}
    return {
        **fresh,
        "state": {"device_id": device["id"], "resumed": True},
        "intro": "This device isn’t in a bin, so its readings are turned away. Choose a bin for it. "
                 "It keeps its API key, so nothing changes on the device.",
        "name": device["name"] or "",
        "create_label": "Save",
    }


@callback(
    output={**REG_OUTPUTS, **_setup_error_outputs("reg")},
    inputs={"_clicks": Input("reg-create", "n_clicks")},
    state={"state": State("reg-state", "data"), **_setup_states("reg")},
    running=[(Output("reg-create", "loading"), True, False)],
    prevent_initial_call=True,
)
def register_device(_clicks, state, name, mode, bin_id, new_name, new_location, country):
    if not _clicked():
        return {**reg_update(), **dict.fromkeys(SETUP_ERRORS, no_update)}

    name, new_name = (name or "").strip(), (new_name or "").strip()
    errors = _setup_errors(name, mode, bin_id, new_name, country)

    def answer(**values):
        return {**reg_update(**values), **errors}

    if any(errors.values()):
        return answer()

    resumed = bool(state and state.get("resumed"))
    try:
        bin_id = _chosen_bin(mode, bin_id, new_name, new_location, country)
        if resumed:
            device, api_key = api_client.set_up_device(state["device_id"], name, bin_id), None
        else:
            registered = api_client.register_device(name, bin_id)
            device, api_key = registered["device"], registered["api_key"]
    except NotAuthenticated:
        return {**reg_signed_out(), **errors}
    except (ApiError, ApiUnavailable) as error:
        if isinstance(error, ApiError) and error.status == 404:
            return answer(notify=toast("That device or bin isn’t on your account any more.", "red"))
        return answer(notify=toast(_api_problem(error), "red"))

    state = {"device_id": device["id"], "seen_before": device.get("last_seen_at")}
    if resumed:
        return answer(
            active=2,
            state=state,
            poll_off=False,
            done_title="Waiting for the next reading…",
            done_text=f"{device['name']} is set up in {device['bin']['name']}. It keeps its API key, so the next reading it sends will be stored.",
            notify=toast(f"{device['name']} is set up.", title="Device set up"),
        )
    return answer(active=1, state=state, key=api_key,
                  notify=toast(f"{device['name']} is registered.", title="Device registered"))


@callback(
    output=REG_OUTPUTS,
    inputs={"_clicks": Input("reg-saved", "n_clicks")},
    state={"state": State("reg-state", "data")},
    prevent_initial_call=True,
)
def key_saved(_clicks, state):
    """On to waiting for the first reading; the key leaves the page for good."""
    if not _clicked() or not state:
        return reg_update()
    return reg_update(
        active=2,
        key="",
        poll_off=False,
        done_title="Waiting for the first reading…",
        done_text="Start your device with the key. Its first reading shows up here as soon as it arrives.",
    )


@callback(
    output=REG_OUTPUTS,
    inputs={"_ticks": Input("reg-poll", "n_intervals")},
    state={"state": State("reg-state", "data")},
    prevent_initial_call=True,
)
def poll_registration(_ticks, state):
    if not state:
        return reg_update(poll_off=True)
    try:
        online = _first_reading(state)
    except NotAuthenticated:
        return reg_signed_out()
    except ApiUnavailable:
        return reg_update()
    except ApiError:
        # unpaired or gone in the meantime
        return reg_update(poll_off=True)
    if online is None:
        return reg_update()
    return reg_update(poll_off=True, done_title="Your device is online", done_text=online)


# ------------------------------------------------------------- new API key ---
#
# "New API key" on a registered device's settings tab (/device/<id>/settings)
# opens a modal: confirm, then the new key, shown once. As in the
# registration wizard, the key only sits in the modal's text and is emptied
# on the way out. new-key-device holds {id, name}.

NEW_KEY_OUTPUTS = {
    "opened": Output("new-key-modal", "opened", allow_duplicate=True),
    "device": Output("new-key-device", "data", allow_duplicate=True),
    "warning": Output("new-key-warning", "children", allow_duplicate=True),
    "ask_style": Output("new-key-ask", "style", allow_duplicate=True),
    "show_style": Output("new-key-show", "style", allow_duplicate=True),
    "key": Output("new-key-value", "children", allow_duplicate=True),
    "redirect": Output("redirect", "data", allow_duplicate=True),
    "notify": Output("notify", "sendNotifications", allow_duplicate=True),
}


def new_key_update(**values):
    return {key: values.get(key, no_update) for key in NEW_KEY_OUTPUTS}


def new_key_signed_out(pathname):
    redirect, note = _session_ended(pathname)
    return new_key_update(opened=False, key="", redirect=redirect, notify=note)


# only devices registered with an API key get a new key; the API enforces it
# (409), and the dashboard never offers it for a paired device either
PAIRED_NO_NEW_KEY = "Only devices registered with an API key can get a new key. To give a paired device a new key, pair it again."


def _device_gone(error):
    if isinstance(error, ApiError) and error.status == 404:
        return "That device isn’t on your account any more."
    if isinstance(error, ApiError) and error.status == 409:
        return PAIRED_NO_NEW_KEY
    return _api_problem(error)


@callback(
    output=NEW_KEY_OUTPUTS,
    inputs={"_clicks": Input({"type": "new-key", "device": ALL}, "n_clicks")},
    state={"pathname": State("url", "pathname")},
    prevent_initial_call=True,
)
def ask_new_key(_clicks, pathname):
    # the button fires with n_clicks=0 when the settings tab renders
    if not ctx.triggered_id or not _clicked():
        return new_key_update()
    try:
        device = api_client.get_device(ctx.triggered_id["device"])
    except NotAuthenticated:
        return new_key_signed_out(pathname)
    except (ApiError, ApiUnavailable) as error:
        return new_key_update(notify=toast(_device_gone(error), "red"))
    if device["registration"] != "manual":
        # the settings tab only shows the button for registered devices
        return new_key_update(notify=toast(PAIRED_NO_NEW_KEY, "red"))
    name = device["name"] or "This device"
    return new_key_update(
        opened=True,
        device={"id": device["id"], "name": name},
        warning=f"{name}’s current key stops working straight away. It can’t send readings again until you put the new key on it.",
        ask_style=SHOWN,
        show_style=HIDDEN,
        key="",
    )


@callback(
    output=NEW_KEY_OUTPUTS,
    inputs={"_clicks": Input("new-key-confirm", "n_clicks")},
    state={"device": State("new-key-device", "data"), "pathname": State("url", "pathname")},
    running=[(Output("new-key-confirm", "loading"), True, False)],
    prevent_initial_call=True,
)
def generate_new_key(_clicks, device, pathname):
    if not _clicked() or not device:
        return new_key_update()
    try:
        replaced = api_client.new_device_key(device["id"])
    except NotAuthenticated:
        return new_key_signed_out(pathname)
    except ApiError as error:
        # refused for good (gone, or not a registered device): close rather than offer a retry
        return new_key_update(opened=False, device=None, key="", notify=toast(_device_gone(error), "red"))
    except ApiUnavailable as error:
        return new_key_update(notify=toast(_api_problem(error), "red"))
    return new_key_update(
        ask_style=HIDDEN,
        show_style=SHOWN,
        key=replaced["api_key"],
        notify=toast(f"{device['name']}’s old key no longer works.", title="New key generated"),
    )


@callback(
    output=NEW_KEY_OUTPUTS,
    inputs={"_clicks": [Input("new-key-cancel", "n_clicks"), Input("new-key-saved", "n_clicks")]},
    prevent_initial_call=True,
)
def close_new_key(_clicks):
    if not _clicked():
        return new_key_update()
    return new_key_update(opened=False, device=None, key="")


# ---------------------------------------------------------------- new bin ---

@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Input("new-bin-submit", "n_clicks"),
    State("new-bin-name", "value"),
    State("new-bin-location", "value"),
    State("new-bin-country", "value"),
    State("url", "pathname"),
    running=[(Output("new-bin-submit", "loading"), True, False)],
    prevent_initial_call=True,
)
def create_bin(n_clicks, name, location, country, pathname):
    if not _clicked():
        return no_update, no_update
    name = (name or "").strip()
    location = (location or "").strip()
    if not name or not country:
        return no_update, toast("A bin needs a name and a country.", "red")
    if len(name) > 80 or len(location) > 120:
        return no_update, toast("Keep the name under 80 characters and location under 120.", "red")
    try:
        bin_data = api_client.create_bin(name, country, location)
    except NotAuthenticated:
        return _session_ended(pathname)
    except (ApiError, ApiUnavailable) as error:
        return no_update, toast(_api_problem(error), "red")
    return goto(f"/bin/{bin_data['id']}/settings"), toast("Bin created.")


# ------------------------------------------------------------- home alerts ---

@callback(Output("home-alerts", "children"), Input("alerts-poll", "n_intervals"), prevent_initial_call=True)
def refresh_alerts(_):
    try:
        return alerts_grid(api_client.list_alerts())
    except (NotAuthenticated, ApiError, ApiUnavailable):
        # keep what's shown; the next page load deals with the problem
        return no_update


# ------------------------------------------------------------ bin settings ---

@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Output("page-root", "children", allow_duplicate=True),
    Input("save-bin-settings", "n_clicks"),
    State("bin-name", "value"),
    State("bin-location", "value"),
    State("bin-country", "value"),
    State("url", "pathname"),
    running=[(Output("save-bin-settings", "loading"), True, False)],
    prevent_initial_call=True,
)
def save_bin_settings(n_clicks, name, location, country, pathname):
    if not n_clicks:
        return no_update, no_update, no_update
    bin_id = _detail_route(pathname)[1]
    try:
        api_client.update_bin(bin_id, name=name, location=location or "", country_code=country)
    except NotAuthenticated:
        return *_session_ended(pathname), no_update
    except ApiError:
        return no_update, toast("Give the bin a name (up to 80 characters).", "red"), no_update
    except ApiUnavailable:
        return no_update, toast(API_DOWN, "red"), no_update
    # redraw the page so the header shows the new name too
    return no_update, toast("Bin settings saved."), bin_detail_page(bin_id, "settings")


@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Input("delete-bin", "submit_n_clicks"),
    State("url", "pathname"),
    prevent_initial_call=True,
)
def delete_bin(confirmed, pathname):
    if not confirmed:
        return no_update, no_update
    try:
        api_client.delete_bin(_detail_route(pathname)[1])
    except NotAuthenticated:
        return _session_ended(pathname)
    except (ApiError, ApiUnavailable) as error:
        return no_update, toast(_api_problem(error), "red")
    return goto("/bins"), toast("Bin deleted.")


# --------------------------------------------------------- device settings ---

@callback(
    Output("redirect", "data", allow_duplicate=True),
    Output("notify", "sendNotifications", allow_duplicate=True),
    Output("page-root", "children", allow_duplicate=True),
    Input("save-device-settings", "n_clicks"),
    State("device-name", "value"),
    State("device-bin", "value"),
    State("url", "pathname"),
    running=[(Output("save-device-settings", "loading"), True, False)],
    prevent_initial_call=True,
)
def save_device_settings(n_clicks, name, bin_id, pathname):
    if not n_clicks:
        return no_update, no_update, no_update
    device_id = _detail_route(pathname)[1]
    try:
        api_client.update_device(device_id, name=name, bin_id=bin_id)
    except NotAuthenticated:
        return *_session_ended(pathname), no_update
    except ApiError:
        return no_update, toast("Give the device a name (up to 80 characters).", "red"), no_update
    except ApiUnavailable:
        return no_update, toast(API_DOWN, "red"), no_update
    # redraw the page so the header shows the new name and bin too
    return no_update, toast("Device settings saved."), device_detail_page(device_id, "settings")


# ------------------------------------------------------- placeholder toasts ---

PLACEHOLDER_TOASTS = {
    "export-report": "Report export prepared",
    "review-alerts": "All alerts marked as reviewed",
    "notification-button": "No new notifications",
}


def _register_toast(component_id, message):
    @callback(
        Output("notify", "sendNotifications", allow_duplicate=True),
        Input(component_id, "n_clicks"),
        prevent_initial_call=True,
    )
    def show(n_clicks):
        return toast(message) if n_clicks else no_update


for _component_id, _message in PLACEHOLDER_TOASTS.items():
    _register_toast(_component_id, _message)


if __name__ == "__main__":
    # 8050 is the production port behind NGINX; locally the simulator also
    # defaults to 8050, so run one of them on another port when using both
    app.run(host="127.0.0.1", port=8050, debug=False)
