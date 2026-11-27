"""CoA worker (runs on the AAA VM, which is on the campus network).

The backend queues disconnects in coa_queue. This worker sends them to the router.
The router's address is private, so only something on the campus network can reach it.

Each run, for every pending row:
1. Find the open sessions in radacct that the row affects.
2. Send a Disconnect-Request for each one.
3. If every session is acknowledged, or there are none, mark the row sent.
4. Otherwise record the attempt and retry later, with a growing delay.

Rows are never deleted, so the queue doubles as an audit trail.

Required environment on the AAA VM: DATABASE_URL (the queue),
RADIUS_DATABASE_URL (radacct), COA_NAS_HOST, RADIUS_COA_SECRET.

Run every minute from cron on the AAA VM:
    * * * * * cd /opt/mapoly-wifi && python -m app.jobs.coa_worker
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import CoaQueue
from app.radius_database import RadiusSessionLocal
from app.services.coa import send_disconnect
from app.services.coa_queue import DEVICE_REASONS
from app.services.radius import ACCOUNT_MATCH, account_match_params
from app.services.routeros_api import RouterApiError, end_hotspot_sessions
from app.utils.mac import normalise_mac
from app.utils.time import utc_now

log = logging.getLogger(__name__)

BATCH_SIZE = 50
MAX_BACKOFF_MINUTES = 30


def pending_rows(db: Session, now: datetime) -> list[CoaQueue]:
    return list(
        db.scalars(
            select(CoaQueue)
            .where(
                CoaQueue.sent_at.is_(None),
                or_(CoaQueue.next_attempt_at.is_(None), CoaQueue.next_attempt_at <= now),
            )
            .order_by(CoaQueue.id)
            .limit(BATCH_SIZE)
        ).all()
    )


def open_sessions(radius_db: Session, row: CoaQueue) -> list[tuple[str, str]]:
    """(username, Calling-Station-Id) for every open session this row affects."""
    if row.reason in ("device_removed", "device_plan_expired"):
        rows = radius_db.execute(
            text(
                "SELECT username, callingstationid FROM radacct "
                "WHERE acctstoptime IS NULL AND callingstationid = :m"
            ),
            # coa_queue stores the MAC with dashes; radacct stores Calling-Station-Id with colons.
            {"m": normalise_mac(row.mac_address)},
        ).all()
    else:
        rows = radius_db.execute(
            text(
                "SELECT username, callingstationid FROM radacct "
                f"WHERE acctstoptime IS NULL AND {ACCOUNT_MATCH}"
            ),
            account_match_params(row.username),
        ).all()
    return [(user, station) for user, station in rows]


def process_row(
    db: Session,
    radius_db: Session,
    row: CoaQueue,
    host: str,
    secret: str,
    now: datetime,
    router_end: Callable[[str], None] | None = None,
) -> bool:
    """Send the disconnects for one row. Returns True when the row is done.

    router_end(mac) ends the hotspot session on the router. It is used for device rows only.
    """
    sessions = open_sessions(radius_db, row)
    failed = []
    for username, station in sessions:
        if not send_disconnect(host, secret, username, station):
            failed.append(station)

    # RADIUS may have no record of the session. Ask the router directly as well.
    if router_end is not None and row.mac_address and row.reason in DEVICE_REASONS:
        try:
            router_end(row.mac_address)
        except (OSError, RouterApiError) as exc:
            failed.append(row.mac_address)
            log.warning("router API for %s failed: %s", row.mac_address, exc)

    if not failed:
        row.sent_at = now
        row.last_error = None
        db.commit()
        return True

    row.attempts += 1
    delay = min(row.attempts, MAX_BACKOFF_MINUTES)
    row.next_attempt_at = now + timedelta(minutes=delay)
    row.last_error = f"no CoA ACK for {len(failed)} session(s)"[:255]
    db.commit()
    log.warning("coa row %s: %s; retry in %d min", row.id, row.last_error, delay)
    return False


def run(now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    if not settings.COA_NAS_HOST or not settings.RADIUS_COA_SECRET:
        raise RuntimeError("COA_NAS_HOST and RADIUS_COA_SECRET must be set on the AAA VM")

    router_end = None
    if settings.ROUTER_API_HOST and settings.ROUTER_API_PASSWORD:
        def router_end(mac: str) -> None:
            end_hotspot_sessions(
                settings.ROUTER_API_HOST, settings.ROUTER_API_PORT,
                settings.ROUTER_API_USER, settings.ROUTER_API_PASSWORD, mac,
            )

    now = now or utc_now()
    db = SessionLocal()
    radius_db = RadiusSessionLocal()
    done = retry = 0
    try:
        for row in pending_rows(db, now):
            try:
                if process_row(db, radius_db, row, settings.COA_NAS_HOST, settings.RADIUS_COA_SECRET, now,
                               router_end=router_end):
                    done += 1
                else:
                    retry += 1
            except SQLAlchemyError:
                db.rollback()
                radius_db.rollback()
                log.exception("coa row %s failed; will retry", row.id)
                retry += 1
    finally:
        db.close()
        radius_db.close()
    result = {"sent": done, "retry": retry}
    log.info("coa worker: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
