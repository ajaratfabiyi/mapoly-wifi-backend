"""Points: earned on cash purchases, spent to buy plans (1 point = ₦1).

Every change to a balance is written to points_entries in the same transaction as the
balance itself, so the balance can always be explained by its entries.
"""

import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ledger, Plan, PointsEntry, User, UserDevice
from app.services.plan_devices import apply_plan_to_devices


class InsufficientPoints(Exception):
    pass


class NotRedeemable(Exception):
    """The plan has no points price, so points cannot buy it."""


def _record(db: Session, user: User, delta: int, reason: str, reference: str, now: datetime) -> None:
    user.points = (user.points or 0) + delta
    db.add(
        PointsEntry(
            user_id=user.id,
            delta=delta,
            balance_after=user.points,
            reason=reason,
            reference=reference,
            created_at=now,
        )
    )


def award_purchase_points(db: Session, reference: str, plan: Plan, now: datetime) -> int:
    """Credit the points a cash purchase earns. Call inside the transaction that marks the
    charge successful, so the points are given once. Returns the points awarded."""
    if plan.points_award <= 0:
        return 0
    ledger = db.scalar(select(Ledger).where(Ledger.reference == reference))
    user = db.scalar(select(User).where(User.id == ledger.user_id).with_for_update())
    _record(db, user, plan.points_award, "purchase", reference, now)
    return plan.points_award


def redeem_points_for_plan(
    db: Session, user: User, plan: Plan, device: UserDevice | None, now: datetime
) -> str:
    """Spend points on a plan for one device. Returns the ledger reference.

    Locks the user row, so two redemptions at once cannot both spend the same points.
    The caller grants RADIUS access after this returns.
    """
    if plan.points_cost is None:
        raise NotRedeemable()

    locked = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if (locked.points or 0) < plan.points_cost:
        raise InsufficientPoints()

    reference = f"points_{now:%Y%m%d}_{secrets.token_hex(4)}"
    db.add(
        Ledger(
            reference=reference,
            user_id=locked.id,
            device_id=device.id if device else None,
            plan_id=plan.id,
            amount_kobo=plan.price_kobo,
            payment_method=None,  # Not paid in cash; the points entry below records it.
            status="success",
            paid_at=now,
            created_at=now,
        )
    )
    _record(db, locked, -plan.points_cost, "redeem", reference, now)
    apply_plan_to_devices(db, locked, device, plan, now)
    db.commit()
    return reference
