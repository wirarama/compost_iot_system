"""Registering a device without pairing: name + bin -> API key -> readings."""
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from conftest import TEST_PASSWORD, reading
from main import db_engine


@pytest.fixture
def registered():
    """The ids of the devices a test registered, removed again afterwards.

    Registered devices have no MAC to find them by, and an unpaired one has
    no owner or name either, so each test lists what it made.
    """
    device_ids = []
    yield device_ids
    with db_engine.begin() as db:
        for device_id in device_ids:
            for table in ("records", "device_apikeys", "device_bin_assn", "setup_codes"):
                db.execute(text(f"DELETE FROM {table} WHERE device_id = :d"), {"d": device_id})
            db.execute(text("DELETE FROM devices WHERE id = :d"), {"d": device_id})


@pytest.fixture
def other_account(client, new_email):
    response = client.post("/auth/register", json={"email": new_email(), "password": TEST_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _bin(client, headers, name="Registration test bin"):
    response = client.post("/bins", headers=headers, json={"name": name, "location": "Test", "country_code": "id"})
    assert response.status_code == 201, response.text
    return response.json()


def _register(client, account, registered, name="Hand-built sensor", bin_id=None):
    """A registered device: (device, api_key)."""
    bin_id = bin_id or _bin(client, account["headers"])["id"]
    response = client.post("/devices", headers=account["headers"], json={"name": name, "bin_id": bin_id})
    assert response.status_code == 201, response.text
    body = response.json()
    registered.append(body["device"]["id"])
    return body["device"], body["api_key"]


def test_registering_gives_a_key_and_readings_are_accepted_at_once(client, account, registered):
    device, api_key = _register(client, account, registered, name="  Hand-built sensor ")
    assert len(api_key) == 64
    assert device["name"] == "Hand-built sensor"
    assert device["bin"]["name"] == "Registration test bin"
    assert device["set_up"] is True

    # no confirm step: the first batch is stored
    batch = [reading(timestamp="2000-01-01T00:00:00+00:00"), reading(timestamp="2000-01-01T00:30:00+00:00")]
    assert client.post("/records", json=batch, headers={"x-key": api_key}).status_code == 200
    with db_engine.connect() as db:
        count = db.execute(text("SELECT count(*) FROM records WHERE device_id = :d"), {"d": device["id"]}).scalar_one()
    assert count == 2


def test_only_the_key_hash_is_stored(client, account, registered):
    device, api_key = _register(client, account, registered)
    with db_engine.connect() as db:
        stored = db.execute(text("SELECT api_key_hash FROM device_apikeys WHERE device_id = :d"), {"d": device["id"]}).scalar_one()
    assert stored != api_key and len(stored) == 64


def test_a_registered_device_is_listed_without_a_hardware_id(client, account, registered):
    device, _ = _register(client, account, registered)
    listed = client.get("/devices", headers=account["headers"]).json()
    assert [d["id"] for d in listed] == [device["id"]]
    assert listed[0]["registration"] == "manual"
    assert listed[0]["hardware_id"] is None
    assert listed[0]["last_seen_at"] is None


def test_registering_needs_a_signed_in_user(client):
    response = client.post("/devices", json={"name": "Sensor", "bin_id": "00000000-0000-4000-8000-000000000101"})
    assert response.status_code == 401


def test_another_users_bin_is_not_found_and_nothing_is_made(client, account, other_account):
    theirs = _bin(client, other_account)
    response = client.post("/devices", headers=account["headers"], json={"name": "Sensor", "bin_id": theirs["id"]})
    assert response.status_code == 404
    assert client.get("/devices", headers=account["headers"]).json() == []


def test_a_name_is_required(client, account):
    bin_ = _bin(client, account["headers"])
    response = client.post("/devices", headers=account["headers"], json={"name": "   ", "bin_id": bin_["id"]})
    assert response.status_code == 422


def test_unpairing_a_registered_device_revokes_its_key(client, account, registered):
    device, api_key = _register(client, account, registered)
    assert client.delete(f"/devices/{device['id']}", headers=account["headers"]).status_code == 204
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 401
    assert client.get(f"/devices/{device['id']}", headers=account["headers"]).status_code == 404


def test_a_device_whose_bin_was_deleted_can_be_set_up_again(client, account, registered):
    device, api_key = _register(client, account, registered)
    assert client.delete(f"/bins/{device['bin']['id']}", headers=account["headers"]).status_code == 204

    stranded = client.get(f"/devices/{device['id']}", headers=account["headers"]).json()
    assert stranded["set_up"] is False
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 409

    second = _bin(client, account["headers"], name="Second bin")
    response = client.post(f"/devices/{device['id']}/setup", headers=account["headers"],
                           json={"name": device["name"], "bin_id": second["id"]})
    assert response.status_code == 200
    assert response.json()["set_up"] is True
    # the same key keeps working
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 200


# ---------------------------------------------------------------- new key ---

def test_a_new_key_replaces_the_old_one(client, account, registered):
    device, old_key = _register(client, account, registered)
    response = client.post(f"/devices/{device['id']}/key", headers=account["headers"])
    assert response.status_code == 201
    body = response.json()
    new_key = body["api_key"]
    assert len(new_key) == 64 and new_key != old_key
    assert body["device"]["id"] == device["id"]

    assert client.post("/records", json=[reading()], headers={"x-key": old_key}).status_code == 401
    assert client.post("/records", json=[reading()], headers={"x-key": new_key}).status_code == 200
    # still in its bin, still set up: only the key changed
    assert client.get(f"/devices/{device['id']}", headers=account["headers"]).json()["set_up"] is True


def test_a_new_key_needs_the_owner(client, account, other_account, registered):
    device, api_key = _register(client, account, registered)
    assert client.post(f"/devices/{device['id']}/key").status_code == 401
    assert client.post(f"/devices/{device['id']}/key", headers=other_account).status_code == 404
    # the owner's key is untouched
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 200


def _paired_and_set_up(client, account, registered):
    """A device that joined by pairing, named and in a bin: (device_id, api_key)."""
    code = client.post("/pairing/codes", headers=account["headers"]).json()["code"]
    redeemed = client.post("/pairing/redeem", json={
        "code": code, "device_uid": f"02:00:00:00:FD:{secrets.randbelow(256):02X}",
    }).json()
    registered.append(redeemed["device_id"])
    bin_id = _bin(client, account["headers"])["id"]
    response = client.post(f"/devices/{redeemed['device_id']}/setup", headers=account["headers"],
                           json={"name": "Paired sensor", "bin_id": bin_id})
    assert response.status_code == 200, response.text
    return redeemed["device_id"], redeemed["api_key"]


def _live_keys(device_id):
    with db_engine.connect() as db:
        return db.execute(
            text("SELECT count(*) FROM device_apikeys WHERE device_id = :d AND revoked_at IS NULL"), {"d": device_id}
        ).scalar_one()


def test_a_paired_device_gets_no_new_key(client, account, registered):
    device_id, api_key = _paired_and_set_up(client, account, registered)
    assert client.get(f"/devices/{device_id}", headers=account["headers"]).json()["registration"] == "pairing"

    response = client.post(f"/devices/{device_id}/key", headers=account["headers"])
    assert response.status_code == 409
    assert "api_key" not in response.json()
    # nothing was revoked or added: the key it paired with is still its only one, and works
    assert _live_keys(device_id) == 1
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 200


def test_a_paired_device_cant_be_made_registered_through_the_api(client, account, registered):
    device_id, _ = _paired_and_set_up(client, account, registered)
    # the settings endpoint ignores fields it doesn't know
    response = client.patch(f"/devices/{device_id}", headers=account["headers"],
                            json={"name": "Renamed", "registration": "manual"})
    assert response.status_code == 200
    assert response.json()["registration"] == "pairing"
    assert client.post(f"/devices/{device_id}/key", headers=account["headers"]).status_code == 409


def test_the_database_keeps_registration_in_step_with_the_mac(client, account, registered):
    device_id, _ = _paired_and_set_up(client, account, registered)
    # a paired device (it has a MAC) can't be marked as registered...
    with pytest.raises(IntegrityError):
        with db_engine.begin() as db:
            db.execute(text("UPDATE devices SET registration = 'manual' WHERE id = :d"), {"d": device_id})
    # ...and a device without a MAC can't pass for a paired one
    with pytest.raises(IntegrityError):
        with db_engine.begin() as db:
            db.execute(text("INSERT INTO devices (owner_id, registration) VALUES (:u, 'pairing')"),
                       {"u": account["user"]["id"]})
    registered_device, _ = _register(client, account, registered)
    with pytest.raises(IntegrityError):
        with db_engine.begin() as db:
            db.execute(text("UPDATE devices SET registration = 'pairing' WHERE id = :d"), {"d": registered_device["id"]})
