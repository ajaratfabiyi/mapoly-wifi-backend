from datetime import datetime

from sqlalchemy.orm import Session

from app.models import SessionLog


def log_event(
    db: Session,
    event: str,
    user_id: int | None,
    mac: str | None,
    ip: str | None,
    now: datetime,
) -> None:
    """Add a sessions_log row. The caller commits."""
    db.add(
        SessionLog(
            user_id=user_id,
            mac_address=mac,
            ip_address=ip,
            event=event,
            created_at=now,
        )
    )
