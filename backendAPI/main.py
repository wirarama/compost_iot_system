import datetime
import hashlib

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import text

from database import db_engine
from routers import alerts, auth, bins, devices, pairing
from routers.alerts import record_alerts


class Record(BaseModel):
    timestamp: datetime.datetime
    temperature: float
    moisture_percent: float
    o2_percent: float
    co2_percent: float
    nh3_ratio: float

app = FastAPI()
app.include_router(auth.router)
app.include_router(pairing.router)
app.include_router(devices.router)
app.include_router(bins.router)
app.include_router(alerts.router)

api_key_header = APIKeyHeader(name = "x-key")

def authentication(api_key: str = Security(api_key_header)):
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    with db_engine.connect() as db:
        result = db.execute(text(
            """
            SELECT k.device_id FROM device_apikeys k
            JOIN devices d ON d.id = k.device_id
            WHERE k.api_key_hash = :key_hash AND k.revoked_at IS NULL AND d.is_active
            """
        ),
        {
            "key_hash": key_hash
        }
        )
        res = result.first()
    if res is None:
        raise HTTPException(status_code=401, detail="Authentication failed: invalid API key.")
    else:
        return res.device_id

@app.get("/")
def root():
    return {"message": "Hello World"}

# plain def: the database calls block, so FastAPI runs this in a thread pool
@app.post("/records")
def send_records (readings: list[Record], device_id: str = Depends(authentication)):
    # its own transaction, so it sticks even when the 409 below rolls the rest back:
    # the dashboard can see the device is in contact before it's set up
    with db_engine.begin() as db:
        db.execute(text("UPDATE devices SET last_seen_at = now() WHERE id = :device_id"), {"device_id": device_id})

    with db_engine.begin() as db:
        result = db.execute(text("""
                                SELECT bin_id FROM device_bin_assn
                                WHERE device_id = :device_id AND unassigned_at is NULL
                              """),
                              {
                                  "device_id":device_id
                              })
        res = result.first()

        if res is None:
            # paired, but not yet confirmed and given a bin in the dashboard;
            # the device waits and checks back
            raise HTTPException(status_code=409, detail="This device isn't set up yet. Finish setting it up in the dashboard.")
        else:
            bin_id = res.bin_id

        if readings:
            # before the insert: it compares the batch with what was stored before it.
            # same transaction: the readings and their incidents are saved together
            record_alerts(db, bin_id, readings)
            db.execute(text("""
                            INSERT INTO records (device_id, bin_id, timestamp,
                            temperature, moisture_percent, o2_percent, co2_percent, nh3_ratio)
                            VALUES (:device_id, :bin_id, :timestamp, :temperature,
                            :moisture_percent, :o2_percent, :co2_percent, :nh3_ratio)
                            """),
                [{"device_id": device_id, "bin_id": bin_id, **reading.model_dump()} for reading in readings])
    return readings
