"""Shapes the account view that /api/me and the device routes return.

Plans are per device. Each device shows its own plan and expiry. The account-level
summary fields (plan_name, access_expires_at, days_left) describe the device whose plan
ends last, so the dashboard summary still has one value to show.
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ledger, Plan, User, UserDevice
from app.schemas.me import DeviceOut, MeResponse

ACTIVE_WINDOW = timedelta(minutes=15)
IDLE_WINDOW = timedelta(hours=24)


def device_plan(db: Session, device: UserDevice) -> Plan | None:
    """The plan from this device's most recent successful payment."""
    return db.scalar(
        select(Plan)
        .join(Ledger, Ledger.plan_id == Plan.id)
        .where(Ledger.device_id == device.id, Ledger.status == "success")
        .order_by(Ledger.paid_at.desc())
        .limit(1)
    )


def device_status(device: UserDevice, now: datetime) -> str:
    if device.last_seen is None:
        return "offline"
    age = now - device.last_seen
    if age <= ACTIVE_WINDOW:
        return "active"
    if age <= IDLE_WINDOW:
        return "idle"
    return "offline"


def latest_device(devices: list[UserDevice]) -> UserDevice | None:
    """The device whose plan ends last, or None if no device has a plan."""
    with_plan = [d for d in devices if d.access_expires_at is not None]
    return max(with_plan, key=lambda d: d.access_expires_at) if with_plan else None


def days_left(expires_at: datetime | None, now: datetime) -> int | None:
    if expires_at is None:
        return None
    seconds = (expires_at - now).total_seconds()
    return max(0, math.ceil(seconds / 86400))


def device_out(device: UserDevice, user: User, plan: Plan | None, now: datetime) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        name=device.device_name,
        mac_address=device.mac_address,
        ip_address=device.ip_address,
        bound_plan=plan.title if plan else None,
        expires_at=device.access_expires_at if user.access_type == "paid" else None,
        auto_renew=device.auto_renew,
        status=device_status(device, now),
        last_seen=device.last_seen,
    )


def build_me(db: Session, user: User, now: datetime) -> MeResponse:
    devices = list(
        db.scalars(
            select(UserDevice).where(UserDevice.user_id == user.id).order_by(UserDevice.id)
        ).all()
    )
    top = latest_device(devices) if user.access_type == "paid" else None
    top_plan = device_plan(db, top) if top is not None else None
    expires = top.access_expires_at if top is not None else None
    return MeResponse(
        email=user.email,
        user_type=user.user_type,
        matric_no=user.matric_no,
        department=user.department,
        level=user.level,
        phone=user.phone,
        plan_name=top_plan.title if top_plan else None,
        access_expires_at=expires,
        days_left=days_left(expires, now),
        auto_renew=user.auto_renew,
        max_devices=user.max_devices,
        points=user.points or 0,
        devices=[device_out(d, user, device_plan(db, d), now) for d in devices],
    )
