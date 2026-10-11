"""POST /records - device-key authentication and batch ingest."""
from sqlalchemy import text

from conftest import SEED_BIN_ID, SEED_DEVICE_ID, TEST_CUTOFF, reading
from main import db_engine


def _test_rows():
    with db_engine.connect() as db:
        return db.execute(
            text(
                "SELECT bin_id::text, temperature FROM records "
                "WHERE device_id = :d AND timestamp < :cutoff ORDER BY timestamp"
            ),
            {"d": SEED_DEVICE_ID, "cutoff": TEST_CUTOFF},
        ).all()


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


def test_records_without_key_is_rejected(client):
    response = client.post("/records", json=[reading()])
    assert response.status_code in (401, 403)


def test_records_with_unknown_key_is_rejected(client):
    response = client.post("/records", json=[reading()], headers={"x-key": "not-a-real-key"})
    assert response.status_code == 401


def test_records_with_revoked_key_is_rejected(client, device_key):
    with db_engine.begin() as db:
        db.execute(text("UPDATE device_apikeys SET revoked_at = now() WHERE device_id = :d"), {"d": SEED_DEVICE_ID})
    response = client.post("/records", json=[reading()], headers={"x-key": device_key})
    assert response.status_code == 401


def test_records_batch_is_stored_against_the_assigned_bin(client, device_key):
    batch = [
        reading(timestamp="2000-01-01T00:00:00+00:00", temperature=50.0),
        reading(timestamp="2000-01-01T00:30:00+00:00", temperature=51.5),
    ]
    response = client.post("/records", json=batch, headers={"x-key": device_key})

    assert response.status_code == 200
    assert len(response.json()) == 2
    rows = _test_rows()
    assert [(row.bin_id, row.temperature) for row in rows] == [(SEED_BIN_ID, 50.0), (SEED_BIN_ID, 51.5)]


def test_records_from_device_without_a_bin_is_a_conflict(client, unassigned_device_key):
    response = client.post("/records", json=[reading()], headers={"x-key": unassigned_device_key})
    assert response.status_code == 409


def test_records_with_missing_field_is_a_validation_error(client, device_key):
    bad = reading()
    del bad["temperature"]
    response = client.post("/records", json=[bad], headers={"x-key": device_key})
    assert response.status_code == 422
    assert _test_rows() == []
