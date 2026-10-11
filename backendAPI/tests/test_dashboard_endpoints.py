"""The endpoints the dashboard reads: bin detail/edit/delete, readings, daily
history, device settings and alerts.

Every test works in throwaway accounts (the `account` fixture), so its bins,
readings and incidents go with the account at teardown. Devices are hardware
and outlive accounts, so the ones made here are recognised by their MAC
prefix and removed by `clean_devices`.
"""
import datetime
import secrets

import pytest
from sqlalchemy import text

from conftest import TEST_PASSWORD, reading
from main import db_engine
from routers.alerts import MAX_TEMPERATURE, OFFLINE_AFTER

# upper case, as the API stores hardware IDs; test_pairing.py uses ...:FD:
TEST_MAC_PREFIX = "02:00:00:00:FC:"
RANDOM_ID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(autouse=True)
def clean_devices():
    def clean():
        devices = "SELECT id FROM devices WHERE mac LIKE :like"
        like = {"like": TEST_MAC_PREFIX + "%"}
        with db_engine.begin() as db:
            for table in ("device_apikeys", "device_bin_assn", "records", "setup_codes"):
                db.execute(text(f"DELETE FROM {table} WHERE device_id IN ({devices})"), like)
            db.execute(text("DELETE FROM devices WHERE mac LIKE :like"), like)
    clean()
    yield
    clean()


@pytest.fixture
def other_account(client, new_email):
    response = client.post("/auth/register", json={"email": new_email(), "password": TEST_PASSWORD})
    return {"headers": {"Authorization": f"Bearer {response.json()['access_token']}"}}


# ----------------------------------------------------------------- helpers ---

def _at(value):
    return datetime.datetime.fromisoformat(value)


def _ago(**delta):
    """An ISO timestamp this long before now, e.g. _ago(hours=2)."""
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(**delta)).isoformat()


def _mac():
    return f"{TEST_MAC_PREFIX}{secrets.randbelow(256):02X}"


def _bin(client, headers, name="Dashboard test bin"):
    response = client.post("/bins", headers=headers, json={"name": name, "location": "Mataram", "country_code": "ID"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _device(client, headers, bin_id, mac=None, name="Test sensor"):
    """A paired, set-up device in bin_id: (device_id, api_key)."""
    code = client.post("/pairing/codes", headers=headers).json()["code"]
    redeemed = client.post("/pairing/redeem", json={"code": code, "device_uid": mac or _mac()})
    assert redeemed.status_code == 201, redeemed.text
    device_id, api_key = redeemed.json()["device_id"], redeemed.json()["api_key"]
    setup = client.post(f"/devices/{device_id}/setup", headers=headers, json={"name": name, "bin_id": bin_id})
    assert setup.status_code == 200, setup.text
    return device_id, api_key


def _send(client, api_key, *readings):
    response = client.post("/records", json=list(readings), headers={"x-key": api_key})
    assert response.status_code == 200, response.text


def _incidents(bin_id):
    with db_engine.connect() as db:
        return db.execute(
            text("SELECT type, severity, triggered_at, resolved_at FROM bin_events WHERE bin_id = :b ORDER BY triggered_at, type"),
            {"b": bin_id},
        ).all()


def _assignments(device_id):
    with db_engine.connect() as db:
        return db.execute(
            text("SELECT bin_id::text, unassigned_at FROM device_bin_assn WHERE device_id = :d ORDER BY assigned_at"),
            {"d": device_id},
        ).all()


# ------------------------------------------------------- GET /bins/{bin_id} ---

def test_a_bin_can_be_read_by_its_owner_only(client, account, other_account):
    bin_id = _bin(client, account["headers"])

    response = client.get(f"/bins/{bin_id}", headers=account["headers"])
    assert response.status_code == 200
    assert response.json() == {"id": bin_id, "name": "Dashboard test bin", "location": "Mataram", "country_code": "ID"}

    assert client.get(f"/bins/{bin_id}", headers=other_account["headers"]).status_code == 404
    assert client.get(f"/bins/{RANDOM_ID}", headers=account["headers"]).status_code == 404
    assert client.get(f"/bins/{bin_id}").status_code == 401
    assert client.get("/bins/not-a-uuid", headers=account["headers"]).status_code == 422


# ----------------------------------------------------- PATCH /bins/{bin_id} ---

def test_patching_a_bin_changes_only_what_was_sent(client, account):
    bin_id = _bin(client, account["headers"])

    response = client.patch(f"/bins/{bin_id}", headers=account["headers"], json={"name": "  Renamed  "})
    assert response.status_code == 200
    assert response.json() == {"id": bin_id, "name": "Renamed", "location": "Mataram", "country_code": "ID"}

    response = client.patch(f"/bins/{bin_id}", headers=account["headers"], json={"country_code": "au", "location": "School"})
    assert response.json() == {"id": bin_id, "name": "Renamed", "location": "School", "country_code": "AU"}


def test_patching_a_bin_needs_a_change_and_valid_values(client, account):
    bin_id = _bin(client, account["headers"])
    assert client.patch(f"/bins/{bin_id}", headers=account["headers"], json={}).status_code == 422
    assert client.patch(f"/bins/{bin_id}", headers=account["headers"], json={"name": "   "}).status_code == 422
    assert client.patch(f"/bins/{bin_id}", headers=account["headers"], json={"country_code": "IDN"}).status_code == 422


def test_you_cant_patch_someone_elses_bin(client, account, other_account):
    bin_id = _bin(client, account["headers"])
    response = client.patch(f"/bins/{bin_id}", headers=other_account["headers"], json={"name": "Mine now"})
    assert response.status_code == 404
    assert client.get(f"/bins/{bin_id}", headers=account["headers"]).json()["name"] == "Dashboard test bin"


# ---------------------------------------------------- DELETE /bins/{bin_id} ---

def test_deleting_a_bin_removes_its_data_and_frees_its_device(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    device_id, api_key = _device(client, headers, bin_id)
    _send(client, api_key, reading(timestamp=_ago(minutes=5), temperature=MAX_TEMPERATURE + 5))
    assert len(_incidents(bin_id)) == 1

    response = client.delete(f"/bins/{bin_id}", headers=headers)
    assert response.status_code == 204
    assert response.content == b""

    assert client.get(f"/bins/{bin_id}", headers=headers).status_code == 404
    with db_engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM records WHERE bin_id = :b"), {"b": bin_id}).scalar_one() == 0
    assert _incidents(bin_id) == []

    # the device is kept, back to "needs setup", and its uploads wait
    device = client.get(f"/devices/{device_id}", headers=headers).json()
    assert device["bin"] is None and device["set_up"] is False
    assert client.post("/records", json=[reading(timestamp=_ago(minutes=1))], headers={"x-key": api_key}).status_code == 409


def test_you_cant_delete_someone_elses_bin(client, account, other_account):
    bin_id = _bin(client, account["headers"])
    assert client.delete(f"/bins/{bin_id}", headers=other_account["headers"]).status_code == 404
    assert client.delete(f"/bins/{RANDOM_ID}", headers=account["headers"]).status_code == 404
    assert client.get(f"/bins/{bin_id}", headers=account["headers"]).status_code == 200


# ----------------------------------------------- GET /bins/{bin_id}/records ---

def test_bin_records_are_the_window_oldest_first(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    device_id, api_key = _device(client, headers, bin_id)
    _send(
        client, api_key,
        reading(timestamp=_ago(hours=1), temperature=52.0),
        reading(timestamp=_ago(hours=30), temperature=40.0),   # outside 24 h
        reading(timestamp=_ago(hours=3), temperature=51.0),
    )

    response = client.get(f"/bins/{bin_id}/records", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [r["temperature"] for r in body] == [51.0, 52.0]
    assert all(r["device_id"] == device_id for r in body)
    assert set(body[0]) == {"timestamp", "temperature", "moisture_percent", "o2_percent", "co2_percent", "nh3_ratio", "device_id"}

    body = client.get(f"/bins/{bin_id}/records?hours=48", headers=headers).json()
    assert [r["temperature"] for r in body] == [40.0, 51.0, 52.0]


def test_bin_records_show_every_sensor_in_the_bin(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    inner, inner_key = _device(client, headers, bin_id, name="Inner")
    outer, outer_key = _device(client, headers, bin_id, name="Outer")
    _send(client, inner_key, reading(timestamp=_ago(minutes=20), temperature=55.0))
    _send(client, outer_key, reading(timestamp=_ago(minutes=10), temperature=48.0))

    body = client.get(f"/bins/{bin_id}/records", headers=headers).json()
    assert [(r["device_id"], r["temperature"]) for r in body] == [(inner, 55.0), (outer, 48.0)]


def test_bin_records_of_a_new_bin_are_empty_not_missing(client, account):
    bin_id = _bin(client, account["headers"])
    response = client.get(f"/bins/{bin_id}/records", headers=account["headers"])
    assert response.status_code == 200
    assert response.json() == []


def test_bin_records_check_the_window_and_the_owner(client, account, other_account):
    bin_id = _bin(client, account["headers"])
    for hours in (0, 169, -1):
        assert client.get(f"/bins/{bin_id}/records?hours={hours}", headers=account["headers"]).status_code == 422
    assert client.get(f"/bins/{bin_id}/records?hours=168", headers=account["headers"]).status_code == 200
    assert client.get(f"/bins/{bin_id}/records", headers=other_account["headers"]).status_code == 404
    assert client.get(f"/bins/{RANDOM_ID}/records", headers=account["headers"]).status_code == 404


# ----------------------------------------------- GET /bins/{bin_id}/history ---

def test_history_is_one_row_per_day_with_average_and_peak(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    # midday UTC on two past days, well away from the midnight boundary
    today = datetime.datetime.now(datetime.timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    day1, day2 = today - datetime.timedelta(days=2), today - datetime.timedelta(days=1)
    _send(
        client, api_key,
        reading(timestamp=(day1 - datetime.timedelta(hours=1)).isoformat(), temperature=40.0),
        reading(timestamp=day1.isoformat(), temperature=50.0),
        reading(timestamp=(day2 - datetime.timedelta(hours=1)).isoformat(), temperature=60.0),
        reading(timestamp=day2.isoformat(), temperature=74.0),
        reading(timestamp=(day2 + datetime.timedelta(hours=1)).isoformat(), temperature=64.0),
    )

    response = client.get(f"/bins/{bin_id}/history?days=7", headers=headers)
    assert response.status_code == 200
    assert response.json() == [
        {"day": day1.date().isoformat(), "avg_temp": 45.0, "max_temp": 50.0},
        {"day": day2.date().isoformat(), "avg_temp": pytest.approx(66.0), "max_temp": 74.0},
    ]


def test_history_checks_the_window_and_the_owner(client, account, other_account):
    bin_id = _bin(client, account["headers"])
    assert client.get(f"/bins/{bin_id}/history", headers=account["headers"]).json() == []
    for days in (0, 366):
        assert client.get(f"/bins/{bin_id}/history?days={days}", headers=account["headers"]).status_code == 422
    assert client.get(f"/bins/{bin_id}/history", headers=other_account["headers"]).status_code == 404


# ------------------------------------------ GET /devices/{device_id}/records ---

def test_device_records_are_only_that_device(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    inner, inner_key = _device(client, headers, bin_id, name="Inner")
    _, outer_key = _device(client, headers, bin_id, name="Outer")
    _send(client, inner_key, reading(timestamp=_ago(minutes=20), temperature=55.0))
    _send(client, outer_key, reading(timestamp=_ago(minutes=10), temperature=48.0))

    response = client.get(f"/devices/{inner}/records", headers=headers)
    assert response.status_code == 200
    assert [(r["device_id"], r["temperature"]) for r in response.json()] == [(inner, 55.0)]


def test_device_records_check_the_window_and_the_owner(client, account, other_account):
    headers = account["headers"]
    device_id, _ = _device(client, headers, _bin(client, headers))
    assert client.get(f"/devices/{device_id}/records?hours=0", headers=headers).status_code == 422
    assert client.get(f"/devices/{device_id}/records", headers=other_account["headers"]).status_code == 404
    assert client.get(f"/devices/{RANDOM_ID}/records", headers=headers).status_code == 404


def test_a_handed_over_device_hides_the_previous_owners_readings(client, account, other_account):
    mac = _mac()
    first_bin = _bin(client, account["headers"], name="Dan's bin")
    device_id, first_key = _device(client, account["headers"], first_bin, mac=mac)
    _send(client, first_key, reading(timestamp=_ago(hours=5), temperature=61.0))

    # the hardware is passed on and paired to the other account: same device row
    second_bin = _bin(client, other_account["headers"], name="Andrew's bin")
    second_id, second_key = _device(client, other_account["headers"], second_bin, mac=mac)
    assert second_id == device_id
    _send(client, second_key, reading(timestamp=_ago(hours=1), temperature=45.0))

    body = client.get(f"/devices/{device_id}/records", headers=other_account["headers"]).json()
    assert [r["temperature"] for r in body] == [45.0]
    # and the previous owner can no longer see the device at all
    assert client.get(f"/devices/{device_id}/records", headers=account["headers"]).status_code == 404
    # their own bin still has their reading
    assert [r["temperature"] for r in client.get(f"/bins/{first_bin}/records", headers=account["headers"]).json()] == [61.0]


# --------------------------------------------------- PATCH /devices/{device_id} ---

def test_patching_a_device_can_rename_it_only(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    device_id, _ = _device(client, headers, bin_id)

    response = client.patch(f"/devices/{device_id}", headers=headers, json={"name": "  Inner sensor "})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Inner sensor"
    assert body["bin"]["id"] == bin_id and body["set_up"] is True
    assert len(_assignments(device_id)) == 1


def test_patching_a_device_can_move_it_and_keeps_the_history(client, account):
    headers = account["headers"]
    first, second = _bin(client, headers, name="First"), _bin(client, headers, name="Second")
    device_id, api_key = _device(client, headers, first, name="Kept name")

    response = client.patch(f"/devices/{device_id}", headers=headers, json={"bin_id": second})
    assert response.status_code == 200
    assert response.json()["bin"] == {"id": second, "name": "Second"}
    assert response.json()["name"] == "Kept name"

    rows = _assignments(device_id)
    assert [row.bin_id for row in rows] == [first, second]
    assert rows[0].unassigned_at is not None and rows[1].unassigned_at is None

    # new readings go to the new bin
    _send(client, api_key, reading(timestamp=_ago(minutes=1), temperature=50.0))
    assert len(client.get(f"/bins/{second}/records", headers=headers).json()) == 1
    assert client.get(f"/bins/{first}/records", headers=headers).json() == []

    # moving to the bin it's already in changes nothing
    client.patch(f"/devices/{device_id}", headers=headers, json={"bin_id": second})
    assert len(_assignments(device_id)) == 2


def test_patching_a_device_can_rename_and_move_at_once(client, account):
    headers = account["headers"]
    first, second = _bin(client, headers), _bin(client, headers, name="Second")
    device_id, _ = _device(client, headers, first)
    body = client.patch(f"/devices/{device_id}", headers=headers, json={"name": "Moved", "bin_id": second}).json()
    assert body["name"] == "Moved" and body["bin"]["id"] == second


def test_patching_a_device_needs_a_change_and_valid_values(client, account):
    headers = account["headers"]
    device_id, _ = _device(client, headers, _bin(client, headers))
    assert client.patch(f"/devices/{device_id}", headers=headers, json={}).status_code == 422
    assert client.patch(f"/devices/{device_id}", headers=headers, json={"name": "  "}).status_code == 422
    assert client.patch(f"/devices/{device_id}", headers=headers, json={"name": "x" * 81}).status_code == 422


def test_a_device_cant_be_moved_into_someone_elses_bin(client, account, other_account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    device_id, _ = _device(client, headers, bin_id, name="Original")
    theirs = _bin(client, other_account["headers"])

    response = client.patch(f"/devices/{device_id}", headers=headers, json={"name": "Changed", "bin_id": theirs})
    assert response.status_code == 404
    # nothing was applied, not even the rename
    device = client.get(f"/devices/{device_id}", headers=headers).json()
    assert device["name"] == "Original" and device["bin"]["id"] == bin_id


def test_you_cant_patch_someone_elses_device(client, account, other_account):
    device_id, _ = _device(client, account["headers"], _bin(client, account["headers"]))
    assert client.patch(f"/devices/{device_id}", headers=other_account["headers"], json={"name": "Mine"}).status_code == 404
    assert client.patch(f"/devices/{RANDOM_ID}", headers=account["headers"], json={"name": "Mine"}).status_code == 404


# ------------------------------------------------------- alerts at ingestion ---

def test_an_incident_opens_stays_single_and_resolves(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    hot1, hot2, cool = _ago(minutes=30), _ago(minutes=20), _ago(minutes=10)

    _send(client, api_key, reading(timestamp=hot1, temperature=72.0, moisture_percent=50.0))
    _send(client, api_key, reading(timestamp=hot2, temperature=74.0, moisture_percent=50.0))
    rows = _incidents(bin_id)
    assert len(rows) == 1
    assert (rows[0].type, rows[0].severity, rows[0].resolved_at) == ("too_hot", "high", None)
    assert rows[0].triggered_at == _at(hot1)

    _send(client, api_key, reading(timestamp=cool, temperature=60.0, moisture_percent=50.0))
    rows = _incidents(bin_id)
    assert len(rows) == 1 and rows[0].resolved_at == _at(cool)


def test_incidents_follow_reading_time_not_batch_order(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    first, second, third = _ago(minutes=30), _ago(minutes=20), _ago(minutes=10)

    _send(
        client, api_key,
        reading(timestamp=third, temperature=60.0, moisture_percent=50.0),
        reading(timestamp=first, temperature=72.0, moisture_percent=50.0),
        reading(timestamp=second, temperature=73.0, moisture_percent=50.0),
    )
    rows = _incidents(bin_id)
    assert len(rows) == 1
    assert rows[0].triggered_at == _at(first) and rows[0].resolved_at == _at(third)


def test_different_problems_are_separate_incidents(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    _send(
        client, api_key,
        reading(timestamp=_ago(minutes=30), temperature=72.0, moisture_percent=63.0),   # hot and wet
        reading(timestamp=_ago(minutes=20), temperature=72.0, moisture_percent=50.0),   # still hot
        reading(timestamp=_ago(minutes=10), temperature=60.0, moisture_percent=35.0),   # now dry
    )
    rows = {row.type: row for row in _incidents(bin_id)}
    assert set(rows) == {"too_hot", "too_wet", "too_dry"}
    assert rows["too_wet"].resolved_at is not None and rows["too_hot"].resolved_at is not None
    assert rows["too_dry"].resolved_at is None
    assert rows["too_wet"].severity == rows["too_dry"].severity == "medium"


def test_a_problem_that_comes_back_is_a_new_incident(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    _send(
        client, api_key,
        reading(timestamp=_ago(minutes=40), temperature=72.0, moisture_percent=50.0),
        reading(timestamp=_ago(minutes=30), temperature=60.0, moisture_percent=50.0),
        reading(timestamp=_ago(minutes=20), temperature=73.0, moisture_percent=50.0),
    )
    rows = _incidents(bin_id)
    assert [row.type for row in rows] == ["too_hot", "too_hot"]
    assert rows[0].resolved_at is not None and rows[1].resolved_at is None


def test_in_range_readings_open_nothing(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    # exactly on the thresholds is still fine
    _send(client, api_key, reading(timestamp=_ago(minutes=5), temperature=70.0, moisture_percent=40.0),
          reading(timestamp=_ago(minutes=4), temperature=55.0, moisture_percent=60.0))
    assert _incidents(bin_id) == []


# ------------------------------------------------------------- GET /alerts ---

def test_alerts_show_open_incidents_and_history_on_request(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers, name="Alert bin")
    _, api_key = _device(client, headers, bin_id)
    _send(
        client, api_key,
        reading(timestamp=_ago(minutes=40), temperature=72.0, moisture_percent=50.0),
        reading(timestamp=_ago(minutes=30), temperature=60.0, moisture_percent=50.0),   # hot resolved
        reading(timestamp=_ago(minutes=20), temperature=60.0, moisture_percent=35.0),   # dry, open
    )

    response = client.get("/alerts", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [(a["type"], a["bin_name"], a["resolved_at"]) for a in body] == [("too_dry", "Alert bin", None)]
    assert body[0]["id"] is not None and body[0]["device_id"] is None

    history = client.get("/alerts?include_resolved=true", headers=headers).json()
    assert [a["type"] for a in history] == ["too_dry", "too_hot"]   # newest first
    assert history[1]["resolved_at"] is not None


def test_alerts_can_be_filtered_to_one_bin(client, account):
    headers = account["headers"]
    hot_bin, dry_bin = _bin(client, headers, name="Hot"), _bin(client, headers, name="Dry")
    _, hot_key = _device(client, headers, hot_bin)
    _, dry_key = _device(client, headers, dry_bin)
    _send(client, hot_key, reading(timestamp=_ago(minutes=5), temperature=75.0, moisture_percent=50.0))
    _send(client, dry_key, reading(timestamp=_ago(minutes=5), temperature=50.0, moisture_percent=30.0))

    assert {a["type"] for a in client.get("/alerts", headers=headers).json()} == {"too_hot", "too_dry"}
    assert [a["type"] for a in client.get(f"/alerts?bin_id={hot_bin}", headers=headers).json()] == ["too_hot"]


def test_alerts_are_private(client, account, other_account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    _, api_key = _device(client, headers, bin_id)
    _send(client, api_key, reading(timestamp=_ago(minutes=5), temperature=75.0))

    assert client.get("/alerts", headers=other_account["headers"]).json() == []
    assert client.get(f"/alerts?bin_id={bin_id}&include_resolved=true", headers=other_account["headers"]).json() == []
    assert client.get("/alerts").status_code == 401


def test_a_quiet_device_shows_as_offline(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers, name="Quiet bin")
    device_id, api_key = _device(client, headers, bin_id, name="Quiet sensor")
    _send(client, api_key, reading(timestamp=_ago(minutes=1)))
    assert client.get("/alerts", headers=headers).json() == []

    quiet_for = OFFLINE_AFTER + datetime.timedelta(minutes=30)
    with db_engine.begin() as db:
        db.execute(text("UPDATE devices SET last_seen_at = now() - :quiet WHERE id = :d"), {"quiet": quiet_for, "d": device_id})

    body = client.get("/alerts", headers=headers).json()
    assert len(body) == 1
    alert = body[0]
    assert (alert["type"], alert["severity"], alert["id"], alert["resolved_at"]) == ("offline", "medium", None, None)
    assert (alert["device_id"], alert["device_name"], alert["bin_name"]) == (device_id, "Quiet sensor", "Quiet bin")

    # an upload brings it back online
    _send(client, api_key, reading(timestamp=_ago(minutes=0)))
    assert client.get("/alerts", headers=headers).json() == []


def test_a_new_device_or_one_without_a_bin_is_not_offline(client, account):
    headers = account["headers"]
    bin_id = _bin(client, headers)
    device_id, _ = _device(client, headers, bin_id)
    # set up a moment ago, nothing sent yet: not offline
    assert client.get("/alerts", headers=headers).json() == []

    # long quiet, but no longer in a bin (its bin was deleted): not offline
    with db_engine.begin() as db:
        db.execute(text("UPDATE devices SET last_seen_at = now() - interval '1 day' WHERE id = :d"), {"d": device_id})
    client.delete(f"/bins/{bin_id}", headers=headers)
    assert client.get("/alerts", headers=headers).json() == []
