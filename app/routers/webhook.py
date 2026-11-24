import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from app.database import SessionLocal
from app.radius_database import RadiusSessionLocal
from app.redis_client import get_redis
from app.services.payments import (
    ALREADY_APPLIED,
    APPLIED,
    apply_successful_charge,
    cache_charge_status,
    grant_mac_for_charge,
)
from app.services.paystack import verify_webhook_signature
from app.utils.paystack_time import parse_paystack_time
from app.utils.time import utc_now

router = APIRouter(prefix="/api/v1/payment", tags=["payments"])
log = logging.getLogger(__name__)


@router.post("/webhook-listener")
async def webhook_listener(request: Request, background: BackgroundTasks) -> dict:
    """Section 6. The HMAC is checked on the raw bytes. We answer 200 at once and
    apply the charge in the background, so Paystack does not time out and retry."""
    raw = await request.body()
    if not verify_webhook_signature(raw, request.headers.get("x-paystack-signature")):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed body.")

    if event.get("event") == "charge.success":
        background.add_task(process_charge, event.get("data") or {})
    return {"ok": True}


def process_charge(data: dict) -> None:
    """Runs after the response, so it opens its own session.

    A failure here is logged, not retried by Paystack, because we already answered 200.
    The verify endpoint and a periodic reconcile are the recovery paths.
    """
    reference = data.get("reference")
    if not reference:
        return

    now = utc_now()
    db = SessionLocal()
    try:
        result = apply_successful_charge(
            db,
            reference,
            # requested_amount is the plan price; amount includes the Paystack fee.
            int(data.get("requested_amount") or data.get("amount", -1)),
            parse_paystack_time(data.get("paid_at"), now),
            now,
        )
        if result in (APPLIED, ALREADY_APPLIED):
            radius_db = RadiusSessionLocal()
            try:
                grant_mac_for_charge(db, radius_db, reference, now)
            finally:
                radius_db.close()
        if result == APPLIED:
            cache_charge_status(get_redis(), db, reference, now)
    except Exception:
        log.exception("Failed to apply charge %s", reference)
        db.rollback()
    finally:
        db.close()
