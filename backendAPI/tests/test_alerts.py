"""Alert incidents from POST /records, and GET /bins/{id}/history day boundaries."""
import datetime
import secrets
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from conftest import _insert_key, reading
from main import db_engine

HOT = 75.0   # above alerts.MAX_TEMPERATURE
FINE = 55.0


@pytest.fixture
def bin_device(client, account):
    """A fresh bin with one set-up device in it, owned by a test account.

    The account's teardown removes the bin (and its records and incidents)
    and the device, which uses the test MAC prefix.
    """
    bin_id = client.post(
        "/bins", headers=account["headers"], json={"name": "Alert test", "country_code": "ID"}
    ).json()["id"]
    with db_engine.begin() as db:
        device_id = db.execute(
            text("INSERT INTO devices (mac, owner_id) VALUES (:mac, :owner) RETURNING id"),
            {"mac": f"02:00:00:00:fe:{secrets.token_hex(1)}", "owner": account["user"]["id"]},
        ).scalar_one()
        db.execute(
            text("INSERT INTO device_bin_assn (device_id, bin_id) VALUES (:d, :b)"),
            {"d": device_id, "b": bin_id},
        )
    key, _ = _insert_key(device_id)
    return {"bin_id": bin_id, "x_key": {"x-key": key}, "headers": account["headers"]}


def _upload(client, bin_device, *readings):
    response = client.post("/records", json=list(readings), headers=bin_device["x_key"])
    assert response.status_code == 200, response.text


def _instant(value):
    return datetime.datetime.fromisoformat(value)


def _too_hot(client, bin_device):
    response = client.get(
        "/alerts",
        params={"bin_id": bin_device["bin_id"], "include_resolved": True},
        headers=bin_device["headers"],
    )
    assert response.status_code == 200, response.text
    return [alert for alert in response.json() if alert["type"] == "too_hot"]


def test_a_newer_reading_resolves_the_incident(client, bin_device):
    _upload(client, bin_device, reading(timestamp="2000-01-01T10:00:00+00:00", temperature=HOT))
    _upload(client, bin_device, reading(timestamp="2000-01-01T11:00:00+00:00", temperature=FINE))

    [incident] = _too_hot(client, bin_device)
    # compared as instants: the API may answer in the database's timezone
    assert _instant(incident["triggered_at"]) == _instant("2000-01-01T10:00:00+00:00")
    assert _instant(incident["resolved_at"]) == _instant("2000-01-01T11:00:00+00:00")


def test_a_late_older_reading_does_not_resolve_a_newer_incident(client, bin_device):
    _upload(client, bin_device, reading(timestamp="2000-01-01T10:00:00+00:00", temperature=HOT))
    # a backlog from before the incident, uploaded afterwards
    _upload(client, bin_device, reading(timestamp="2000-01-01T09:00:00+00:00", temperature=FINE))

    [incident] = _too_hot(client, bin_device)
    assert incident["resolved_at"] is None


def test_a_resent_batch_does_not_reopen_a_resolved_incident(client, bin_device):
    hot = reading(timestamp="2000-01-01T10:00:00+00:00", temperature=HOT)
    _upload(client, bin_device, hot)
    _upload(client, bin_device, reading(timestamp="2000-01-01T11:00:00+00:00", temperature=FINE))
    # a device retrying an upload it thinks failed
    _upload(client, bin_device, hot)

    [incident] = _too_hot(client, bin_device)
    assert incident["resolved_at"] is not None


def test_history_days_start_at_midnight_lombok_time(client, bin_device):
    lombok = ZoneInfo("Asia/Makassar")
    day = datetime.datetime.now(lombok).date() - datetime.timedelta(days=2)
    midnight = datetime.datetime.combine(day, datetime.time(), tzinfo=lombok)
    before = midnight - datetime.timedelta(minutes=30)   # 23:30 the day before
    after = midnight + datetime.timedelta(minutes=30)    # 00:30 on `day`
    _upload(
        client,
        bin_device,
        reading(timestamp=before.isoformat(), temperature=40.0),
        reading(timestamp=after.isoformat(), temperature=50.0),
    )

    response = client.get(
        f"/bins/{bin_device['bin_id']}/history", params={"days": 7}, headers=bin_device["headers"]
    )
    assert response.status_code == 200, response.text
    by_day = {row["day"]: row["max_temp"] for row in response.json()}
    assert by_day == {
        (day - datetime.timedelta(days=1)).isoformat(): 40.0,
        day.isoformat(): 50.0,
    }
