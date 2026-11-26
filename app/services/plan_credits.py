"""Plan credits: paid plan slots on an account that no device has taken yet.

The plan's time starts at purchase and ends at a fixed time. A device bound before that
time takes one open slot and gets the plan until the same end time. Once the plan ends,
unused slots are gone. Does not commit; the caller commits with the binding.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccountPlanCredit, User, UserDevice


def claim_plan_credit(db: Session, user: User, device: UserDevice, now: datetime) -> bool:
    """Give a newly bound device one open plan slot. Returns False if none is left."""
    credit = db.scalar(
        select(AccountPlanCredit)
        .where(
            AccountPlanCredit.user_id == user.id,
            AccountPlanCredit.slots_left > 0,
            AccountPlanCredit.expires_at > now,
        )
        .order_by(AccountPlanCredit.created_at, AccountPlanCredit.id)
        .limit(1)
        .with_for_update()
    )
    if credit is None:
        return False
    device.access_expires_at = credit.expires_at
    device.expiry_handled_for = None
    credit.slots_left -= 1
    return True
