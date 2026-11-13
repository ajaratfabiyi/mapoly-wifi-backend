"""Queue disconnects for the CoA worker (see app/jobs/coa_worker.py).

The backend only writes here. It never contacts the router.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import CoaQueue
from app.utils.mac import to_radius_format

REASONS = ("plan_expired", "device_removed", "device_plan_expired", "account_disabled")

# These reasons disconnect one device, identified by MAC.
DEVICE_REASONS = ("device_removed", "device_plan_expired")


def enqueue_disconnect(
    db: Session,
    reason: str,
    now: datetime,
    username: str | None = None,
    mac: str | None = None,
) -> CoaQueue:
    """Add a row. Does not commit: the caller commits it with its own change."""
    if reason not in REASONS:
        raise ValueError(f"unknown CoA reason: {reason}")
    if reason in DEVICE_REASONS and not mac:
        raise ValueError(f"{reason} needs a MAC")
    if reason not in DEVICE_REASONS and not username:
        raise ValueError(f"{reason} needs a username")

    row = CoaQueue(
        reason=reason,
        username=username,
        mac_address=to_radius_format(mac) if mac else None,
        created_at=now,
        attempts=0,
    )
    db.add(row)
    return row
