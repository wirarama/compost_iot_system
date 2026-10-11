# PM2 setup (Ubuntu VM)

PM2 runs the two app processes and restarts them on crash or reboot.
PostgreSQL runs as its own systemd service and is only reachable from the VM itself.

| Component | Managed by | Listens on | Public? |
|---|---|---|---|
| Dashboard (`monitoringSystem/`, Dash + Mantine, via gunicorn) | PM2 `compostiq-dashboard` | `127.0.0.1:8050` | Yes, at `https://dashboard.compostiq.win` |
| API (`backendAPI/`, FastAPI via uvicorn) | PM2 `compostiq-api` | `127.0.0.1:8000` | Yes, at `https://api.compostiq.win` |
| PostgreSQL | systemd `postgresql` | `127.0.0.1:5432` | No |

The simulator is **not** hosted on the VM. It is a self-hosted device that people run on their own machine, and it talks to the API like a real compost sensor would.

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y postgresql nodejs npm
sudo npm install -g pm2
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. Database (systemd, not PM2)

```bash
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE USER compostiq WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE compostiq OWNER compostiq;"
sudo -u postgres psql -d compostiq -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
psql "postgresql://compostiq:CHANGE_ME@127.0.0.1:5432/compostiq" -f database/CompostIQ_PostgreSQL_schema_fix1.sql
```

Then apply the migrations in `database/migrations/`, in name order. They are safe to run again, so after a `git pull` that brings a new one, run them all. On a database that already holds data, follow the full playbook in [`database/README.md`](../../database/README.md) instead: it adds the backup, the checks and the rollback.

```bash
for f in database/migrations/*.sql; do
  psql "postgresql://compostiq:CHANGE_ME@127.0.0.1:5432/compostiq" -v ON_ERROR_STOP=1 -f "$f"
done
```

| Migration | What it adds |
|---|---|
| `002_users_auth.sql` | `users.created_at` and `users.display_name`, case-insensitive unique emails, and the `ON DELETE` rules that account deletion relies on |

Do **not** load `database/seed_dev.sql` on the VM. It creates a user with a published, development-only password.

Ubuntu's PostgreSQL only listens on localhost by default. Keep it that way, and do not open port 5432 in the firewall.

## 3. App code and Python environment

```bash
git clone <repo-url> ~/compost_iot_system
cd ~/compost_iot_system
uv sync --all-packages  # --all-packages also installs the API's own dependencies (backendAPI/pyproject.toml)
cp .env.example .env    # then edit .env: see the table below
chmod 600 .env
```

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | API | the real database password |
| `JWT_SECRET` | API | signs sign-in tokens. Required, at least 32 characters: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it signs everyone out |
| `JWT_EXPIRY_HOURS` | API | optional, defaults to 8 |
| `API_URL` | Dashboard | `http://127.0.0.1:8000`. Keep it on the loopback address, so the dashboard's calls skip NGINX and its rate limit |
| `PUBLIC_API_URL` | Dashboard | optional, defaults to `https://api.compostiq.win`. Shown to users with a registered device's API key, as where the device sends readings |
| `DASHBOARD_SECRET_KEY` | Dashboard | signs the session cookie. Required: the dashboard refuses to start without it. Use a different value from `JWT_SECRET` |

`ecosystem.config.js` reads `.env` from the repo root and gives each app only the variables it needs. The dashboard never receives `DATABASE_URL`. When an app starts reading a new variable, add its name to that app's `pick([...])` list.

## 4. Start the apps

```bash
pm2 start deploy/pm2/ecosystem.config.js
pm2 status
```

## 5. Survive reboots

```bash
pm2 startup systemd     # prints a sudo command - run it
pm2 save                # remembers the current process list
```

## 6. Log rotation

```bash
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
```

## Everyday commands

```bash
pm2 status
pm2 logs compostiq-api
pm2 restart compostiq-dashboard
pm2 reload deploy/pm2/ecosystem.config.js --update-env   # after editing .env
```

To deploy new code, run `git pull` and `uv sync --all-packages`, apply any new migrations (section 2), then restart both apps.

## Moving an existing VM off the simulator (one-off)

Before this change, `compostiq-dashboard` ran the **simulator** from `simulator/`. Anyone could reach it without signing in, and it held a real device API key. PM2 doesn't reliably pick up a changed working directory on `reload`, so recreate the process:

```bash
cd ~/compost_iot_system
git pull && uv sync --all-packages
pm2 delete compostiq-dashboard
pm2 start deploy/pm2/ecosystem.config.js --only compostiq-dashboard
pm2 reload deploy/pm2/ecosystem.config.js --only compostiq-api --update-env   # API now gets only its own env vars
pm2 save
curl -sI http://127.0.0.1:8050/ | head -1   # expect HTTP/1.1 200 OK
```

Then clean up what the simulator left behind:

```bash
rm -f simulator/.env      # held the simulator's device API key
rm -rf simulator/Data     # generated runs, not needed on the VM
```

That key was reachable by anyone while the simulator was public, so revoke it in the database. Anyone running the simulator locally will then need a new key, until device pairing replaces manual keys.

```sql
UPDATE device_apikeys SET revoked_at = now()
WHERE revoked_at IS NULL AND device_id = '<the simulator device id>';
```

## Notes

- **API routes.** The API has its own subdomain, so its routes need no `/api` prefix.
- **Dashboard workers.** The dashboard keeps no state on disk or in memory between requests, so two gunicorn workers are safe. Once sign-in lands, sessions live in a signed cookie, which also works across workers.
