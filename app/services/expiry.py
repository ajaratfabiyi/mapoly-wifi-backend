"""Ends a device's plan once it has passed its access_expires_at (Sections 4 and 7).

This runs when the account is used: on login and when the dashboard loads (no cron job).
The router disconnects an expired device by itself, through Session-Timeout, and FreeRADIUS
refuses new logins once the token's Expiration has passed. This module brings the
database and RADIUS records up to date.

For each expired device that has not yet been handled:
1. queue a CoA disconnect for its open session (the CoA worker on the AAA VM sends it)
2. delete that device's RADIUS rows (token, reply, group, MAC), so new logins are refused
3. clear that device's Redis status key and log an 'expired' event
4. record expiry_handled_for, so this expiry is not processed again

Expiry is per device. Other devices on the same account keep their own plans.
Free (gated) users never expire. Each device commits on its own, so a failure on one
device does not block the others; that device is retried the next time the account is used.
"""

import logging
from datetime import datetime

import redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import User, UserDevice
from app.services.audit import log_event
from app.services.coa_queue import enqueue_disconnect
from app.services.radius import device_username, remove_device_access

log = logging.getLogger(__name__)


def expire_device(
    db: Session,
    radius_db: Session,
    r: redis.Redis,
    device: UserDevice,
    now: datetime,
) -> bool:
    """Process one expired device. Returns True if it was handled, False if it will be retried."""
    try:
        user = device.user
        mac = device.mac_address
        enqueue_disconnect(db, "device_plan_expired", now, mac=mac)
        remove_device_access(radius_db, mac)
        r.delete(f"wifi:status:{device_username(user.email, mac)}")
        log_event(db, "expired", user.id, mac, None, now)
        device.expiry_handled_for = device.access_expires_at
        db.commit()
        return True
    except (SQLAlchemyError, redis.RedisError):
        db.rollback()
        radius_db.rollback()
        log.exception("Expiry failed for device %s; will retry", device.id)
        return False


def expire_user_devices(
    db: Session,
    radius_db: Session,
    r: redis.Redis,
    user: User,
    now: datetime,
) -> int:
    """Process every expired, unhandled device on this account. Returns how many were handled."""
    if user.access_type != "paid":
        return 0

    devices = db.scalars(
        select(UserDevice).where(
            UserDevice.user_id == user.id,
            UserDevice.access_expires_at.is_not(None),
            UserDevice.access_expires_at <= now,
        )
    ).all()

    handled = 0
    for device in devices:
        if device.expiry_handled_for == device.access_expires_at:
            continue
        if expire_device(db, radius_db, r, device, now):
            handled += 1
    return handled
