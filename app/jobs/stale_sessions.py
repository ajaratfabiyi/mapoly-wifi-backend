"""Stale session cleanup (radius/README.md, known limits).

A session counts against Simultaneous-Use until FreeRADIUS sees its Stop. If MikroTik
never sends the Stop (reboot, power cut, lost packet), the row stays open and that
device is refused until the row is closed.

This job closes open rows that have had no interim update for STALE_SESSION_MINUTES.
MikroTik sends interim updates every 5 minutes, so a live session always updates.
Time comes from the database clock, not the app, so it matches FreeRADIUS's timestamps.

Render: a Cron Job service with schedule "*/5 * * * *" and command
    python -m app.jobs.stale_sessions
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.radius_database import RadiusSessionLocal

log = logging.getLogger(__name__)

CLOSE_CAUSE = "Lost-Service"


def close_stale_sessions(radius_db: Session, stale_minutes: int) -> int:
    """Close open sessions with no update for `stale_minutes`. Returns the count closed."""
    result = radius_db.execute(
        text(
            """
            UPDATE radacct
            SET acctstoptime = NOW(),
                acctsessiontime = TIMESTAMPDIFF(
                    SECOND, acctstarttime, NOW()
                ),
                acctterminatecause = :cause
            WHERE acctstoptime IS NULL
              AND COALESCE(acctupdatetime, acctstarttime)
                  < NOW() - INTERVAL :minutes MINUTE
            """
        ),
        {"cause": CLOSE_CAUSE, "minutes": stale_minutes},
    )
    radius_db.commit()
    return result.rowcount or 0


def run(stale_minutes: int | None = None) -> int:
    minutes = stale_minutes if stale_minutes is not None else get_settings().STALE_SESSION_MINUTES
    radius_db = RadiusSessionLocal()
    try:
        closed = close_stale_sessions(radius_db, minutes)
    finally:
        radius_db.close()
    log.info("stale sessions closed: %d", closed)
    return closed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
