# CompostIQ

**An IoT system that monitors compost bins and tells people what to do next.**

Sensors inside a compost bin report temperature, moisture and gas levels to the cloud. A web dashboard turns those readings into something useful: which phase the compost is in, whether it is healthy, and when to turn it, water it or leave it alone.

CompostIQ is an RMIT capstone project, built around a pilot site in Mataram, Indonesia.

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [The systems](#the-systems)
- [How it works](#how-it-works)
- [Security model](#security-model)
- [Data model](#data-model)
- [The composting cycle](#the-composting-cycle)
- [Project status](#project-status)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)

## What it does

| For | CompostIQ provides |
|---|---|
| **People who compost** | A dashboard for their own bins and devices: live readings, history, and alerts that say what action to take |
| **The public** | An open landing page. Anonymised, country-level statistics are planned |
| **Researchers and developers** | A simulator that produces realistic composting cycles, so the whole system can be built and tested without a physical bin |

## Architecture

CompostIQ is four systems with one rule between them: **only the API touches the database.** The dashboard and the devices both go through it.

```mermaid
flowchart TB
    person(["Visitor or signed-in user"])
    device["Compost device<br/>sensors in the bin"]
    sim["Simulator<br/>stands in for a device"]

    subgraph vm["Cloud VM"]
        nginx["NGINX<br/>HTTPS and rate limiting"]
        dash["Dashboard<br/>Dash + Mantine"]
        api["API<br/>FastAPI"]
        db[("PostgreSQL")]
    end

    person -->|browser| nginx
    device -->|readings| nginx
    sim -->|readings| nginx
    nginx -->|dashboard.compostiq.win| dash
    nginx -->|api.compostiq.win| api
    dash -->|"calls made by the server"| api
    api -->|SQL| db

    classDef client fill:#e7f5ff,stroke:#1c7ed6,color:#0b2a4a
    classDef app fill:#ebfbee,stroke:#2f9e44,color:#0b3d17
    classDef store fill:#fff4e6,stroke:#e8590c,color:#4a1f04
    classDef edge fill:#f1f3f5,stroke:#495057,color:#212529
    class person,device,sim client
    class dash,api app
    class db store
    class nginx edge
```

Three ideas shape this design:

1. **The API is the single gatekeeper.** Every rule about who may see or change what is enforced in one place, next to the data.
2. **The browser never talks to the API.** The dashboard's server makes the calls, so a user's credentials never reach browser JavaScript.
3. **The simulator is just another device.** It uses the same endpoint and the same kind of key as real hardware, so nothing in the cloud is simulator-specific.

## The systems

| System | What it does | Built with | Folder |
|---|---|---|---|
| **API** | Receives readings from devices, manages user accounts, and is the only path to the database | FastAPI, SQLAlchemy Core, PyJWT, argon2 | [`backendAPI/`](backendAPI/README.md) |
| **Dashboard** | The website. Public landing page, sign-up and sign-in, and each user's bins, devices and charts | Dash, dash-mantine-components, Plotly | [`monitoringSystem/`](monitoringSystem/README.md) |
| **Simulator** | Generates a full composting cycle of sensor readings and sends it to the API as a device would. Runs on a developer's own machine | Dash, pandas, Plotly | [`simulator/`](simulator/README.md) |
| **Database** | Stores users, bins, devices, readings and alerts. Schema changes are versioned as migrations | PostgreSQL 16 | [`database/`](database/README.md) |
| **Deployment** | Runs the API and dashboard on one VM behind HTTPS | NGINX, PM2, Let's Encrypt | [`deploy/`](deploy/pm2/README.md) |

## How it works

### A reading's journey

A device collects readings and sends them in batches. The API works out which bin the device is assigned to and stores each reading against it.

```mermaid
sequenceDiagram
    participant D as Device or simulator
    participant A as API
    participant P as PostgreSQL

    D->>A: POST /records (batch of readings + device key)
    A->>P: Is this key valid and not revoked?
    P-->>A: Yes, it belongs to device X
    A->>P: Which bin is device X assigned to?
    P-->>A: Bin Y
    A->>P: Store the readings against bin Y
    A-->>D: 200 OK
```

Each reading holds a timestamp, temperature, moisture, oxygen, carbon dioxide and a relative ammonia level.

### Signing in

Users sign in on the dashboard. The API issues a token, and the dashboard keeps it in a signed cookie that browser scripts cannot read.

```mermaid
sequenceDiagram
    actor U as User
    participant D as Dashboard
    participant A as API
    participant P as PostgreSQL

    U->>D: Email and password
    D->>A: POST /auth/login
    A->>P: Find the user, check the password hash
    A-->>D: Token, valid for 8 hours
    D-->>U: Signed session cookie

    Note over U,P: Every page after that
    U->>D: Open a private page (cookie attached)
    D->>A: Request with the user's token
    A->>P: Fetch only this user's data
    A-->>D: Data
    D-->>U: Page
```

When the token expires the user is asked to sign in again, then returned to the page they were on.

## Security model

There are two kinds of caller, with separate credentials that cannot stand in for each other.

| Caller | Credential | Allowed to |
|---|---|---|
| **Device** | An API key, stored only as a hash | Send readings. Nothing else |
| **User** | A token (JWT) issued at sign-in, valid for 8 hours | Manage their own account, bins and devices |

- Passwords are stored as argon2 hashes.
- Ownership is checked in the database query itself, so a user can only ever reach their own data.
- The dashboard holds no database credentials. In production it is not even given the database address.
- Public pages are designed to show totals only, at country level: no names, locations or device identifiers.

## Data model

A user owns bins and devices. A device is assigned to a bin, and its readings are stored against that bin.

```mermaid
erDiagram
    USERS ||--o{ BINS : owns
    USERS ||--o{ DEVICES : owns
    BINS ||--o{ DEVICE_BIN_ASSN : hosts
    DEVICES ||--o{ DEVICE_BIN_ASSN : "is placed by"
    DEVICES ||--o{ DEVICE_APIKEYS : "signs in with"
    BINS ||--o{ RECORDS : collects
    BINS ||--o{ BIN_EVENTS : raises
```

| Table | Holds |
|---|---|
| `users` | Accounts |
| `bins` | Compost bins, each owned by one user |
| `devices` | Sensor devices, each owned by one user once paired |
| `device_bin_assn` | Which device is in which bin, and when |
| `device_apikeys` | Hashed device keys, which can be revoked |
| `records` | Sensor readings. Each also remembers which device sent it |
| `bin_events` | Alerts raised for a bin |

Two smaller tables are left off the diagram: `setup_codes` (pairing codes) and `device_maintenance`.

- **Assignments keep their history.** Moving a device closes one assignment and opens another, so old readings stay with the bin they were taken in.
- **Deleting an account** removes its bins and readings, and releases its devices to be paired again.

## The composting cycle

Compost passes through five stages, each with its own expected temperature. CompostIQ uses these ranges to work out which stage a bin is in and whether its readings are healthy. The simulator uses the same ranges to generate data.

```mermaid
flowchart LR
    s0["Early mesophilic<br/>28 to 45 °C<br/>about 2 days"]
    s1["Active thermophilic<br/>45 to 60 °C<br/>about 3 days"]
    s2["Peak decomposition<br/>57 to 65 °C<br/>about 5 days"]
    s3["Cooling<br/>60 down to 30 °C<br/>about 8 days"]
    s4["Maturation<br/>28 to 32 °C<br/>about 30 days"]
    s0 --> s1 --> s2 --> s3 --> s4

    classDef warm fill:#fff9db,stroke:#f08c00,color:#4a3300
    classDef hot fill:#ffe8cc,stroke:#e8590c,color:#4a1f04
    classDef peak fill:#ffe3e3,stroke:#e03131,color:#4a0b0b
    classDef mature fill:#ebfbee,stroke:#2f9e44,color:#0b3d17
    class s0,s3 warm
    class s1 hot
    class s2 peak
    class s4 mature
```

One milestone matters most: holding **55 °C or above for three days in a row** kills pathogens, which is the EPA Class A sanitation target.

The ranges and their sources are in [`simulator/composting_stages.py`](simulator/composting_stages.py).

## Project status

The project is in active development. This is what exists today.

| Area | Status |
|---|---|
| Devices sending readings to the API | ✅ Working |
| Simulator generating cycles and uploading them | ✅ Working, using a manually issued device key |
| User accounts: sign-up, sign-in, account page | ✅ Working |
| Dashboard pages for bins, devices and charts | 🟡 Interface built, showing placeholder data |
| Deployment to the VM | 🟡 Configured; sign-in release not yet deployed |
| API for a user's bins, devices and readings | ⬜ Designed |
| Pairing a device with a 6-digit code | ⬜ Designed |
| Alerts and health scoring | ⬜ Designed |
| Public statistics and country map | ⬜ Designed |

The agreed design for everything marked "Designed" is in [`docs/storyboard.md`](docs/storyboard.md).

## Getting started

You need [Docker](https://docs.docker.com/get-docker/) and [uv](https://docs.astral.sh/uv/). Run each command from the repository root.

> **Docker is only used for the local database.** It gives development and testing a PostgreSQL to run against. The API, dashboard and simulator run directly with Python, and the production VM does not use Docker.

**1. Start the database.** The first start loads the schema, the migrations and a development user.

```bash
docker compose up -d
```

**2. Install the dependencies.**

```bash
uv sync --all-packages
```

**3. Create the local settings files**, then set the secrets inside them. Each file explains how.

```bash
cp backendAPI/.env.example backendAPI/.env
```

```bash
cp monitoringSystem/.env.example monitoringSystem/.env
```

**4. Start the API**, in its own terminal.

```bash
uv run --directory backendAPI uvicorn main:app --port 8000
```

**5. Start the dashboard**, in another terminal.

```bash
uv run python monitoringSystem/app.py
```

Open <http://127.0.0.1:8050> and create an account.

To check that everything is wired up, run the API's tests:

```bash
uv run --directory backendAPI pytest
```

The simulator is optional and also uses port 8050, so stop the dashboard first or see its [README](simulator/README.md).

## Repository layout

```text
compost_iot_system/
├── backendAPI/          The API, and its tests
├── monitoringSystem/    The dashboard
├── simulator/           The device simulator
├── database/            Schema, migrations and the development seed
├── deploy/              NGINX and PM2 configuration for the VM
├── docs/                Storyboards and design decisions
├── mock-data/           Sample readings and the schema they follow
├── Archive/             The original prototype, kept for reference
└── compose.yaml         The local development database
```

## Documentation

| To learn about | Read |
|---|---|
| Endpoints, authentication and running the API | [`backendAPI/README.md`](backendAPI/README.md) |
| Pages, sign-in and the structure of the dashboard | [`monitoringSystem/README.md`](monitoringSystem/README.md) |
| Generating and inspecting composting cycles | [`simulator/README.md`](simulator/README.md) |
| The schema, and how to apply a migration safely | [`database/README.md`](database/README.md) |
| Setting up the VM | [`deploy/pm2/README.md`](deploy/pm2/README.md), [`deploy/nginx/README.md`](deploy/nginx/README.md) |
| User flows, page inventory and design decisions | [`docs/storyboard.md`](docs/storyboard.md) |
