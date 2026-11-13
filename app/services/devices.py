"""Device binding rules.

Rules (backend document, Sections 4 and 11):
- Every user may have at most 2 devices (Section 11, item 5).
- A device is identified by its MAC address. A MAC belongs to at most one user.
- A MAC is bound to a user the first time it signs in with that user's credentials,
  if the user has a free slot.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User, UserDevice


class DeviceLimitReached(Exception):
    """The user already has the maximum number of devices."""


class DeviceOwnedByAnotherUser(Exception):
    """The MAC is already bound to a different account."""


def count_devices(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(UserDevice).where(UserDevice.user_id == user.id)
    ) or 0


def bind_device(
    db: Session,
    user: User,
    mac: str,
    ip: str | None,
    device_name: str | None,
    now: datetime,
) -> UserDevice:
    """Find or create this user's device for `mac`. Does not commit.

    Raises DeviceOwnedByAnotherUser or DeviceLimitReached; the caller maps these to 409.
    """
    existing = db.scalar(select(UserDevice).where(UserDevice.mac_address == mac))

    if existing is not None:
        if existing.user_id != user.id:
            raise DeviceOwnedByAnotherUser()
        existing.last_seen = now
        if ip:
            existing.ip_address = ip
        return existing

    # max_devices None means no limit.
    if user.max_devices is not None and count_devices(db, user) >= user.max_devices:
        raise DeviceLimitReached()

    device = UserDevice(
        user_id=user.id,
        mac_address=mac,
        device_name=device_name or None,
        ip_address=ip or None,
        bound_at=now,
        last_seen=now,
    )
    db.add(device)
    return device
