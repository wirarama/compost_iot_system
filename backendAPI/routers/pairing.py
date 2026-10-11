"""
Pairing a device with a user's account.

  1. POST /pairing/codes          the signed-in dashboard asks for a code
  2. POST /pairing/redeem         the device sends the code and its hardware
                                  ID, and gets its API key back, once
  3. GET  /pairing/codes/{code}   the dashboard polls until the code is used,
                                  then shows which hardware ID used it
  4. POST /devices/{id}/setup     the user confirms and gives it a name and a
                                  bin (routers/devices.py)

The code is the only thing that proves which account a device belongs to, so
it is short-lived, used once, and guessing is limited per client IP. The key
itself is only ever stored as a SHA-256 hash, as /records expects.
"""
import datetime
import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from config import settings
from database import db_engine
from routers.auth import current_user
from routers.devices import Device, device_out, issue_key, owned_device, release_device

router = APIRouter(prefix="/pairing", tags=["pairing"])

# wrong codes allowed per client IP before it has to wait (a 6-digit code has
# a million values, so guessing a live one takes far more tries than this)
MAX_FAILURES = 10
FAILURE_WINDOW = datetime.timedelta(minutes=15)
# live codes one user may hold at once (e.g. pairing in two tabs)
MAX_LIVE_CODES_PER_USER = 5

CODE_PATTERN = r"^\d{6}$"
MAC_PATTERN = r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$"


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value):
    return _strip(value) or None


Optional80 = Annotated[str | None, BeforeValidator(_blank_to_none), Field(default=None, max_length=80)]


class PairingCode(BaseModel):
    code: str
    expires_at: datetime.datetime


class PairingStatus(BaseModel):
    code: str
    status: Literal["pending", "redeemed", "expired"]
    expires_at: datetime.datetime
    # the device that used the code, once it has (and while this user owns it)
    device: Device | None = None


class RedeemRequest(BaseModel):
    code: Annotated[str, BeforeValidator(_strip), Field(pattern=CODE_PATTERN)]
    # the device's MAC address, stored upper case in devices.mac
    device_uid: Annotated[str, BeforeValidator(_strip), Field(pattern=MAC_PATTERN), AfterValidator(str.upper)]
    model: Optional80 = None
    firmware_version: Optional80 = None


class RedeemResponse(BaseModel):
    device_id: uuid.UUID
    api_key: str


# ------------------------------------------------------------ dashboard ---

@router.post("/codes", status_code=201, response_model=PairingCode)
def create_code(user=Depends(current_user)):
    expires_at = (datetime.datetime.now(datetime.timezone.utc)
                  + datetime.timedelta(minutes=settings.pairing_code_minutes)).replace(microsecond=0)
    with db_engine.begin() as db:
        # codes that ran out unused are no use to anyone; clearing them also
        # frees their numbers for reuse
        db.execute(text("DELETE FROM setup_codes WHERE used_at IS NULL AND expiry < now()"))
        # keep only the user's newest few live codes
        db.execute(
            text(
                """
                DELETE FROM setup_codes WHERE id IN (
                    SELECT id FROM setup_codes WHERE user_id = :user_id AND used_at IS NULL
                    ORDER BY created_at DESC OFFSET :keep
                )
                """
            ),
            {"user_id": user.id, "keep": MAX_LIVE_CODES_PER_USER - 1},
        )
        # a number another live code already has is skipped by the partial
        # unique index; with a million numbers, a clash is rare
        for _ in range(20):
            code = f"{secrets.randbelow(10**6):06d}"
            row = db.execute(
                text(
                    """
                    INSERT INTO setup_codes (code, user_id, expiry) VALUES (:code, :user_id, :expiry)
                    ON CONFLICT (code) WHERE used_at IS NULL DO NOTHING
                    RETURNING code
                    """
                ),
                {"code": code, "user_id": user.id, "expiry": expires_at},
            ).first()
            if row:
                return PairingCode(code=code, expires_at=expires_at)
    raise HTTPException(status_code=503, detail="Couldn't issue a pairing code. Try again.")


@router.get("/codes/{code}", response_model=PairingStatus)
def read_code(code: str, user=Depends(current_user)):
    """Has the device used this code yet? Polled by the dashboard's pairing page."""
    with db_engine.connect() as db:
        row = db.execute(
            text(
                """
                SELECT code, expiry, used_at, device_id FROM setup_codes
                WHERE code = :code AND user_id = :user_id
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"code": code, "user_id": user.id},
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Pairing code not found.")

        if row.used_at is not None:
            device = owned_device(db, row.device_id, user.id) if row.device_id else None
            return PairingStatus(code=row.code, status="redeemed", expires_at=row.expiry,
                                 device=device_out(device) if device else None)

    expired = row.expiry <= datetime.datetime.now(datetime.timezone.utc)
    return PairingStatus(code=row.code, status="expired" if expired else "pending", expires_at=row.expiry)


# --------------------------------------------------------------- device ---

def _client_ip(request):
    # uvicorn --proxy-headers (PM2) puts the real client here from NGINX's X-Forwarded-For
    return request.client.host if request.client else "unknown"


def _too_many_failures(db, client_ip):
    db.execute(text("DELETE FROM pairing_failures WHERE failed_at < now() - :window"), {"window": FAILURE_WINDOW})
    failures = db.execute(
        text("SELECT count(*) FROM pairing_failures WHERE client_ip = :ip AND failed_at >= now() - :window"),
        {"ip": client_ip, "window": FAILURE_WINDOW},
    ).scalar_one()
    return failures >= MAX_FAILURES


def _record_failure(client_ip):
    # its own transaction, so it is kept even though the request fails
    with db_engine.begin() as db:
        db.execute(text("INSERT INTO pairing_failures (client_ip) VALUES (:ip)"), {"ip": client_ip})


@router.post("/redeem", status_code=201, response_model=RedeemResponse)
def redeem_code(body: RedeemRequest, request: Request):
    """Called by the device: swap a pairing code for this device's API key.

    No bin is assigned here. The device has a key straight away, but its
    readings are only accepted once the user sets it up in the dashboard.
    """
    client_ip = _client_ip(request)
    failure = None
    try:
        with db_engine.begin() as db:
            if _too_many_failures(db, client_ip):
                raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes, then try again.")

            # FOR UPDATE: two devices sending the same code at once can't both win
            code = db.execute(
                text("SELECT id, user_id, expiry FROM setup_codes WHERE code = :code AND used_at IS NULL FOR UPDATE"),
                {"code": body.code},
            ).first()
            if code is None:
                used = db.execute(
                    text("SELECT 1 FROM setup_codes WHERE code = :code AND used_at IS NOT NULL AND expiry > now()"),
                    {"code": body.code},
                ).first()
                failure = (409, "Pairing code already used.") if used else (404, "Pairing code not found or expired.")
            elif code.expiry <= datetime.datetime.now(datetime.timezone.utc):
                failure = (410, "Pairing code expired.")
            else:
                device_id = _claim_device(db, body, code.user_id)
                api_key = issue_key(db, device_id)
                db.execute(
                    text("UPDATE setup_codes SET used_at = now(), device_id = :device_id WHERE id = :id"),
                    {"device_id": device_id, "id": code.id},
                )
    except IntegrityError:
        # the same hardware ID being paired twice at the same moment
        raise HTTPException(status_code=409, detail="This device is being paired already. Try again.")

    if failure:
        _record_failure(client_ip)
        raise HTTPException(status_code=failure[0], detail=failure[1])
    return RedeemResponse(device_id=device_id, api_key=api_key)


def _claim_device(db, body, user_id):
    """The devices row for this hardware, now owned by the code's user.

    Hardware that was paired before (a factory reset, or a device passed on
    to someone else) keeps its row, but loses its old keys, bin and name:
    whoever holds a fresh code and the device itself decides where it goes.
    """
    existing = db.execute(
        text("SELECT id FROM devices WHERE upper(mac) = :mac FOR UPDATE"),
        {"mac": body.device_uid},
    ).first()
    details = {"mac": body.device_uid, "user_id": user_id, "model": body.model, "firmware": body.firmware_version}
    if existing is None:
        return db.execute(
            text(
                """
                INSERT INTO devices (mac, owner_id, model, firmware_version, is_active, paired_at)
                VALUES (:mac, :user_id, :model, :firmware, true, now())
                RETURNING id
                """
            ),
            details,
        ).scalar_one()

    release_device(db, existing.id)
    db.execute(
        text(
            """
            UPDATE devices
            SET owner_id = :user_id, name = NULL, model = :model, firmware_version = :firmware,
                is_active = true, paired_at = now(), last_seen_at = NULL
            WHERE id = :id
            """
        ),
        {**details, "id": existing.id},
    )
    return existing.id
