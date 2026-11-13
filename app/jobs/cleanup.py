"""Cleanup job (Section 8 and the draft's job list). Run every minute.

1. Delete login tokens past their expiry: the radcheck row first, then the tracking row.
   If the RADIUS delete fails, the tracking row stays and the next run retries.
2. Delete sessions_log rows older than 90 days.

Render: a Cron Job service with schedule "* * * * *" and command
    python -m app.jobs.cleanup
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import LoginToken, SessionLog
from app.radius_database import RadiusSessionLocal
from app.utils.time import utc_now

AUDIT_RETENTION = timedelta(days=90)
log = logging.getLogger(__name__)


def delete_expired_tokens(db: Session, radius_db: Session, now: datetime) -> int:
    expired = db.scalars(select(LoginToken).where(LoginToken.expires_at < now)).all()
    if not expired:
        return 0
    tokens = [t.token for t in expired]

    radius_db.execute(
        text(
            "DELETE FROM radcheck "
            "WHERE attribute = 'Cleartext-Password' AND value IN :tokens"
        ).bindparams(bindparam("tokens", expanding=True)),
        {"tokens": tokens},
    )
    radius_db.commit()

    db.execute(delete(LoginToken).where(LoginToken.token.in_(tokens)))
    db.commit()
    return len(tokens)


def prune_audit(db: Session, now: datetime) -> int:
    cutoff = now - AUDIT_RETENTION
    result = db.execute(delete(SessionLog).where(SessionLog.created_at < cutoff))
    db.commit()
    return result.rowcount or 0


def run(now: datetime | None = None) -> dict[str, int]:
    now = now or utc_now()
    db = SessionLocal()
    radius_db = RadiusSessionLocal()
    try:
        tokens = delete_expired_tokens(db, radius_db, now)
        pruned = prune_audit(db, now)
    finally:
        db.close()
        radius_db.close()
    result = {"expired_tokens_removed": tokens, "audit_rows_pruned": pruned}
    log.info("cleanup: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
