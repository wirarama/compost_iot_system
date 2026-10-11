"""
The signed-in user's devices: register, list, finish setting up, new key, unpair.

A device joins an account in one of two ways:

  pairing       it redeems a pairing code and gets its key (routers/pairing.py),
                then the user confirms it here and gives it a name and a bin.
                Until then it has an owner but no bin, and /records answers
                409, which the device takes as "not set up yet".
  registration  POST /devices: the user names it and picks a bin in the
                dashboard and gets its key back, once, to copy onto the device
                by hand. It has no hardware ID, and its readings are accepted
                straight away. If the key is lost or leaked, POST
                /devices/{id}/key swaps it for a new one.
"""
import datetime
import hashlib
import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, Query
from pydantic import BaseModel, BeforeValidator, Field
from sqlalchemy import text

from database import db_engine
from routers.auth import current_user
from routers.bins import owned_bin, READING_COLUMNS, Reading

router = APIRouter(prefix="/devices", tags=["devices"])

NOT_FOUND = HTTPException(status_code=404, detail="Device not found.")
PAIRED_KEY_REFUSED = HTTPException(
    status_code=409,
    detail="Only a device registered with an API key can get a new key. A paired device gets one by being paired again.",
)


class BinRef(BaseModel):
    id: uuid.UUID
    name: str | None


class Device(BaseModel):
    id: uuid.UUID
    # the MAC it paired with; None for a registered device
    hardware_id: str | None
    # how it joined the account (see the top of this file), stored when the
    # device is created and never changed (migration 004)
    registration: Literal["pairing", "manual"]
    name: str | None
    model: str | None
    firmware_version: str | None
    paired_at: datetime.datetime | None
    last_seen_at: datetime.datetime | None
    bin: BinRef | None
    # confirmed in the dashboard, named and in a bin: its readings are accepted
    set_up: bool


# the rules for a device name, shared by setup and PATCH
DeviceName = Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
                       Field(min_length=1, max_length=80)]


class DeviceSetup(BaseModel):
    name: DeviceName
    bin_id: uuid.UUID


class DeviceChange(BaseModel):
    name: DeviceName | None = None
    bin_id: uuid.UUID | None = None


class DeviceRegistration(BaseModel):
    name: DeviceName
    bin_id: uuid.UUID


class DeviceWithKey(BaseModel):
    device: Device
    # returned this once; only its hash is stored
    api_key: str


DEVICE_SELECT = """
    SELECT d.id, d.mac, d.registration, d.name, d.model, d.firmware_version, d.paired_at, d.last_seen_at,
           b.id AS bin_id, b.name AS bin_name
    FROM devices d
    LEFT JOIN device_bin_assn a ON a.device_id = d.id AND a.unassigned_at IS NULL
    LEFT JOIN bins b ON b.id = a.bin_id
"""


def device_out(row):
    bin_ref = BinRef(id=row.bin_id, name=row.bin_name) if row.bin_id else None
    return Device(
        id=row.id,
        hardware_id=row.mac,
        registration=row.registration,
        name=row.name,
        model=row.model,
        firmware_version=row.firmware_version,
        paired_at=row.paired_at,
        last_seen_at=row.last_seen_at,
        bin=bin_ref,
        set_up=bin_ref is not None and bool(row.name),
    )


def owned_device(db, device_id, user_id):
    """The user's device row, or None - someone else's device is never shown."""
    return db.execute(
        text(DEVICE_SELECT + " WHERE d.id = :id AND d.owner_id = :user_id"),
        {"id": device_id, "user_id": user_id},
    ).first()


def issue_key(db, device_id):
    """A new API key for the device. Only its SHA-256 hash is stored, as
    /records expects, so this is the one time the key itself can be read."""
    api_key = secrets.token_hex(32)
    db.execute(
        text("INSERT INTO device_apikeys (device_id, api_key_hash) VALUES (:device_id, :hash)"),
        {"device_id": device_id, "hash": hashlib.sha256(api_key.encode()).hexdigest()},
    )
    return api_key


def revoke_keys(db, device_id):
    """Every key the device has stops working at once."""
    db.execute(
        text("UPDATE device_apikeys SET revoked_at = now() WHERE device_id = :id AND revoked_at IS NULL"),
        {"id": device_id},
    )


def release_device(db, device_id):
    """Revoke its keys and take it out of its bin: it can no longer send readings."""
    revoke_keys(db, device_id)
    db.execute(
        text("UPDATE device_bin_assn SET unassigned_at = now() WHERE device_id = :id AND unassigned_at IS NULL"),
        {"id": device_id},
    )


def change_device(db, device, user_id, name=None, bin_id=None):
    """Rename the device and/or move it to another of the user's bins.

    Shared by setup and PATCH; anything left as None is not changed.
    """
    if bin_id is not None and owned_bin(db, bin_id, user_id) is None:
        raise HTTPException(status_code=404, detail="Bin not found.")

    if name is not None:
        db.execute(text("UPDATE devices SET name = :name WHERE id = :id"), {"name": name, "id": device.id})

    if bin_id is not None and device.bin_id != bin_id:
        db.execute(
            text("UPDATE device_bin_assn SET unassigned_at = now() WHERE device_id = :id AND unassigned_at IS NULL"),
            {"id": device.id},
        )
        db.execute(
            text("INSERT INTO device_bin_assn (device_id, bin_id) VALUES (:id, :bin_id)"),
            {"id": device.id, "bin_id": bin_id},
        )


@router.get("", response_model=list[Device])
def list_devices(user=Depends(current_user)):
    with db_engine.connect() as db:
        rows = db.execute(
            text(DEVICE_SELECT + " WHERE d.owner_id = :user_id ORDER BY d.paired_at DESC NULLS LAST, d.name"),
            {"user_id": user.id},
        ).all()
    return [device_out(row) for row in rows]


@router.post("", status_code=201, response_model=DeviceWithKey)
def register_device(body: DeviceRegistration, user=Depends(current_user)):
    """Register a device without pairing: name it, put it in a bin, get its key.

    For hardware that can't run the pairing flow. The key goes onto the device
    by hand; there is no hardware ID to check, so nothing waits for a confirm.
    """
    with db_engine.begin() as db:
        # paired_at: when it joined the account, so it sorts with paired devices
        device_id = db.execute(
            text(
                """
                INSERT INTO devices (owner_id, name, registration, is_active, paired_at)
                VALUES (:user_id, :name, 'manual', true, now())
                RETURNING id
                """
            ),
            {"user_id": user.id, "name": body.name},
        ).scalar_one()
        # 404 for a bin that isn't the user's, which rolls the insert back
        change_device(db, owned_device(db, device_id, user.id), user.id, bin_id=body.bin_id)
        api_key = issue_key(db, device_id)
        row = owned_device(db, device_id, user.id)
    return DeviceWithKey(device=device_out(row), api_key=api_key)


@router.get("/{device_id}", response_model=Device)
def read_device(device_id: uuid.UUID, user=Depends(current_user)):
    with db_engine.connect() as db:
        row = owned_device(db, device_id, user.id)
    if row is None:
        raise NOT_FOUND
    return device_out(row)


@router.post("/{device_id}/setup", response_model=Device)
def set_up_device(device_id: uuid.UUID, body: DeviceSetup, user=Depends(current_user)):
    """Confirm the registration: name the device and put it in one of the user's bins."""
    with db_engine.begin() as db:
        device = owned_device(db, device_id, user.id)
        if device is None:
            raise NOT_FOUND
        change_device(db, device, user.id, name=body.name, bin_id=body.bin_id)
        row = owned_device(db, device_id, user.id)
    return device_out(row)


@router.post("/{device_id}/key", status_code=201, response_model=DeviceWithKey)
def replace_key(device_id: uuid.UUID, user=Depends(current_user)):
    """A new API key for a registered device; the old one stops working at once.

    For a key that was lost or leaked. Registered devices only (409 for any
    other): a paired device has to receive its key itself, so it gets a new
    one by being paired again, which revokes the old one too.
    """
    with db_engine.begin() as db:
        # the stored registration, not a guess from the MAC; the row stays
        # locked until the swap commits, so two requests at once can't both
        # leave a working key behind
        registration = db.execute(
            text("SELECT registration FROM devices WHERE id = :id AND owner_id = :user_id FOR UPDATE"),
            {"id": device_id, "user_id": user.id},
        ).scalar()
        if registration is None:
            raise NOT_FOUND
        if registration != "manual":
            raise PAIRED_KEY_REFUSED
        revoke_keys(db, device_id)
        api_key = issue_key(db, device_id)
        row = owned_device(db, device_id, user.id)
    return DeviceWithKey(device=device_out(row), api_key=api_key)


@router.delete("/{device_id}", status_code=204)
def unpair_device(device_id: uuid.UUID, user=Depends(current_user)):
    """Unpair: the key stops working at once and the device can be paired again."""
    with db_engine.begin() as db:
        if owned_device(db, device_id, user.id) is None:
            raise NOT_FOUND
        release_device(db, device_id)
        db.execute(
            text("UPDATE devices SET owner_id = NULL, name = NULL, is_active = false WHERE id = :id"),
            {"id": device_id},
        )
    return Response(status_code=204)


@router.get("/{device_id}/records", response_model=list[Reading])
def get_device_records(device_id: uuid.UUID, hours: Annotated[int, Query(ge=1, le=168)] = 24, user = Depends(current_user)):
    with db_engine.connect() as db:
        if owned_device(db, device_id, user.id) is None:
            raise HTTPException(status_code=404, detail="The device with the given ID does not exist.")
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        results = db.execute(
            text(
                f"""
                SELECT {READING_COLUMNS} FROM records r
                JOIN bins b on b.id = r.bin_id
                WHERE r.device_id = :device_id
                AND b.user_id = :user_id
                AND r.timestamp >= :since
                ORDER BY timestamp ASC
                """
            ),
            {"device_id":device_id, "since": since, "user_id":user.id}
        ).all()
    return [Reading(**row._mapping) for row in results]


@router.patch("/{device_id}", response_model=Device)
def update_device(device_id: uuid.UUID, body: DeviceChange, user=Depends(current_user)):
    """Device settings: rename it and/or move it to another of the user's bins."""
    if body.name is None and body.bin_id is None:
        raise HTTPException(status_code=422, detail="No fields are being changed.")
    with db_engine.begin() as db:
        device = owned_device(db, device_id, user.id)
        if device is None:
            raise NOT_FOUND
        change_device(db, device, user.id, name=body.name, bin_id=body.bin_id)
        row = owned_device(db, device_id, user.id)
    return device_out(row)