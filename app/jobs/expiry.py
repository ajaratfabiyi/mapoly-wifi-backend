"""Optional manual expiry run: python -m app.jobs.expiry

Normally there is no need to run this. Plans end on login and dashboard visits
(app/services/expiry.py). This command ends every expired plan at once, for example
to bring the records up to date after a long period without use.
"""

import logging
from datetime import datetime

import redis
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User, UserDevice
from app.radius_database import RadiusSessionLocal
from app.redis_client import get_redis
from app.services.expiry import expire_device
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
        if expire_device(db, radius_db, r, device, now):
            handled += 1
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
