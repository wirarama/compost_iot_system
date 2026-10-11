"""/auth - register, sign in, and the signed-in user's own account."""
import datetime
import secrets

import jwt
from sqlalchemy import text

from config import settings
from conftest import TEST_EPOCH, TEST_PASSWORD, reading
from main import db_engine

LOGIN_FAILED = {"detail": "Email or password is incorrect."}


def _token(user_id, secret=None, expires_in_hours=1):
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=expires_in_hours)
    return jwt.encode({"sub": user_id, "exp": expires_at}, secret or settings.jwt_secret, algorithm="HS256")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _scalar(sql, **params):
    with db_engine.connect() as db:
        return db.execute(text(sql), params).scalar()


# ---------------------------------------------------------------- register ---

def test_register_signs_the_user_in(client, new_email):
    email = new_email()
    response = client.post(
        "/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "display_name": "  Ayu  "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == email
    assert body["user"]["display_name"] == "Ayu"
    expires_at = datetime.datetime.fromisoformat(body["expires_at"])
    lifetime = expires_at - datetime.datetime.now(datetime.timezone.utc)
    assert datetime.timedelta(hours=settings.jwt_expiry_hours - 1) < lifetime <= datetime.timedelta(hours=settings.jwt_expiry_hours)

    # the token works straight away
    me = client.get("/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == body["user"]


def test_register_stores_a_lower_case_email_and_an_argon2_hash(client, new_email):
    email = new_email()
    response = client.post("/auth/register", json={"email": email.upper(), "password": TEST_PASSWORD})

    assert response.status_code == 201
    assert response.json()["user"]["email"] == email
    assert response.json()["user"]["display_name"] is None
    stored = _scalar("SELECT password_hash FROM users WHERE email = :e", e=email)
    assert stored.startswith("$argon2id$")
    assert TEST_PASSWORD not in stored


def test_register_with_a_used_email_is_a_conflict(client, account):
    response = client.post("/auth/register", json={"email": account["email"], "password": TEST_PASSWORD})
    assert response.status_code == 409


def test_register_treats_email_case_as_the_same_account(client, account):
    response = client.post("/auth/register", json={"email": account["email"].upper(), "password": TEST_PASSWORD})
    assert response.status_code == 409
    assert _scalar("SELECT count(*) FROM users WHERE lower(email) = :e", e=account["email"]) == 1


def test_register_with_a_short_password_is_a_validation_error(client, new_email):
    email = new_email()
    response = client.post("/auth/register", json={"email": email, "password": "short"})
    assert response.status_code == 422
    assert _scalar("SELECT count(*) FROM users WHERE email = :e", e=email) == 0


def test_register_with_a_bad_email_is_a_validation_error(client):
    response = client.post("/auth/register", json={"email": "not-an-email", "password": TEST_PASSWORD})
    assert response.status_code == 422


# ------------------------------------------------------------------- login ---

def test_login_returns_a_working_token(client, account):
    response = client.post("/auth/login", json={"email": account["email"], "password": account["password"]})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_at", "user"}
    assert body["user"] == account["user"]
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 200


def test_login_ignores_email_case(client, account):
    response = client.post("/auth/login", json={"email": account["email"].upper(), "password": account["password"]})
    assert response.status_code == 200


def test_login_failures_look_the_same(client, account, new_email):
    wrong_password = client.post("/auth/login", json={"email": account["email"], "password": "not-the-password"})
    unknown_email = client.post("/auth/login", json={"email": new_email(), "password": account["password"]})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == LOGIN_FAILED


def test_login_with_a_non_argon2_hash_fails_cleanly(client, new_email):
    email = new_email()
    with db_engine.begin() as db:
        db.execute(text("INSERT INTO users (email, password_hash) VALUES (:e, '!placeholder')"), {"e": email})
    response = client.post("/auth/login", json={"email": email, "password": "!placeholder"})
    assert response.status_code == 401
    assert response.json() == LOGIN_FAILED


# ------------------------------------------------------------------ tokens ---

def test_me_requires_a_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_a_token_that_is_not_a_jwt(client):
    assert client.get("/auth/me", headers=_bearer("not-a-token")).status_code == 401


def test_me_rejects_an_expired_token(client, account):
    expired = _token(account["user"]["id"], expires_in_hours=-1)
    response = client.get("/auth/me", headers=_bearer(expired))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_a_token_signed_with_another_secret(client, account):
    forged = _token(account["user"]["id"], secret=secrets.token_urlsafe(48))
    assert client.get("/auth/me", headers=_bearer(forged)).status_code == 401


def test_me_rejects_a_tampered_token(client, account, new_email):
    other = client.post("/auth/register", json={"email": new_email(), "password": TEST_PASSWORD}).json()
    # the other user's payload with this user's signature
    header, _, signature = account["headers"]["Authorization"].removeprefix("Bearer ").split(".")
    _, payload, _ = other["access_token"].split(".")
    response = client.get("/auth/me", headers=_bearer(f"{header}.{payload}.{signature}"))
    assert response.status_code == 401


def test_me_rejects_an_unsigned_token(client, account):
    unsigned = jwt.encode({"sub": account["user"]["id"], "exp": 4102444800}, key=None, algorithm="none")
    assert client.get("/auth/me", headers=_bearer(unsigned)).status_code == 401


def test_me_rejects_a_token_without_an_expiry(client, account):
    eternal = jwt.encode({"sub": account["user"]["id"]}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/auth/me", headers=_bearer(eternal)).status_code == 401


def test_me_rejects_a_token_for_a_user_that_no_longer_exists(client, account):
    with db_engine.begin() as db:
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": account["user"]["id"]})
    assert client.get("/auth/me", headers=account["headers"]).status_code == 401


def test_a_user_token_cannot_write_records(client, account):
    # /records only takes a device x-key; a user's JWT is no substitute
    as_bearer = client.post("/records", json=[reading()], headers=account["headers"])
    as_key = client.post(
        "/records",
        json=[reading()],
        headers={"x-key": account["headers"]["Authorization"].removeprefix("Bearer ")},
    )
    assert as_bearer.status_code in (401, 403)
    assert as_key.status_code == 401


# ----------------------------------------------------------------- profile ---

def test_patch_me_changes_the_display_name(client, account):
    response = client.patch("/auth/me", json={"display_name": "Compost Carl"}, headers=account["headers"])

    assert response.status_code == 200
    assert response.json() == {**account["user"], "display_name": "Compost Carl"}
    assert client.get("/auth/me", headers=account["headers"]).json()["display_name"] == "Compost Carl"


def test_patch_me_with_a_blank_name_clears_it(client, account):
    response = client.patch("/auth/me", json={"display_name": "   "}, headers=account["headers"])
    assert response.status_code == 200
    assert response.json()["display_name"] is None


def test_patch_me_with_a_very_long_name_is_a_validation_error(client, account):
    response = client.patch("/auth/me", json={"display_name": "x" * 81}, headers=account["headers"])
    assert response.status_code == 422


def test_patch_me_requires_a_token(client):
    assert client.patch("/auth/me", json={"display_name": "Nobody"}).status_code == 401


# --------------------------------------------------------- change password ---

def test_change_password_swaps_the_password(client, account):
    response = client.post(
        "/auth/change-password",
        json={"current_password": account["password"], "new_password": "a-brand-new-password"},
        headers=account["headers"],
    )
    assert response.status_code == 204

    old = client.post("/auth/login", json={"email": account["email"], "password": account["password"]})
    new = client.post("/auth/login", json={"email": account["email"], "password": "a-brand-new-password"})
    assert old.status_code == 401
    assert new.status_code == 200


def test_change_password_with_the_wrong_current_password_is_rejected(client, account):
    response = client.post(
        "/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "a-brand-new-password"},
        headers=account["headers"],
    )
    assert response.status_code == 401
    # a wrong password is not a token problem, so the client can tell them apart
    assert "www-authenticate" not in response.headers
    assert client.post("/auth/login", json={"email": account["email"], "password": account["password"]}).status_code == 200


def test_change_password_to_a_short_password_is_a_validation_error(client, account):
    response = client.post(
        "/auth/change-password",
        json={"current_password": account["password"], "new_password": "short"},
        headers=account["headers"],
    )
    assert response.status_code == 422


# ---------------------------------------------------------- delete account ---

def _give_the_user_a_bin_and_device(user_id):
    """One bin with a paired, active device that has a key, a reading, an
    alert and a pending setup code. Returns the ids."""
    with db_engine.begin() as db:
        bin_id = db.execute(
            text("INSERT INTO bins (location, user_id, name) VALUES ('Test', :u, 'Test bin') RETURNING id"),
            {"u": user_id},
        ).scalar_one()
        device_id = db.execute(
            text("INSERT INTO devices (mac, owner_id, is_active) VALUES (:mac, :u, true) RETURNING id"),
            {"mac": f"02:00:00:00:fe:{secrets.randbelow(256):02x}", "u": user_id},
        ).scalar_one()
        key_id = db.execute(
            text("INSERT INTO device_apikeys (device_id, api_key_hash) VALUES (:d, :h) RETURNING id"),
            {"d": device_id, "h": secrets.token_hex(32)},
        ).scalar_one()
        db.execute(text("INSERT INTO device_bin_assn (device_id, bin_id) VALUES (:d, :b)"), {"d": device_id, "b": bin_id})
        reading_id = db.execute(
            text(
                """
                INSERT INTO records (device_id, bin_id, timestamp, temperature, moisture_percent,
                                     o2_percent, co2_percent, nh3_ratio)
                VALUES (:d, :b, :t, 55, 52, 6.5, 4.2, 0.4) RETURNING id
                """
            ),
            {"d": device_id, "b": bin_id, "t": TEST_EPOCH},
        ).scalar_one()
        db.execute(
            text("INSERT INTO bin_events (bin_id, type, severity, reading_id) VALUES (:b, 'too_hot', 'high', :r)"),
            {"b": bin_id, "r": reading_id},
        )
        db.execute(
            text("INSERT INTO setup_codes (code, user_id, device_id, expiry) VALUES (:c, :u, :d, now() + interval '10 minutes')"),
            {"c": f"pytest-{secrets.token_hex(4)}", "u": user_id, "d": device_id},
        )
    return {"bin": bin_id, "device": device_id, "key": key_id}


def test_delete_me_with_the_wrong_password_changes_nothing(client, account):
    ids = _give_the_user_a_bin_and_device(account["user"]["id"])

    response = client.request("DELETE", "/auth/me", json={"password": "not-the-password"}, headers=account["headers"])

    assert response.status_code == 401
    assert client.get("/auth/me", headers=account["headers"]).status_code == 200
    assert _scalar("SELECT count(*) FROM bins WHERE id = :b", b=ids["bin"]) == 1
    assert _scalar("SELECT revoked_at FROM device_apikeys WHERE id = :k", k=ids["key"]) is None


def test_delete_me_requires_a_token(client):
    assert client.request("DELETE", "/auth/me", json={"password": TEST_PASSWORD}).status_code == 401


def test_delete_me_removes_the_account_and_unpairs_its_devices(client, account, new_email):
    user_id = account["user"]["id"]
    ids = _give_the_user_a_bin_and_device(user_id)
    # someone else's data must be left alone
    bystander = client.post("/auth/register", json={"email": new_email(), "password": TEST_PASSWORD}).json()
    theirs = _give_the_user_a_bin_and_device(bystander["user"]["id"])

    response = client.request("DELETE", "/auth/me", json={"password": account["password"]}, headers=account["headers"])

    assert response.status_code == 204
    assert _scalar("SELECT count(*) FROM users WHERE id = :u", u=user_id) == 0
    # the bin is gone, and everything under it
    for table in ("bins WHERE id", "device_bin_assn WHERE bin_id", "records WHERE bin_id", "bin_events WHERE bin_id"):
        assert _scalar(f"SELECT count(*) FROM {table} = :b", b=ids["bin"]) == 0, table
    assert _scalar("SELECT count(*) FROM setup_codes WHERE user_id = :u", u=user_id) == 0
    # the device stays, unpaired, with its key revoked
    with db_engine.connect() as db:
        device = db.execute(text("SELECT owner_id, is_active FROM devices WHERE id = :d"), {"d": ids["device"]}).one()
    assert device.owner_id is None
    assert device.is_active is False
    assert _scalar("SELECT revoked_at FROM device_apikeys WHERE id = :k", k=ids["key"]) is not None

    # the old token and password are dead
    assert client.get("/auth/me", headers=account["headers"]).status_code == 401
    login = client.post("/auth/login", json={"email": account["email"], "password": account["password"]})
    assert login.status_code == 401

    # the bystander is untouched
    assert _scalar("SELECT count(*) FROM records WHERE bin_id = :b", b=theirs["bin"]) == 1
    assert _scalar("SELECT count(*) FROM bin_events WHERE bin_id = :b", b=theirs["bin"]) == 1
    assert _scalar("SELECT owner_id::text FROM devices WHERE id = :d", d=theirs["device"]) == bystander["user"]["id"]
    assert _scalar("SELECT revoked_at FROM device_apikeys WHERE id = :k", k=theirs["key"]) is None


def test_a_deleted_accounts_email_can_register_again(client, account):
    client.request("DELETE", "/auth/me", json={"password": account["password"]}, headers=account["headers"])
    response = client.post("/auth/register", json={"email": account["email"], "password": TEST_PASSWORD})
    assert response.status_code == 201
