"""
Alerts: incidents where a bin's readings go out of range, and silent devices.

POST /records (main.py) calls record_alerts: an incident is opened when a
rule is first broken and resolved when the readings are back in range, so
each incident is one row in bin_events with how long it lasted.

A device that has gone quiet sends nothing, so ingestion can't notice it;
GET /alerts works out offline devices live instead.

Known limitations: incidents are per bin, so two sensors in one bin that
disagree (one too hot, one fine) can open and resolve the same incident in
turns; and offline periods are shown live but not stored.
"""
import datetime
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from database import db_engine
from routers.auth import current_user

router = APIRouter(prefix="/alerts", tags=["alerts"])

# thresholds from composting science (CLAUDE.md, D3 domain model)
MAX_TEMPERATURE = 70      # °C, above this even useful microbes die
MIN_MOISTURE = 40         # %, below this the pile dries out and stalls
MAX_MOISTURE = 60         # %, above this the pile goes anaerobic
# a set-up device quiet for this long counts as offline
OFFLINE_AFTER = datetime.timedelta(hours=1)

SEVERITY = {"too_hot": "high", "too_dry": "medium", "too_wet": "medium"}

# history is newest first, and capped so one request stays small
HISTORY_LIMIT = 500


class Alert(BaseModel):
    # None for offline alerts, which are worked out live and not stored
    id: uuid.UUID | None
    bin_id: uuid.UUID
    bin_name: str | None
    type: str
    severity: str
    triggered_at: datetime.datetime
    # None while the incident is still going
    resolved_at: datetime.datetime | None
    # only set for offline alerts: which device went quiet
    device_id: uuid.UUID | None = None
    device_name: str | None = None


def broken_rules(reading):
    """The alert types this reading triggers, e.g. {"too_hot"}; empty if all is well."""
    types = set()
    if reading.temperature > MAX_TEMPERATURE:
        types.add("too_hot")
    if reading.moisture_percent < MIN_MOISTURE:
        types.add("too_dry")
    if reading.moisture_percent > MAX_MOISTURE:
        types.add("too_wet")
    return types


def record_alerts(db, bin_id, readings):
    """Open and resolve the bin's incidents for a batch of readings.

    Runs inside POST /records' transaction, before the batch is inserted, so
    readings and incidents are saved together or not at all.
    """
    # looked up once per upload; kept up to date as the batch is walked
    open_types = set(db.execute(
        text("SELECT type FROM bin_events WHERE bin_id = :bin_id AND resolved_at IS NULL"),
        {"bin_id": bin_id},
    ).scalars())

    # a device uploading a backlog late must not rewrite incidents that newer
    # readings already decided, e.g. resolve one before it was triggered
    latest_stored = db.execute(
        text("SELECT max(timestamp) FROM records WHERE bin_id = :bin_id"),
        {"bin_id": bin_id},
    ).scalar()

    # a batch can arrive out of order; incidents must follow the readings' time
    for reading in sorted(readings, key=lambda r: r.timestamp):
        if latest_stored is not None and reading.timestamp <= latest_stored:
            continue
        broken = broken_rules(reading)
        for alert_type in SEVERITY:
            if alert_type in broken and alert_type not in open_types:
                db.execute(
                    text(
                        """
                        INSERT INTO bin_events (bin_id, type, severity, triggered_at)
                        VALUES (:bin_id, :type, :severity, :at)
                        """
                    ),
                    {"bin_id": bin_id, "type": alert_type, "severity": SEVERITY[alert_type], "at": reading.timestamp},
                )
                open_types.add(alert_type)
            elif alert_type not in broken and alert_type in open_types:
                db.execute(
                    text(
                        """
                        UPDATE bin_events SET resolved_at = :at
                        WHERE bin_id = :bin_id AND type = :type AND resolved_at IS NULL
                        """
                    ),
                    {"bin_id": bin_id, "type": alert_type, "at": reading.timestamp},
                )
                open_types.discard(alert_type)


@router.get("", response_model=list[Alert])
def list_alerts(bin_id: uuid.UUID | None = None, include_resolved: bool = False, user=Depends(current_user)):
    """The user's alerts, newest first: open ones, or the history with include_resolved."""
    # fixed strings only; the values go in as parameters
    conditions = ["b.user_id = :user_id"]
    if not include_resolved:
        conditions.append("e.resolved_at IS NULL")
    if bin_id is not None:
        conditions.append("e.bin_id = :bin_id")
    params = {"user_id": user.id, "bin_id": bin_id, "limit": HISTORY_LIMIT}
    cutoff = datetime.datetime.now(datetime.timezone.utc) - OFFLINE_AFTER

    with db_engine.connect() as db:
        events = db.execute(
            text(
                f"""
                SELECT e.id, e.bin_id, b.name AS bin_name, e.type, e.severity, e.triggered_at, e.resolved_at
                FROM bin_events e
                JOIN bins b ON b.id = e.bin_id
                WHERE {" AND ".join(conditions)}
                ORDER BY e.triggered_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).all()

        # set-up devices (active, in a bin) that haven't been heard from;
        # one that never sent anything counts from when it was paired
        quiet = db.execute(
            text(
                f"""
                SELECT d.id AS device_id, d.name AS device_name, b.id AS bin_id, b.name AS bin_name,
                       coalesce(d.last_seen_at, d.paired_at) AS last_contact
                FROM devices d
                JOIN device_bin_assn a ON a.device_id = d.id AND a.unassigned_at IS NULL
                JOIN bins b ON b.id = a.bin_id
                WHERE d.owner_id = :user_id AND d.is_active
                  AND coalesce(d.last_seen_at, d.paired_at) < :cutoff
                  {"AND b.id = :bin_id" if bin_id is not None else ""}
                """
            ),
            {"user_id": user.id, "bin_id": bin_id, "cutoff": cutoff},
        ).all()

    alerts = [Alert(**row._mapping) for row in events]
    alerts += [
        Alert(
            id=None,
            bin_id=row.bin_id,
            bin_name=row.bin_name,
            type="offline",
            severity="medium",
            triggered_at=row.last_contact + OFFLINE_AFTER,
            resolved_at=None,
            device_id=row.device_id,
            device_name=row.device_name,
        )
        for row in quiet
    ]
    alerts.sort(key=lambda alert: alert.triggered_at, reverse=True)
    return alerts
