"""
Environment settings for the dashboard.

Locally they come from monitoringSystem/.env (see .env.example); on the VM,
PM2 passes them in from the repo-root .env. Variables that are already set
win over the file.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000").rstrip("/")
# where devices send their readings, shown with a registered device's API key.
# Not API_URL: on the VM that is the loopback address only the dashboard uses
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "https://api.compostiq.win").rstrip("/")
# the public website (website/public on compostiq.win, served by NGINX):
# the dashboard's own "/" sends signed-out visitors there
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://compostiq.win").rstrip("/")
# signs the session cookie; app.py decides what a missing key means
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY")
# how long the session cookie lasts - matches the API's 8-hour token
SESSION_HOURS = 8
