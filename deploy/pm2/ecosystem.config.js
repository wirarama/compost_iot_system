// CompostIQ - PM2 process file
// Start:   pm2 start deploy/pm2/ecosystem.config.js
// Both apps bind to 127.0.0.1 only; NGINX is the public entry point.
// PostgreSQL is NOT managed here - it runs as a normal systemd service.

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const VENV_BIN = path.join(ROOT, ".venv/bin");

// Load secrets (DATABASE_URL etc.) from the repo-root .env, never from this file.
// Values are read once, at `pm2 start` / `pm2 reload --update-env`.
function loadEnv(file) {
  if (!fs.existsSync(file)) return {};
  return Object.fromEntries(
    fs.readFileSync(file, "utf8")
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#") && l.includes("="))
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i).trim(), l.slice(i + 1).trim().replace(/^["']|["']$/g, "")];
      })
  );
}
const env = loadEnv(path.join(ROOT, ".env"));

// Each app only gets the variables it needs, so the dashboard never sees the
// database password. Add a key here when an app starts reading a new one.
function pick(keys) {
  return Object.fromEntries(keys.filter((k) => k in env).map((k) => [k, env[k]]));
}

module.exports = {
  apps: [
    {
      // The monitoring dashboard (Dash + dash-mantine-components). The
      // simulator is NOT hosted here - it runs on the user's own machine as a
      // self-hosted device.
      name: "compostiq-dashboard",
      cwd: path.join(ROOT, "monitoringSystem"),
      script: path.join(VENV_BIN, "gunicorn"),
      args: "app:server --bind 127.0.0.1:8050 --workers 2 --timeout 60",
      interpreter: "none",
      env: pick(["API_URL", "DASHBOARD_SECRET_KEY", "PUBLIC_API_URL", "WEBSITE_URL"]),
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      max_memory_restart: "500M",
    },
    {
      // FastAPI app at backendAPI/main.py exposing `app`.
      name: "compostiq-api",
      cwd: path.join(ROOT, "backendAPI"),
      script: path.join(VENV_BIN, "uvicorn"),
      args: "main:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers",
      interpreter: "none",
      env: pick(["DATABASE_URL", "JWT_SECRET", "JWT_EXPIRY_HOURS", "PAIRING_CODE_MINUTES"]),
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      max_memory_restart: "500M",
    },
  ],
};
