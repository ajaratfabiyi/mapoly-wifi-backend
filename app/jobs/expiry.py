"""Expiry job (Sections 4 and 7). Run every minute.

For each device whose plan has passed its access_expires_at and has not yet been handled:
1. queue a CoA disconnect for that device's open session (the CoA worker sends it)
2. delete that device's RADIUS rows (token, reply, group, MAC), so new logins are refused
3. clear that device's Redis status key and log an 'expired' event
4. record expiry_handled_for, so this expiry is not processed again

Expiry is per device. Other devices on the same account keep their own plans.
Each device commits on its own, so one failure does not block the others, and the
failed device is retried on the next run. Free (gated) users never expire.

Render: a Cron Job service with schedule "* * * * *" and command
    python -m app.jobs.expiry
"""

import logging
from datetime import datetime

import redis
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User, UserDevice
from app.radius_database import RadiusSessionLocal
from app.redis_client import get_redis
from app.services.audit import log_event
from app.services.coa_queue import enqueue_disconnect
from app.services.radius import device_username, remove_device_access
from app.utils.time import utc_now

log = logging.getLogger(__name__)


def devices_to_expire(db: Session, now: datetime) -> list[UserDevice]:
    return list(
        db.scalars(
            select(UserDevice)
            .join(User, User.id == UserDevice.user_id)
            .where(
                User.access_type == "paid",
                UserDevice.access_expires_at.is_not(None),
                UserDevice.access_expires_at <= now,
                or_(
                    UserDevice.expiry_handled_for.is_(None),
                    UserDevice.expiry_handled_for != UserDevice.access_expires_at,
                ),
            )
        ).all()
    )


def expire_plans(
    db: Session,
    radius_db: Session,
    r: redis.Redis,
    now: datetime,
) -> int:
    handled = 0
    for device in devices_to_expire(db, now):
        try:
            user = device.user
            mac = device.mac_address
            enqueue_disconnect(db, "device_plan_expired", now, mac=mac)
            remove_device_access(radius_db, mac)
            r.delete(f"wifi:status:{device_username(user.email, mac)}")
            log_event(db, "expired", user.id, mac, None, now)
            device.expiry_handled_for = device.access_expires_at
            db.commit()
            handled += 1
        except (SQLAlchemyError, redis.RedisError):
            db.rollback()
            radius_db.rollback()
            log.exception("Expiry failed for device %s; will retry", device.id)
    return handled


def run(now: datetime | None = None) -> int:
    now = now or utc_now()
    db = SessionLocal()
    radius_db = RadiusSessionLocal()
    try:
        handled = expire_plans(db, radius_db, get_redis(), now)
    finally:
        db.close()
        radius_db.close()
    log.info("expiry: %d device plan(s) ended", handled)
    return handled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
