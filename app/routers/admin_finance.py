from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import Ledger, Plan, User, UserDevice
from app.radius_database import get_radius_db
from app.schemas.admin_tools import PaymentOut, PlanAdminOut, PlanPatch, ReportOut
from app.services.admin_log import record_admin_action
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])

PAYMENT_STATUSES = ("pending", "success", "failed")
REPLACE_NOT_NULL = ("title", "price_kobo", "points_award", "is_active")


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[PaymentOut]:
    if status is not None and status not in PAYMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status.")
    stmt = (
        select(Ledger, User.email, Plan.title)
        .join(User, User.id == Ledger.user_id)
        .join(Plan, Plan.id == Ledger.plan_id)
        .order_by(desc(Ledger.id)).limit(limit)
    )
    if status:
        stmt = stmt.where(Ledger.status == status)
    return [
        PaymentOut(
            reference=l.reference, email=email, plan_title=title,
            amount_naira=(l.amount_kobo or 0) // 100, status=l.status,
            payment_method=l.payment_method, paid_at=l.paid_at,
            refunded_at=l.refunded_at, created_at=l.created_at,
        )
        for l, email, title in db.execute(stmt).all()
    ]


@router.post("/payments/{reference}/refund", status_code=204)
def mark_refunded(
    reference: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Record that a payment has been sent back. The money itself is returned through Paystack."""
    ledger = db.scalar(select(Ledger).where(Ledger.reference == reference))
    if ledger is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if ledger.status != "success":
        raise HTTPException(status_code=422, detail="Only successful payments can be marked refunded.")
    if ledger.refunded_at is not None:
        raise HTTPException(status_code=409, detail="Already marked refunded.")
    now = utc_now()
    ledger.refunded_at = now
    record_admin_action(db, admin, "mark_refunded", ledger.user_id, reference, now)
    db.commit()
    return Response(status_code=204)


@router.get("/plans", response_model=list[PlanAdminOut])
def list_plans_admin(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[PlanAdminOut]:
    plans = db.scalars(select(Plan).order_by(Plan.duration_hours)).all()
    return [PlanAdminOut(id=p.id, title=p.title, duration_hours=p.duration_hours,
                         price_naira=p.price_kobo // 100, speed_limit_mbps=p.speed_limit_mbps,
                         points_award=p.points_award, points_cost=p.points_cost,
                         is_active=bool(p.is_active))
            for p in plans]


@router.patch("/plans/{plan_id}", response_model=PlanAdminOut)
def update_plan(
    plan_id: str,
    body: PlanPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PlanAdminOut:
    """Change a plan's price, points or on-sale status. Existing purchases are not affected."""
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    for field in REPLACE_NOT_NULL:
        if field in body.model_fields_set and getattr(body, field) is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty.")

    changes = []
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "price_kobo":
            changes.append(f"price {plan.price_kobo // 100} -> {value // 100}")
            plan.price_kobo = value
        elif field == "title":
            plan.title = value.strip()
            changes.append("title")
        elif field == "speed_limit_mbps":
            plan.speed_limit_mbps = value
            changes.append("speed")
        elif field == "points_award":
            changes.append(f"earns {plan.points_award} -> {value}")
            plan.points_award = value
        elif field == "points_cost":
            changes.append(f"costs {plan.points_cost} -> {value}")
            plan.points_cost = value
        elif field == "is_active":
            plan.is_active = value
            changes.append("on sale" if value else "off sale")
    if changes:
        record_admin_action(db, admin, "update_plan", None, f"{plan.id}: " + ", ".join(changes), utc_now())
    db.commit()
    return PlanAdminOut(id=plan.id, title=plan.title, duration_hours=plan.duration_hours,
                        price_naira=plan.price_kobo // 100, speed_limit_mbps=plan.speed_limit_mbps,
                        points_award=plan.points_award, points_cost=plan.points_cost,
                        is_active=bool(plan.is_active))


@router.get("/reports/summary", response_model=ReportOut)
def report_summary(
    days: int = Query(default=7, ge=1, le=366),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> ReportOut:
    now = utc_now()
    since = now - timedelta(days=days)

    paid = db.execute(
        select(func.count(), func.coalesce(func.sum(Ledger.amount_kobo), 0))
        .where(Ledger.status == "success", Ledger.amount_kobo > 0, Ledger.paid_at >= since)
    ).one()
    new_users = db.scalar(select(func.count()).select_from(User).where(User.created_at >= since))
    total_users = db.scalar(select(func.count()).select_from(User))
    gated = db.scalar(select(func.count()).select_from(User).where(User.user_type == "gated"))
    seen = db.scalar(select(func.count()).select_from(UserDevice)
                     .where(UserDevice.last_seen >= now - timedelta(minutes=15)))
    connected = radius_db.execute(text(
        "SELECT COUNT(*) FROM radacct WHERE acctstoptime IS NULL"
    )).scalar()

    return ReportOut(
        days=days,
        payments_count=int(paid[0]),
        payments_naira_total=int(paid[1]) // 100,
        new_users=int(new_users or 0),
        total_users=int(total_users or 0),
        gated_users=int(gated or 0),
        devices_seen_15_min=int(seen or 0),
        devices_connected_now=int(connected or 0),
    )
