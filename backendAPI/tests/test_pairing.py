"""Pairing a device end to end: code -> redeem -> confirm + set up -> readings."""
import secrets

import pytest
from sqlalchemy import text

from conftest import TEST_PASSWORD, reading
from main import db_engine
from routers.pairing import MAX_FAILURES

# upper case, as the API stores hardware IDs
TEST_MAC_PREFIX = "02:00:00:00:FD:"
# what TestClient reports as the client address
TEST_CLIENT_IP = "testclient"


def _mac():
    return f"{TEST_MAC_PREFIX}{secrets.randbelow(256):02X}"


@pytest.fixture(autouse=True)
def clean_pairing_rows():
    def clean():
        devices = "SELECT id FROM devices WHERE mac LIKE :like"
        like = {"like": TEST_MAC_PREFIX + "%"}
        with db_engine.begin() as db:
            for table in ("device_apikeys", "device_bin_assn", "records", "setup_codes"):
                db.execute(text(f"DELETE FROM {table} WHERE device_id IN ({devices})"), like)
            db.execute(text("DELETE FROM devices WHERE mac LIKE :like"), like)
            db.execute(text("DELETE FROM pairing_failures WHERE client_ip = :ip"), {"ip": TEST_CLIENT_IP})
    clean()
    yield
    clean()


@pytest.fixture
def other_account(client, new_email):
    response = client.post("/auth/register", json={"email": new_email(), "password": TEST_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _code(client, headers):
    response = client.post("/pairing/codes", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["code"]


def _redeem(client, code, mac=None):
    return client.post("/pairing/redeem", json={
        "code": code, "device_uid": mac or _mac(), "model": "CompostIQ Emulator", "firmware_version": "0.1.0",
    })


def _bin(client, headers, name="Pairing test bin"):
    response = client.post("/bins", headers=headers, json={"name": name, "location": "Test", "country_code": "id"})
    assert response.status_code == 201, response.text
    return response.json()


def _paired(client, account, mac=None):
    """A redeemed device: (device_id, api_key, code)."""
    code = _code(client, account["headers"])
    response = _redeem(client, code, mac)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["device_id"], body["api_key"], code


def _set_up(client, account, device_id, name="Outer sensor"):
    bin_ = _bin(client, account["headers"])
    return client.post(f"/devices/{device_id}/setup", headers=account["headers"],
                       json={"name": name, "bin_id": bin_["id"]})


# ------------------------------------------------------------------ codes ---

def test_a_code_is_six_digits_and_expires(client, account):
    response = client.post("/pairing/codes", headers=account["headers"])
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 6 and body["code"].isdigit()
    status = client.get(f"/pairing/codes/{body['code']}", headers=account["headers"]).json()
    assert status["status"] == "pending"
    assert status["device"] is None


def test_codes_need_a_signed_in_user(client):
    assert client.post("/pairing/codes").status_code == 401


def test_a_code_is_private_to_its_user(client, account, other_account):
    code = _code(client, account["headers"])
    assert client.get(f"/pairing/codes/{code}", headers=other_account).status_code == 404


# ----------------------------------------------------------------- redeem ---

def test_redeeming_gives_the_device_a_key_but_no_bin(client, account):
    device_id, api_key, code = _paired(client, account)
    assert len(api_key) == 64

    status = client.get(f"/pairing/codes/{code}", headers=account["headers"]).json()
    assert status["status"] == "redeemed"
    assert status["device"]["id"] == device_id
    assert status["device"]["hardware_id"].startswith(TEST_MAC_PREFIX)
    assert status["device"]["model"] == "CompostIQ Emulator"
    assert status["device"]["registration"] == "pairing"
    assert status["device"]["set_up"] is False

    # the key works, but there's no bin until the user sets the device up
    response = client.post("/records", json=[reading()], headers={"x-key": api_key})
    assert response.status_code == 409


def test_only_the_key_hash_is_stored(client, account):
    device_id, api_key, _ = _paired(client, account)
    with db_engine.connect() as db:
        stored = db.execute(text("SELECT api_key_hash FROM device_apikeys WHERE device_id = :d"), {"d": device_id}).scalar_one()
    assert stored != api_key and len(stored) == 64


def test_a_code_works_once(client, account):
    code = _code(client, account["headers"])
    assert _redeem(client, code).status_code == 201
    response = _redeem(client, code)
    assert response.status_code == 409


def test_an_unknown_code_is_not_found(client):
    with db_engine.begin() as db:
        db.execute(text("DELETE FROM setup_codes WHERE code = '999999'"))
    assert _redeem(client, "999999").status_code == 404


def test_an_expired_code_is_refused(client, account):
    code = _code(client, account["headers"])
    with db_engine.begin() as db:
        db.execute(text("UPDATE setup_codes SET expiry = now() - interval '1 minute' WHERE code = :c"), {"c": code})
    assert _redeem(client, code).status_code == 410
    status = client.get(f"/pairing/codes/{code}", headers=account["headers"]).json()
    assert status["status"] == "expired"


def test_bad_hardware_ids_are_rejected(client, account):
    code = _code(client, account["headers"])
    assert _redeem(client, code, mac="not-a-mac").status_code == 422


def test_guessing_is_limited(client):
    for _ in range(MAX_FAILURES):
        _redeem(client, "123456")
    assert _redeem(client, "123456").status_code == 429


def test_pairing_again_takes_the_device_over_and_revokes_the_old_key(client, account, other_account):
    mac = _mac()
    device_id, old_key, _ = _paired(client, account, mac)
    assert _set_up(client, account, device_id).status_code == 200

    # factory reset, then paired by someone else with their own code
    response = _redeem(client, _code(client, other_account), mac)
    assert response.status_code == 201
    assert response.json()["device_id"] == device_id  # same hardware, same row

    assert client.post("/records", json=[reading()], headers={"x-key": old_key}).status_code == 401
    assert client.get(f"/devices/{device_id}", headers=account["headers"]).status_code == 404
    moved = client.get(f"/devices/{device_id}", headers=other_account).json()
    assert moved["set_up"] is False and moved["bin"] is None and moved["name"] is None


# ------------------------------------------------------------------ setup ---

def test_setup_names_the_device_and_readings_start(client, account):
    device_id, api_key, _ = _paired(client, account)
    response = _set_up(client, account, device_id, name="  Outer sensor ")
    assert response.status_code == 200
    device = response.json()
    assert device["name"] == "Outer sensor"
    assert device["set_up"] is True
    assert device["bin"]["name"] == "Pairing test bin"

    batch = [reading(timestamp="2000-01-01T00:00:00+00:00"), reading(timestamp="2000-01-01T00:30:00+00:00")]
    assert client.post("/records", json=batch, headers={"x-key": api_key}).status_code == 200
    with db_engine.connect() as db:
        count = db.execute(text("SELECT count(*) FROM records WHERE device_id = :d"), {"d": device_id}).scalar_one()
    assert count == 2
    listed = client.get("/devices", headers=account["headers"]).json()
    assert [d["id"] for d in listed] == [device_id]
    assert listed[0]["last_seen_at"] is not None


def test_moving_a_device_closes_its_old_bin(client, account):
    device_id, _, _ = _paired(client, account)
    _set_up(client, account, device_id)
    second = _bin(client, account["headers"], name="Second bin")
    response = client.post(f"/devices/{device_id}/setup", headers=account["headers"],
                           json={"name": "Outer sensor", "bin_id": second["id"]})
    assert response.json()["bin"]["id"] == second["id"]
    with db_engine.connect() as db:
        open_rows = db.execute(
            text("SELECT count(*) FROM device_bin_assn WHERE device_id = :d AND unassigned_at IS NULL"), {"d": device_id}
        ).scalar_one()
    assert open_rows == 1


def test_you_cant_set_up_someone_elses_device(client, account, other_account):
    device_id, _, _ = _paired(client, account)
    bin_ = _bin(client, other_account)
    response = client.post(f"/devices/{device_id}/setup", headers=other_account,
                           json={"name": "Mine now", "bin_id": bin_["id"]})
    assert response.status_code == 404


def test_you_cant_use_someone_elses_bin(client, account, other_account):
    device_id, _, _ = _paired(client, account)
    their_bin = _bin(client, other_account)
    response = client.post(f"/devices/{device_id}/setup", headers=account["headers"],
                           json={"name": "Sneaky", "bin_id": their_bin["id"]})
    assert response.status_code == 404


def test_setup_needs_a_name(client, account):
    device_id, _, _ = _paired(client, account)
    bin_ = _bin(client, account["headers"])
    response = client.post(f"/devices/{device_id}/setup", headers=account["headers"], json={"name": "  ", "bin_id": bin_["id"]})
    assert response.status_code == 422


# ----------------------------------------------------------------- unpair ---

def test_unpairing_revokes_the_key_at_once(client, account):
    device_id, api_key, _ = _paired(client, account)
    _set_up(client, account, device_id)
    assert client.delete(f"/devices/{device_id}", headers=account["headers"]).status_code == 204
    assert client.post("/records", json=[reading()], headers={"x-key": api_key}).status_code == 401
    assert client.get("/devices", headers=account["headers"]).json() == []


# ------------------------------------------------------------------- bins ---

def test_bins_are_listed_per_user(client, account, other_account):
    _bin(client, account["headers"], name="Mine")
    names = [b["name"] for b in client.get("/bins", headers=account["headers"]).json()]
    assert names == ["Mine"]
    assert client.get("/bins", headers=other_account).json() == []


def test_a_bin_needs_a_valid_country(client, account):
    response = client.post("/bins", headers=account["headers"], json={"name": "X", "country_code": "IDN"})
    assert response.status_code == 422
