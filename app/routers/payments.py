import secrets

import redis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.radius_database import get_radius_db
from app.models import Ledger, Plan, User, UserDevice
from app.redis_client import get_redis
from app.schemas.payments import InitializeRequest, InitializeResponse, VerifyResponse
from app.services.payments import (
    AMOUNT_MISMATCH,
    apply_successful_charge,
    cache_charge_status,
    grant_mac_for_charge,
)
from app.services.paystack import PaystackError, initialize_transaction, verify_transaction
from app.utils.paystack_time import parse_paystack_time
from app.utils.time import utc_now

router = APIRouter(prefix="/api/payments", tags=["payments"])

PAYSTACK_UNAVAILABLE = "Payment service is unavailable. Try again."


def _new_reference(now) -> str:
    return f"mapoly_{now:%Y%m%d}_{secrets.token_hex(4)}"


@router.post("/initialize", response_model=InitializeResponse)
def initialize_payment(
    body: InitializeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InitializeResponse:
    if user.user_type == "gated" or user.access_type == "free":
        raise HTTPException(status_code=403, detail="Gated accounts do not buy plans.")

    plan = db.scalar(select(Plan).where(Plan.id == body.plan_id, Plan.is_active.is_(True)))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")

    # The device is optional. Without one, the plan goes on the account and links to devices
    # by priority, and to any device bound later.
    device = None
    if body.device_id is not None:
        device = db.get(UserDevice, body.device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found.")
        if device.user_id != user.id:
            raise HTTPException(status_code=403, detail="This device belongs to another account.")

    now = utc_now()
    reference = _new_reference(now)
    db.add(
        Ledger(
            reference=reference,
            user_id=user.id,
            device_id=device.id if device else None,
            plan_id=plan.id,
            amount_kobo=plan.price_kobo,
            status="pending",
            created_at=now,
        )
    )
    db.commit()

    try:
        data = initialize_transaction(
            email=user.email,
            amount_kobo=plan.price_kobo,
            reference=reference,
        )
    except PaystackError:
        # The pending row stays. Verify or a retry can settle it later.
        raise HTTPException(status_code=502, detail=PAYSTACK_UNAVAILABLE)

    return InitializeResponse(
        reference=data["reference"],
        authorization_url=data["authorization_url"],
        access_code=data["access_code"],
    )


@router.get("/verify/{reference}", response_model=VerifyResponse)
def verify_payment(
    reference: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> VerifyResponse:
    """Fallback for when the webhook has not arrived. Asks Paystack directly.
    The redirect alone is never trusted (Section 5.10)."""
    ledger = db.scalar(select(Ledger).where(Ledger.reference == reference))
    if ledger is None or ledger.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found.")

    now = utc_now()
    if ledger.status != "success":
        try:
            data = verify_transaction(reference)
        except PaystackError:
            raise HTTPException(status_code=502, detail=PAYSTACK_UNAVAILABLE)

        outcome = data.get("status")
        if outcome == "success":
            result = apply_successful_charge(
                db,
                reference,
                # requested_amount is the plan price; amount includes the Paystack fee
                # the customer paid, so comparing amount would reject every charge.
                int(data.get("requested_amount") or data.get("amount", -1)),
                parse_paystack_time(data.get("paid_at"), now),
                now,
            )
            if result == AMOUNT_MISMATCH:
                db.refresh(ledger)
        elif outcome == "failed" and ledger.status == "pending":
            ledger.status = "failed"
            db.commit()

    db.refresh(ledger)

    if ledger.status == "success":
        grant_mac_for_charge(db, radius_db, reference, now)
        cache_charge_status(r, db, reference, now)
        device = db.get(UserDevice, ledger.device_id) if ledger.device_id is not None else None
        return VerifyResponse(
            status="success",
            access_expires_at=device.access_expires_at if device else None,
        )
    if ledger.status == "failed":
        return VerifyResponse(status="failed", access_expires_at=None)
    return VerifyResponse(status="pending", access_expires_at=None)
