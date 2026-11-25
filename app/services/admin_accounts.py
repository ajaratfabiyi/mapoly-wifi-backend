"""Admin actions on accounts, devices, blocks and points.

Each function changes the database in one transaction and writes an admin_actions row.
RADIUS is changed first where access is removed, so a failure leaves the device as it was
and the admin can retry. Disconnects go through the CoA queue, as the user routes do.
"""

import csv
import io
import secrets
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BlockedMac, Ledger, Plan, PointsEntry, User, UserDevice
from app.schemas.admin import GatedUserCreate
from app.security.passwords import hash_password
from app.services.access import extend_device_access
from app.services.admin_log import record_admin_action
from app.services.coa_queue import enqueue_disconnect
from app.services.device_cache import cache_device_removed
from app.services.payments import cache_charge_status, grant_mac_for_charge
from app.services.radius import close_device_sessions, device_username, remove_device_access, remove_user_access

MAX_BULK_ROWS = 500
CSV_REQUIRED = ("email", "full_name", "temporary_password")


class AdminActionError(Exception):
    """A user-facing refusal. The message is safe to show to the admin."""


# ---------- gated accounts ----------


def end_from(data: dict, now: datetime) -> datetime | None:
    """The end date for a new or extended gated account: an explicit date, a duration from now, or none."""
    if data.get("gated_until") is not None:
        return data["gated_until"]
    if data.get("duration_days"):
        return now + timedelta(days=data["duration_days"])
    return None


def create_gated_account(db: Session, admin: User, data: dict, now: datetime) -> User:
    email = data["email"].strip().lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise AdminActionError("This email is already registered.")
    user = User(
        email=email,
        password_hash=hash_password(data["temporary_password"]),
        status="active",
        user_type="gated",
        access_type="free",
        full_name=data["full_name"].strip(),
        matric_no=data.get("matric_no"),
        department=data.get("department"),
        level=data.get("level"),
        phone=data.get("phone"),
        is_active=True,
        must_change_password=True,
        gated_until=end_from(data, now),
    )
    db.add(user)
    db.flush()
    detail = f"created {email}"
    if user.gated_until:
        detail += f", ends {user.gated_until:%Y-%m-%d}"
    record_admin_action(db, admin, "create_gated_user", user.id, detail, now)
    return user


def parse_gated_csv(text: str) -> tuple[list[dict], list[str]]:
    """Read and check a CSV of gated accounts before anything is saved.

    Returns the valid rows and a list of problems. If there are any problems, nothing is saved.
    """
    problems: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [h for h in CSV_REQUIRED if h not in headers]
    if missing:
        return [], [f"The first line must include: {', '.join(CSV_REQUIRED)}. Missing: {', '.join(missing)}."]

    rows: list[dict] = []
    seen_emails: set[str] = set()
    for line_no, raw in enumerate(reader, start=2):
        if line_no - 1 > MAX_BULK_ROWS:
            problems.append(f"At most {MAX_BULK_ROWS} rows can be uploaded at once.")
            break
        clean = {
            (k or "").strip().lower(): (v or "").strip() or None
            for k, v in raw.items()
            if k is not None
        }
        clean = {k: v for k, v in clean.items() if k in GatedUserCreate.model_fields}
        try:
            parsed = GatedUserCreate(**clean).model_dump()
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ())) or "row"
            problems.append(f"Line {line_no}: {field} is not valid.")
            continue
        if parsed["email"].lower() in seen_emails:
            problems.append(f"Line {line_no}: {parsed['email']} appears twice in this file.")
            continue
        seen_emails.add(parsed["email"].lower())
        rows.append(parsed)
    return rows, problems


def create_gated_batch(db: Session, admin: User, rows: list[dict], now: datetime) -> list[int]:
    """Create every row, or none. Returns the new user ids."""
    emails = [r["email"].strip().lower() for r in rows]
    taken = db.scalars(select(User.email).where(User.email.in_(emails))).all()
    if taken:
        raise AdminActionError(f"Already registered: {', '.join(sorted(taken)[:10])}")
    ids = [create_gated_account(db, admin, row, now).id for row in rows]
    db.commit()
    return ids


def disable_account(db: Session, radius_db: Session, admin: User, user: User, now: datetime) -> None:
    """Stop the account: no login, and its devices are cut off now."""
    devices = db.scalars(select(UserDevice).where(UserDevice.user_id == user.id)).all()
    remove_user_access(radius_db, user.email)
    for device in devices:
        enqueue_disconnect(db, "account_disabled", now,
                           username=device_username(user.email, device.mac_address))
    user.is_active = False
    record_admin_action(db, admin, "disable_account", user.id, user.email, now)
    db.commit()


def disconnect_account(db: Session, radius_db: Session, admin: User, user: User, now: datetime) -> int:
    """Cut off every device on the account now. The account stays active, so the person can sign in again."""
    devices = db.scalars(select(UserDevice).where(UserDevice.user_id == user.id)).all()
    for device in devices:
        close_device_sessions(radius_db, device.mac_address, now)
        enqueue_disconnect(db, "device_removed", now, mac=device.mac_address)
    record_admin_action(db, admin, "disconnect_account", user.id,
                        f"{user.email}: {len(devices)} device(s)", now)
    db.commit()
    return len(devices)


def enable_account(db: Session, admin: User, user: User, now: datetime) -> None:
    user.is_active = True
    record_admin_action(db, admin, "enable_account", user.id, user.email, now)
    db.commit()


# ---------- devices ----------


def disconnect_device(db: Session, radius_db: Session, admin: User, device: UserDevice, now: datetime) -> None:
    """Cut the device off now. Its plan stays; it can reconnect after logging in."""
    close_device_sessions(radius_db, device.mac_address, now)
    enqueue_disconnect(db, "device_removed", now, mac=device.mac_address)
    record_admin_action(db, admin, "disconnect_device", device.user_id, device.mac_address, now)
    db.commit()


def remove_device(db: Session, radius_db: Session, r, admin: User, device: UserDevice, now: datetime) -> None:
    """Remove the device, as the user's own remove does, with the admin's name in the log."""
    user = db.get(User, device.user_id)
    mac = device.mac_address
    remove_device_access(radius_db, mac)
    enqueue_disconnect(db, "device_removed", now, mac=mac)
    record_admin_action(db, admin, "remove_device", device.user_id, mac, now)
    db.delete(device)
    db.commit()
    if user is not None:
        cache_device_removed(r, user.email, mac)


def grant_plan_to_device(
    db: Session, radius_db: Session, r, admin: User, device: UserDevice, plan: Plan, now: datetime
) -> datetime:
    """Give a device a plan for free, for example after a failed payment. Logged as a zero-amount charge."""
    user = db.get(User, device.user_id)
    if user is None or user.access_type != "paid":
        raise AdminActionError("Plans can only be given to paid accounts.")
    if not plan.is_active:
        raise AdminActionError("This plan is not on sale.")

    reference = f"admin_{now:%Y%m%d}_{secrets.token_hex(4)}"
    db.add(
        Ledger(
            reference=reference,
            user_id=user.id,
            device_id=device.id,
            plan_id=plan.id,
            amount_kobo=0,
            payment_method=None,
            status="success",
            paid_at=now,
            created_at=now,
        )
    )
    expires = extend_device_access(device, plan.duration_hours, now)
    record_admin_action(db, admin, "grant_plan", user.id,
                        f"{plan.id} to {device.mac_address}", now)
    db.commit()
    grant_mac_for_charge(db, radius_db, reference, now)
    cache_charge_status(r, db, reference, now)
    return expires


# ---------- blocks ----------


def block_mac(db: Session, radius_db: Session, admin: User, mac: str, reason: str, now: datetime) -> None:
    """Block a device. If it has access now, that access is removed and it is cut off."""
    if db.scalar(select(BlockedMac.id).where(BlockedMac.mac == mac)) is not None:
        raise AdminActionError("This device is already blocked.")
    device = db.scalar(select(UserDevice).where(UserDevice.mac_address == mac))
    if device is not None:
        remove_device_access(radius_db, mac)
        enqueue_disconnect(db, "device_removed", now, mac=mac)
    db.add(BlockedMac(mac=mac, reason=reason, created_at=now))
    record_admin_action(db, admin, "block_mac", device.user_id if device else None,
                        f"{mac}: {reason}", now)
    db.commit()


def unblock_mac(db: Session, admin: User, mac: str, now: datetime) -> None:
    block = db.scalar(select(BlockedMac).where(BlockedMac.mac == mac))
    if block is None:
        raise AdminActionError("This device is not blocked.")
    db.delete(block)
    record_admin_action(db, admin, "unblock_mac", None, mac, now)
    db.commit()


# ---------- points ----------


def adjust_points(db: Session, admin: User, user: User, delta: int, reason: str, now: datetime) -> int:
    if user.points + delta < 0:
        raise AdminActionError("This would take the balance below zero.")
    user.points = (user.points or 0) + delta
    reference = f"admin_{now:%Y%m%d%H%M%S}_{secrets.token_hex(3)}"
    db.add(
        PointsEntry(
            user_id=user.id,
            delta=delta,
            balance_after=user.points,
            reason="admin",
            reference=reference,
            created_at=now,
        )
    )
    record_admin_action(db, admin, "adjust_points", user.id, f"{delta:+d}: {reason}", now)
    db.commit()
    return user.points
