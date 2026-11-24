"""Applying a successful charge (Section 6, steps 5 to 8).

Called by the webhook and by the verify fallback. Both paths go through here, so
a charge is applied once no matter which arrives first.

A charge extends the plan of the device named on its ledger row, and only that device.
"""

import logging
from datetime import datetime

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ledger, Plan, UserDevice
from app.services.access import extend_device_access
from app.services.login_tokens import group_for
from app.services.radius import device_username, grant_mac_access

log = logging.getLogger(__name__)

APPLIED = "applied"
ALREADY_APPLIED = "already_applied"
UNKNOWN_REFERENCE = "unknown_reference"
AMOUNT_MISMATCH = "amount_mismatch"
DEVICE_GONE = "device_gone"


def apply_successful_charge(
    db: Session,
    reference: str,
    amount_paid_kobo: int,
    paid_at: datetime,
    now: datetime,
) -> str:
    """Apply one charge in a single transaction. Returns one of the result codes above.

    Row locks stop two concurrent deliveries from both extending access.
    """
    ledger = db.scalar(
        select(Ledger).where(Ledger.reference == reference).with_for_update()
    )
    if ledger is None:
        # Never create a ledger row from a webhook (Section 6, step 5).
        return UNKNOWN_REFERENCE
    if ledger.status == "success":
        return ALREADY_APPLIED

    plan = db.get(Plan, ledger.plan_id)
    if plan is None or amount_paid_kobo != plan.price_kobo:
        ledger.status = "failed"
        db.commit()
        return AMOUNT_MISMATCH

    device = None
    if ledger.device_id is not None:
        device = db.scalar(
            select(UserDevice).where(UserDevice.id == ledger.device_id).with_for_update()
        )
    if device is None:
        # The device was removed after checkout. The customer has paid for a device that
        # no longer exists, so nothing is granted. Logged for a manual refund.
        ledger.status = "failed"
        db.commit()
        log.warning("Charge %s paid for a device that no longer exists; refund needed", reference)
        return DEVICE_GONE

    ledger.status = "success"
    ledger.paid_at = paid_at
    extend_device_access(device, plan.duration_hours, now)
    db.commit()
    return APPLIED


def grant_mac_for_charge(db: Session, radius_db: Session, reference: str, now: datetime) -> None:
    """Let the paid device onto the network by MAC, so it reconnects without logging in.

    Safe to call again for the same charge: it replaces the device's rows with the
    current expiry. Call it after apply_successful_charge returns APPLIED or ALREADY_APPLIED.
    """
    ledger = db.scalar(select(Ledger).where(Ledger.reference == reference))
    if ledger is None or ledger.device_id is None or ledger.status != "success":
        return
    device = db.get(UserDevice, ledger.device_id)
    if device is None or device.access_expires_at is None:
        return
    grant_mac_access(
        radius_db,
        device.mac_address,
        device.access_expires_at,
        group_for(device.user.user_type),
        now,
    )


def cache_status(r: redis.Redis, username: str, expires_at: datetime, now: datetime) -> None:
    """Section 6, step 8, per device: wifi:status:<device username> with the remaining
    lifetime as TTL. The device username is <email>|<MAC>, as used by RADIUS."""
    seconds = int((expires_at - now).total_seconds())
    if seconds > 0:
        r.set(f"wifi:status:{username}", 1, ex=seconds)


def cache_charge_status(r: redis.Redis, db: Session, reference: str, now: datetime) -> None:
    """Cache the status of the device a successful charge paid for."""
    ledger = db.scalar(select(Ledger).where(Ledger.reference == reference))
    if ledger is None or ledger.device_id is None:
        return
    device = db.get(UserDevice, ledger.device_id)
    if device is None or device.access_expires_at is None:
        return
    cache_status(r, device_username(ledger.user.email, device.mac_address),
                 device.access_expires_at, now)
