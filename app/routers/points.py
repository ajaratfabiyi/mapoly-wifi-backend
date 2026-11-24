import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Plan, User, UserDevice
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.schemas.points import RedeemRequest, RedeemResponse
from app.services.payments import cache_charge_status, grant_mac_for_charge
from app.services.points import InsufficientPoints, NotRedeemable, redeem_points_for_plan
from app.utils.time import utc_now

router = APIRouter(prefix="/api/points", tags=["points"])


@router.post("/redeem", response_model=RedeemResponse)
def redeem_points(
    body: RedeemRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> RedeemResponse:
    """Buy a plan with points, for one of the user's devices. No payment provider is involved."""
    if user.user_type == "gated" or user.access_type == "free":
        raise HTTPException(status_code=403, detail="Gated accounts do not buy plans.")

    plan = db.scalar(select(Plan).where(Plan.id == body.plan_id, Plan.is_active.is_(True)))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    device = db.get(UserDevice, body.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    if device.user_id != user.id:
        raise HTTPException(status_code=403, detail="This device belongs to another account.")

    now = utc_now()
    try:
        reference = redeem_points_for_plan(db, user, plan, device, now)
    except NotRedeemable:
        raise HTTPException(status_code=422, detail="This plan cannot be bought with points.")
    except InsufficientPoints:
        raise HTTPException(status_code=402, detail="Not enough points for this plan.")

    # The plan is paid and committed. Now let the device onto the network by MAC,
    # the same as a cash payment does.
    grant_mac_for_charge(db, radius_db, reference, now)
    cache_charge_status(r, db, reference, now)

    db.refresh(user)
    db.refresh(device)
    return RedeemResponse(
        status="success",
        reference=reference,
        points_balance=user.points,
        access_expires_at=device.access_expires_at,
    )
