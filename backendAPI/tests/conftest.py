"""
Shared fixtures for the API tests.

These are integration tests: they run the real FastAPI app against the local
PostgreSQL from compose.yaml (`docker compose up -d`), seeded by
database/seed_dev.sql. If the database is not reachable every test is skipped
with a hint, rather than failing.
"""
import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from main import app, db_engine

# fixed ids from database/seed_dev.sql
SEED_DEVICE_ID = "00000000-0000-4000-8000-000000000201"
SEED_BIN_ID = "00000000-0000-4000-8000-000000000101"
SEED_USER_ID = "00000000-0000-4000-8000-000000000001"

# readings written by tests use timestamps in the year 2000, so teardown can
# delete exactly what the tests created and nothing else
TEST_EPOCH = "2000-01-01T00:00:00+00:00"
TEST_CUTOFF = "2001-01-01T00:00:00+00:00"

# users and devices created by the auth tests are recognisable by these, so
# teardown can remove them even when a test fails half-way
TEST_EMAIL_LIKE = "pytest-%@example.com"
TEST_MAC_LIKE = "02:00:00:00:fe:%"
TEST_PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def database_available():
    try:
        with db_engine.connect() as db:
            db.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("local database not reachable - run `docker compose up -d` from the repo root")


@pytest.fixture
def client():
    return TestClient(app)


def _insert_key(device_id):
    key = secrets.token_hex(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with db_engine.begin() as db:
        key_id = db.execute(
            text("INSERT INTO device_apikeys (device_id, api_key_hash) VALUES (:d, :h) RETURNING id"),
            {"d": device_id, "h": key_hash},
        ).scalar_one()
    return key, key_id


@pytest.fixture
def device_key():
    """A fresh API key for the seeded device; removed again afterwards."""
    key, key_id = _insert_key(SEED_DEVICE_ID)
    yield key
    with db_engine.begin() as db:
        db.execute(
            text("DELETE FROM records WHERE device_id = :d AND timestamp < :cutoff"),
            {"d": SEED_DEVICE_ID, "cutoff": TEST_CUTOFF},
        )
        db.execute(text("DELETE FROM device_apikeys WHERE id = :id"), {"id": key_id})


@pytest.fixture
def unassigned_device_key():
    """A key for a throwaway device that is not assigned to any bin."""
    with db_engine.begin() as db:
        device_id = db.execute(
            text("INSERT INTO devices (mac, owner_id) VALUES ('02:00:00:00:ff:ff', :u) RETURNING id"),
            {"u": SEED_USER_ID},
        ).scalar_one()
    key, _ = _insert_key(device_id)
    yield key
    with db_engine.begin() as db:
        db.execute(text("DELETE FROM device_apikeys WHERE device_id = :d"), {"d": device_id})
        db.execute(text("DELETE FROM devices WHERE id = :d"), {"d": device_id})


def reading(**overrides):
    base = {
        "timestamp": TEST_EPOCH,
        "temperature": 55.0,
        "moisture_percent": 52.0,
        "o2_percent": 6.5,
        "co2_percent": 4.2,
        "nh3_ratio": 0.4,
    }
    return {**base, **overrides}


@pytest.fixture
def new_email():
    """Returns a function that makes a fresh, unused test email address."""
    def make():
        return f"pytest-{secrets.token_hex(6)}@example.com"
    yield make
    with db_engine.begin() as db:
        # bins, and everything under them, go with the user (migration 002)
        db.execute(text("DELETE FROM users WHERE email LIKE :like"), {"like": TEST_EMAIL_LIKE})
        test_devices = "SELECT id FROM devices WHERE mac LIKE :like"
        for table in ("device_apikeys", "device_bin_assn", "records", "setup_codes"):
            db.execute(text(f"DELETE FROM {table} WHERE device_id IN ({test_devices})"), {"like": TEST_MAC_LIKE})
        db.execute(text("DELETE FROM devices WHERE mac LIKE :like"), {"like": TEST_MAC_LIKE})


@pytest.fixture
def account(client, new_email):
    """A registered user: {"email", "password", "user", "headers"}."""
    email = new_email()
    response = client.post(
        "/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "display_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "email": email,
        "password": TEST_PASSWORD,
        "user": body["user"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
    }
