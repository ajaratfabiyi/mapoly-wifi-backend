from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Ledger, Plan, User
from app.schemas.history import PaymentHistoryOut

router = APIRouter(prefix="/api", tags=["history"])


def _method(entry: Ledger) -> str | None:
    if entry.reference.startswith("points_"):
        return "points"
    if entry.reference.startswith("admin_"):
        return "admin"
    return entry.payment_method


@router.get("/me/payments", response_model=list[PaymentHistoryOut])
def my_payments(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PaymentHistoryOut]:
    """The signed-in user's own payments, newest first. Never shows another account's rows."""
    rows = db.execute(
        select(Ledger, Plan.title)
        .join(Plan, Plan.id == Ledger.plan_id)
        .where(Ledger.user_id == user.id)
        .order_by(Ledger.id.desc())
        .limit(limit)
    ).all()
    return [
        PaymentHistoryOut(
            reference=entry.reference,
            plan_title=title,
            amount_naira=entry.amount_kobo // 100,
            status=entry.status,
            method=_method(entry),
            paid_at=entry.paid_at,
            created_at=entry.created_at,
            refunded_at=entry.refunded_at,
        )
        for entry, title in rows
    ]
