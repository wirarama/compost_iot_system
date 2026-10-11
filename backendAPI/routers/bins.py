"""
The signed-in user's compost bins.

Only what pairing needs so far: list them, and create one. Every query is
scoped to the user, so another user's bin simply doesn't exist (404).
"""
import uuid
from typing import Annotated
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field
from sqlalchemy import text

from database import db_engine
from routers.auth import current_user

router = APIRouter(prefix="/bins", tags=["bins"])


def _strip(value):
    return value.strip() if isinstance(value, str) else value


Name = Annotated[str, BeforeValidator(_strip), Field(min_length=1, max_length=80)]
Location = Annotated[str, BeforeValidator(_strip), Field(max_length=120)]
# ISO 3166-1 alpha-2, stored upper case
CountryCode = Annotated[str, Field(pattern=r"^[A-Za-z]{2}$"), AfterValidator(str.upper)]


class BinCreate(BaseModel):
    name: Name
    location: Location = ""
    country_code: CountryCode

class Bin(BaseModel):
    id: uuid.UUID
    name: str | None
    location: str
    country_code: str | None

class BinChange(BaseModel):
    name: Name | None = None
    location: Location | None = None
    country_code: CountryCode | None = None
    
class Reading(BaseModel):
    timestamp: datetime.datetime
    temperature: float
    moisture_percent: float
    o2_percent: float
    co2_percent: float
    nh3_ratio: float
    device_id: uuid.UUID


class DailyReading(BaseModel):
    day: datetime.date
    avg_temp: float
    max_temp: float


BIN_COLUMNS = "id, name, location, country_code"

READING_COLUMNS = "timestamp, temperature, moisture_percent, o2_percent, co2_percent, nh3_ratio, device_id"

# days in /history start at midnight here, not in the database server's timezone
SITE_TIMEZONE = "Asia/Makassar"


def owned_bin(db, bin_id, user_id):
    """The user's bin, or None if it doesn't exist or belongs to someone else."""
    return db.execute(
        text(f"SELECT {BIN_COLUMNS} FROM bins WHERE id = :id AND user_id = :user_id"),
        {"id": bin_id, "user_id": user_id},
    ).first()


@router.get("", response_model=list[Bin])
def list_bins(user=Depends(current_user)):
    with db_engine.connect() as db:
        rows = db.execute(
            text(f"SELECT {BIN_COLUMNS} FROM bins WHERE user_id = :user_id ORDER BY created_at, name"),
            {"user_id": user.id},
        ).all()
    return [Bin(**row._mapping) for row in rows]


@router.post("", status_code=201, response_model=Bin)
def create_bin(body: BinCreate, user=Depends(current_user)):
    with db_engine.begin() as db:
        row = db.execute(
            text(
                f"""
                INSERT INTO bins (name, location, country_code, user_id)
                VALUES (:name, :location, :country_code, :user_id)
                RETURNING {BIN_COLUMNS}
                """
            ),
            {**body.model_dump(), "user_id": user.id},
        ).first()
    return Bin(**row._mapping)


@router.get("/{bin_id}", response_model = Bin)
def get_specific_bin(bin_id: uuid.UUID, user = Depends(current_user)):
    with db_engine.connect() as db:
        row = db.execute(
            text(
                """
                SELECT id, name, location, country_code FROM bins
                WHERE id = :id AND user_id = :user_id
                """
            ),
            {"id": bin_id, "user_id": user.id}
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="The bin with the given ID does not exist.")
    return Bin(**row._mapping)


@router.patch("/{bin_id}", response_model=Bin)
def change_bin(bin_id: uuid.UUID, body: BinChange, user = Depends(current_user)):
    changed_fields = body.model_dump(exclude_none=True)
    if changed_fields == {}:
        raise HTTPException(status_code=422, detail="No fields are being changed.")
    set_parts = []
    for column in changed_fields:
        set_parts.append(f"{column} = :{column}")
    set_query = ", ".join(set_parts)
    with db_engine.begin() as db:
        row = db.execute(
            text(
            f"""
            UPDATE bins
            SET {set_query}
            WHERE id = :id AND user_id = :user_id
            RETURNING id, name, location, country_code
            """),
            {"id": bin_id, "user_id": user.id, **changed_fields}
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="The bin with the given ID does not exist.")
    return Bin(**row._mapping)


@router.delete("/{bin_id}", status_code=204)
def delete_bin(bin_id: uuid.UUID, user = Depends(current_user)):
    with db_engine.begin() as db:
        row = db.execute(
            text(
                """
                DELETE from bins
                WHERE id = :bin_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"bin_id": bin_id, "user_id":user.id}
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="The bin with the given ID does not exist.")
        

@router.get("/{bin_id}/records", response_model=list[Reading])
def get_bin_records(bin_id: uuid.UUID, hours: Annotated[int, Query(ge=1, le=168)] = 24, user = Depends(current_user)):
    with db_engine.connect() as db:
        if owned_bin(db, bin_id, user.id) is None:
            raise HTTPException(status_code=404, detail="The bin with the given ID does not exist.")
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        results = db.execute(
            text(
                f"""
                SELECT {READING_COLUMNS} FROM records
                WHERE bin_id = :bin_id AND timestamp >= :since
                ORDER BY timestamp ASC
                """
            ),
            {"bin_id":bin_id, "since": since}
        ).all()
    return [Reading(**row._mapping) for row in results]


@router.get("/{bin_id}/history", response_model=list[DailyReading])
def get_bin_history(bin_id: uuid.UUID, days: Annotated[int, Query(ge=1, le=365)] = 30, user = Depends(current_user)):
    with db_engine.connect() as db:
        if owned_bin(db, bin_id, user.id) is None:
            raise HTTPException(status_code=404, detail="The bin with the given ID does not exist.")
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        results = db.execute(
            text(
                """
                SELECT (timestamp AT TIME ZONE :tz)::date AS day,
                    avg(temperature) AS avg_temp,
                    max(temperature) AS max_temp
                FROM records
                WHERE bin_id = :bin_id AND timestamp >= :since
                GROUP BY day
                ORDER BY day
                """
            ),
            {"bin_id":bin_id, "since": since, "tz": SITE_TIMEZONE}
        ).all()
    return [DailyReading(**row._mapping) for row in results]

