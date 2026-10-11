# CompostIQ dashboard

The monitoring dashboard, built with [Plotly Dash](https://dash.plotly.com/) and [dash-mantine-components](https://www.dash-mantine-components.com/) 2.x (Mantine v8).

Signed out, it is a public site. Signed in, a user reaches their own bins, devices and readings through the API.

## Run locally

The dashboard needs the API, and the API needs the database:

```bash
docker compose up -d                                   # repo root: the database
uv sync --all-packages
(cd backendAPI && uv run uvicorn main:app --port 8000)  # the API, in its own terminal
cp monitoringSystem/.env.example monitoringSystem/.env  # then set DASHBOARD_SECRET_KEY
uv run python monitoringSystem/app.py
```

Open `http://127.0.0.1:8050/login`, and create an account or sign in as the development user (see the [API README](../backendAPI/README.md)).

| Variable | Notes |
|---|---|
| `API_URL` | where the dashboard's server reaches the API. Defaults to `http://127.0.0.1:8000` |
| `PUBLIC_API_URL` | where devices send readings, shown with a registered device's API key. Defaults to `https://api.compostiq.win` |
| `WEBSITE_URL` | the public website, where `/` and signing out lead. Defaults to `https://compostiq.win`; locally `http://127.0.0.1:8070` |
| `DASHBOARD_SECRET_KEY` | signs the session cookie |

**If `DASHBOARD_SECRET_KEY` is missing:**
- `python app.py` (local development) makes up a random key and logs a warning. It works, but everyone is signed out each time the dashboard restarts.
- gunicorn (the VM) refuses to start. With two workers, a random key per worker would sign users out at random.

`python app.py` also sends the session cookie over plain HTTP. Under gunicorn the cookie is `Secure`, so signing in needs HTTPS.

## Pages

| Route | Access | Page |
|---|---|---|
| `/` | public | no page of its own: redirects to `/dashboard` when signed in, otherwise to the public website (`WEBSITE_URL`, [website/](../website/README.md)) |
| `/login`, `/register` | public | a signed-in user is sent on to `/dashboard` |
| `/dashboard` | private | overview |
| `/bins`, `/bins/new`, `/bin/<id>/<tab>` | private | list bins; create one with a name, location and country; view readings and devices; edit its details in settings |
| `/devices` | private | the user's devices from the API; unfinished ones are marked *Needs setup*, the rest link to their device page |
| `/devices/add` | private | the pairing wizard: get a code → the device redeems it → confirm its hardware ID → name and bin → wait for the first reading. `?device=<id>` resumes setup for a device paired earlier |
| `/devices/register` | private | registering without pairing: name and bin → the device's API key, shown once with the endpoint and an example → wait for the first reading. `?device=<id>` gives a registered device a new bin after its bin was deleted |
| `/device/<id>/<tab>` | private | one device: live readings, history, settings (rename, move to another bin). A registered device's settings also have **New API key**: confirm, then the replacement key, shown once (the old one stops working) |
| `/account` | private | display name, change password, delete account |
| anything else | | 404 page |

A signed-out visitor who opens a private page is sent to `/login?next=<page>` and returned to that page after signing in.

## How sign-in works

- The browser never talks to the API. Dash callbacks run on the server and call it through `api_client.py`.
- The user's token lives only in the signed, HttpOnly Flask session cookie (`SameSite=Lax`, 8 hours, like the token). It is never put in a `dcc.Store` or anywhere browser JavaScript can read.
- `render_page` in `app.py` is the route guard. Opening a private page asks the API who the user is, which also proves the token still works.
- If the API turns the token away mid-session, the session is cleared, the user sees "Your session expired", and they land on `/login?next=<the page they were on>`.
- If the API cannot be reached, the user sees "Can’t reach CompostIQ right now" and the page still renders.
- The guard is there for the user's experience. What keeps data private is the API checking the token on every call.

## Structure

- `app.py`: the Dash server, session cookie settings, routing and route guard, and all callbacks.
- `pages.py`: page layouts, built from the reusable components.
- `components.py`: the shell (header, navbar), cards and other reusable pieces.
- `api_client.py`: the only module that calls the API. Raises `NotAuthenticated`, `ApiUnavailable` or `ApiError`.
- `auth.py`: register, login, logout and the signed-in user, on top of the Flask session.
- `settings.py`: environment variables, loaded from `.env`.
- `theme.py`: Mantine theme, Plotly templates and the icon set (Tabler, through DashIconify).
- `figures.py`: Plotly figure factories.
- `data.py`: legacy placeholder data for older mock components.
- `assets/styles.css`: the few styles Mantine props cannot reach.

One `MantineProvider`, one `AppShell` and one `NotificationContainer` wrap the whole app. The public pages use the same shell with the navbar hidden.

## Conventions

- **Buttons that navigate** use `linked_button(label, href)`. A `dmc.Button` has no `href` and must not sit inside a link, so the `navigate` callback moves the page.
- **Redirects from a callback** return `goto("/somewhere")` to the `redirect` store. A callback cannot write `url.pathname` to send `render_page` somewhere else, because Dash drops that as a circular chain.
- **Callbacks triggered by a button** start with `if not _clicked(): return no_update`. Dash also runs a callback when its inputs first appear on the page.
