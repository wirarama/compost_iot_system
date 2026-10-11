# CompostIQ API

FastAPI, with raw SQL through SQLAlchemy Core, on PostgreSQL. It is the only part of CompostIQ that touches the database.

| | |
|---|---|
| Production | `https://api.compostiq.win` → uvicorn on `127.0.0.1:8000` ([PM2](../deploy/pm2/README.md), [NGINX](../deploy/nginx/README.md)) |
| Interactive docs | `/docs` |

## Run it locally

```bash
docker compose up -d                 # from the repo root: Postgres 16 with schema, migrations and dev seed
uv sync --all-packages
cp backendAPI/.env.example backendAPI/.env   # then set JWT_SECRET
cd backendAPI
uv run uvicorn main:app --reload --port 8000
```

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | the example matches `compose.yaml` |
| `JWT_SECRET` | yes | at least 32 characters; the API will not start without it |
| `JWT_EXPIRY_HOURS` | no | defaults to 8 |
| `PAIRING_CODE_MINUTES` | no | how long a pairing code stays valid; defaults to 10 |

`.env` is read from this folder, whatever directory the API is started from.

**Development sign-in.** The dev seed creates `dev@compostiq.local` with the password `compostiq-dev`. It exists only in the local Compose database (`database/seed_dev.sql`) and must never be loaded on the VM.

## Layout

| File | What it holds |
|---|---|
| `main.py` | the app, `POST /records` and the device `x-key` authentication |
| `routers/auth.py` | user accounts and the `current_user` dependency |
| `routers/pairing.py` | pairing codes: issue, check, and redeem one for a device key |
| `routers/devices.py` | the user's devices: register without pairing, list, set up (name + bin), new key, unpair |
| `routers/bins.py` | the user's bins: list, create |
| `database.py` | the one SQLAlchemy engine everything shares |
| `config.py` | settings, from the environment or `.env` |

## Two kinds of authentication

| Who | Sends | Can reach |
|---|---|---|
| A device | `x-key: <api key>` | `POST /records` only |
| A device, before it has a key | a pairing code | `POST /pairing/redeem` only |
| A user | `Authorization: Bearer <JWT>` | `/auth/*`, `/pairing/codes`, `/devices`, `/bins` (their own only) |

They are separate dependencies. A user token cannot write readings, and a device key cannot reach a user endpoint.

User tokens are JWTs (HS256) carrying `sub` (the user id) and `exp`. They last 8 hours and there are no refresh tokens: the user signs in again. Passwords are stored as argon2id hashes.

## Auth endpoints

| Method | Path | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| POST | `/auth/register` | none | `{email, password, display_name?}` | 201 token | 409 email already used · 422 invalid |
| POST | `/auth/login` | none | `{email, password}` | 200 token | 401 |
| GET | `/auth/me` | Bearer | | 200 user | 401 |
| PATCH | `/auth/me` | Bearer | `{display_name}` | 200 user | 401 · 422 |
| POST | `/auth/change-password` | Bearer | `{current_password, new_password}` | 204 | 401 · 422 |
| DELETE | `/auth/me` | Bearer | `{password}` | 204 | 401 |

A token response is `{access_token, token_type: "bearer", expires_at, user}` and a user is `{id, email, display_name}`.

- Emails are stored in lower case and compared without regard to case.
- Passwords are 8 to 128 characters. Display names are up to 80; a blank one is stored as empty.
- `POST /auth/login` gives the same 401 body for an unknown email and a wrong password, and takes as long for both.
- **Two kinds of 401.** A missing, invalid or expired token is answered with a `WWW-Authenticate: Bearer` header. A wrong password (login, change-password, delete) is a 401 without that header. Clients use the header to tell "sign in again" from "wrong password".
- `DELETE /auth/me` runs in one transaction. The user's bins are deleted, along with their device assignments, readings and events. The user's devices are kept but unpaired (no owner, inactive, assignment closed) with their API keys revoked, so they can be paired again. Their setup codes are removed.
- Requests to `/auth/` are rate-limited per IP by NGINX in production (429). See the [NGINX README](../deploy/nginx/README.md).

## Pairing a device

Pairing (the device gets its key) and setup (the user names it and picks a bin) are separate steps, so a pairing code carries nothing but the account.

1. The dashboard asks for a code: `POST /pairing/codes`.
2. The user types it into the device's own page, and the device calls `POST /pairing/redeem` with the code and its hardware ID (MAC). It gets its API key back, once. The device now belongs to the user but has no bin, so `/records` answers **409** and the device waits.
3. The dashboard polls `GET /pairing/codes/{code}` until it says `redeemed`, and shows which hardware ID used the code. The user checks it matches the device.
4. The user presses **Confirm registration**, and the dashboard calls `POST /devices/{id}/setup` with a name and a bin (creating the bin first with `POST /bins` if it's new). From then on `/records` accepts the device's readings.

If the hardware ID doesn't match, "That's not my device" calls `DELETE /devices/{id}`, which revokes the new key at once. The full sequence diagram, and how the device behaves at each step, is in the [emulator README](../emulator/README.md#pairing).

| Method | Path | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| POST | `/pairing/codes` | Bearer | | 201 `{code, expires_at}` | 401 |
| GET | `/pairing/codes/{code}` | Bearer | | 200 `{code, status, expires_at, device}` | 401 · 404 (not this user's code) |
| POST | `/pairing/redeem` | the code | `{code, device_uid, model?, firmware_version?}` | 201 `{device_id, api_key}` | 404 unknown · 409 already used · 410 expired · 422 · 429 too many wrong codes |
| POST | `/devices` | Bearer | `{name, bin_id}` | 201 `{device, api_key}` | 401 · 404 bin not the user's · 422 |
| GET | `/devices` | Bearer | | 200 list of devices | 401 |
| GET | `/devices/{id}` | Bearer | | 200 device | 401 · 404 |
| POST | `/devices/{id}/setup` | Bearer | `{name, bin_id}` | 200 device | 401 · 404 device or bin not the user's · 422 |
| POST | `/devices/{id}/key` | Bearer | | 201 `{device, api_key}` | 401 · 404 · 409 a paired device |
| DELETE | `/devices/{id}` | Bearer | | 204 | 401 · 404 |
| GET | `/bins` | Bearer | | 200 list of bins | 401 |
| POST | `/bins` | Bearer | `{name, location?, country_code}` | 201 bin | 401 · 422 |

`status` is `pending`, `expired` or `redeemed`; once redeemed, `device` is the device that used it. A device is `{id, hardware_id, registration, name, model, firmware_version, paired_at, last_seen_at, bin: {id, name} | null, set_up}`, where `set_up` means it has a name and a bin, so its readings are accepted. `registration` is `pairing` or `manual` (below); a manual device's `hardware_id` is `null`.

- **Codes** are 6 digits (leading zeros kept), valid for `PAIRING_CODE_MINUTES`, and used once. Only live codes have to be unique, so a number can come round again later. A user can hold up to 5 live codes; asking for more drops the oldest.
- **Guessing is limited.** Every wrong, used or expired code sent to `/pairing/redeem` is counted per client IP in `pairing_failures`. After 10 in 15 minutes that IP gets 429 until the oldest fall out of the window. The count lives in the database because PM2 runs two API workers. NGINX also rate-limits `/pairing/` in production.
- **Keys** are 64 hex characters from `secrets`. Only their SHA-256 hash is stored, as `/records` expects, and the key is only ever in the redeem (or register) response.
- **Pairing hardware again** (after a factory reset, or passing the device on) reuses its `devices` row. Its old keys are revoked, and it leaves its bin and loses its name, so the new owner sets it up from scratch.
- **Someone else's device or bin** is always a 404, never a 403.
- `/records` also rejects a key whose device is inactive (unpaired), and records `last_seen_at` on every call, including the 409s before setup, so the dashboard can see a device is in contact.

## Registering a device without pairing

For hardware that can't run the pairing flow. `POST /devices` with a name and one of the user's bins creates the device, puts it in the bin and returns `{device, api_key}`. The key is in that response only; the dashboard shows it once for the user to copy onto the device, and never stores it. There is no code and no confirm step, and nothing is learnt from the hardware, so the device has no hardware ID (`devices.mac` is null, migration 004) and its readings are accepted straight away.

A lost or leaked key is replaced with `POST /devices/{id}/key`: the old key stops working at once and the new one is returned, once, the same way. The dashboard offers this as **New API key** on the device's settings tab.

**Only registered devices can get a new key.** A paired device has to receive its key itself, so it gets a new one by being paired again, which revokes the old one too. This is enforced in three places:

- **The database.** `devices.registration` (`pairing` or `manual`, migration 004) is set when the device is created and nothing changes it. Two checks keep it `manual` exactly when the device has no MAC, so a paired device can't be marked as registered by any code path.
- **The API.** `POST /devices/{id}/key` reads that column, with the device row locked, and answers 409 for anything but `manual`, without revoking or adding a key. `PATCH /devices/{id}` ignores a `registration` field.
- **The dashboard.** It only shows the button for registered devices, refuses to open the dialog for a paired one, and shows the API's 409 as a plain message.

Everything else is shared with paired devices: `DELETE /devices/{id}` revokes the key, and if the device's bin is deleted it goes back to "not set up" (409 on `/records`) until `POST /devices/{id}/setup` gives it another bin. It keeps its key throughout.

## Tests

```bash
cd backendAPI && uv run pytest
```

These are integration tests against the Compose database; if it is not running they are skipped with a hint. The auth tests register their own users (`pytest-…@example.com`) and remove everything they create. The pairing tests (`tests/test_pairing.py`) cover every step above, including expired, reused and guessed codes, another user's device or bin, re-pairing and unpairing, and remove their devices (`02:00:00:00:FD:…`) afterwards. The registration tests (`tests/test_registration.py`) cover registering, the key working at once, another user's bin, unpairing, setting a device up again after its bin is deleted, replacing a key, and the paired-device rules above (409 with nothing revoked, `registration` can't be changed through the API, the database checks reject a mismatched row), and remove the devices they register.
