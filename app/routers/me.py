import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, UserDevice
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.schemas.me import MeResponse, ProfileUpdate
from app.services.access import any_device_has_access
from app.services.account import build_me
from app.services.expiry import expire_user_devices
from app.utils.time import utc_now

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me", response_model=MeResponse)
def get_me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> MeResponse:
    now = utc_now()
    # Ends any expired plan on the account before showing it. This replaces the expiry cron job.
    expire_user_devices(db, radius_db, r, user, now)
    return build_me(db, user, now)


@router.patch("/me", response_model=MeResponse)
def update_me(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    now = utc_now()

    if body.phone is not None:
        user.phone = body.phone or None

    if body.email is not None:
        new_email = body.email.strip().lower()
        if new_email != user.email:
            # RADIUS rows are keyed by email. Changing it mid-plan would orphan the
            # user's active access, so block it until the plan ends.
            devices = db.scalars(select(UserDevice).where(UserDevice.user_id == user.id)).all()
            if any_device_has_access(user, list(devices), now):
                raise HTTPException(
                    status_code=409,
                    detail="Email cannot be changed while your plan is active.",
                )
            taken = db.scalar(select(User.id).where(User.email == new_email))
            if taken is not None:
                raise HTTPException(status_code=409, detail="This email is already registered.")
            user.email = new_email

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email is already registered.")

    return build_me(db, user, now)
