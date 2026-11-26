"""Which devices a plan purchase covers.

A purchase covers up to plan.device_count devices, in this order:
1. The device named on the purchase, if the buyer named one.
2. The account's other devices that have no active plan, by priority (set by the student;
   devices without a priority come last), then oldest first.
3. Any slots still open are kept on the account as a plan credit until the plan ends. A
   device bound before then takes one slot and gets the plan until the same end time.

The plan's time starts at purchase, whether or not a device is bound. Every device covered
gets the same end time. Used by every way a plan is bought: card payment, points, and an
admin grant.
"""

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import AccountPlanCredit, Plan, User, UserDevice
from app.services.access import extend_device_access


def apply_plan_to_devices(
    db: Session, user: User, device: UserDevice | None, plan: Plan, now: datetime
) -> list[UserDevice]:
    """Cover devices now and keep the rest as credit. Returns the devices covered now.

    Does not commit. The caller grants RADIUS access for the covered devices afterwards.
    """
    slots = max(plan.device_count or 1, 1)
    covered: list[UserDevice] = []
    plan_ends = now + timedelta(hours=plan.duration_hours)
    if device is not None:
        expiry = extend_device_access(device, plan.duration_hours, now)
        covered.append(device)
    else:
        expiry = plan_ends

    room = slots - len(covered)
    if room > 0:
        query = select(UserDevice).where(
            UserDevice.user_id == user.id,
            or_(UserDevice.access_expires_at.is_(None), UserDevice.access_expires_at <= now),
        )
        if device is not None:
            query = query.where(UserDevice.id != device.id)
        extras = db.scalars(
            query.order_by(
                UserDevice.priority.asc().nulls_last(), UserDevice.bound_at, UserDevice.id
            ).limit(room)
        ).all()
        for extra in extras:
            extra.access_expires_at = expiry
            extra.expiry_handled_for = None
        covered.extend(extras)

    unused = slots - len(covered)
    if unused > 0:
        db.add(
            AccountPlanCredit(
                user_id=user.id,
                plan_id=plan.id,
                expires_at=plan_ends,
                slots_left=unused,
                created_at=now,
            )
        )
    return covered
