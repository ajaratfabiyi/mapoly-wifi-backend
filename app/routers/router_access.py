"""Gives a paid device its router login straight after payment, without a password.

The dashboard session cookie identifies the user. The device must belong to that user
and have an active paid plan. The portal calls this after a successful payment, then
sends the device to the router with the returned username and token.
"""

import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, UserDevice
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.services import access
from app.services.login_tokens import issue_token
from app.services.radius import close_device_sessions, device_username
from app.utils.time import utc_now

router = APIRouter(prefix="/api/devices", tags=["devices"])

NO_PLAN = "This device has no active plan."
UNAVAILABLE = "The login service is temporarily unavailable."


@router.post("/{device_id}/router-token")
def router_token(
    device_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> dict:
    device = db.get(UserDevice, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="Device not found.")

    now = utc_now()
    if user.access_type != "paid" or not access.has_device_access(user, device, now):
        raise HTTPException(status_code=403, detail=NO_PLAN)

    settings = get_settings()
    username = device_username(user.email, device.mac_address)
    try:
        # Same as a normal login: a stuck session on this device must not block the new one.
        close_device_sessions(radius_db, device.mac_address, now)
        token = issue_token(
            db=db,
            radius_db=radius_db,
            r=r,
            now=now,
            username=username,
            user_type=user.user_type,
            mac=device.mac_address,
            session_seconds=access.device_seconds_left(user, device, now),
            ttl_seconds=settings.LOGIN_TOKEN_TTL_SECONDS,
        )
    except (SQLAlchemyError, redis.RedisError):
        db.rollback()
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    return {
        "username": username,
        "token": token,
        "expires_in": settings.LOGIN_TOKEN_TTL_SECONDS,
    }
