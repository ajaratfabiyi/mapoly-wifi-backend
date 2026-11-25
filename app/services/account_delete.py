"""Permanently deleting accounts, and the database reset.

Deleting an account removes the account and everything tied to it: devices, points history,
login tokens, RADIUS rows and live sessions. Payment (ledger) rows are kept and detached from
the account, with the payer's email saved on the row, so finance records stay intact.

Admin accounts are never deleted by these functions. Their RADIUS rows, if any, are not
touched either.

RADIUS is changed before the database, so a failure leaves the account as it was.
"""

from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.models import AdminAction, Ledger, LoginToken, PointsEntry, SessionLog, User, UserDevice
from app.services.admin_accounts import AdminActionError
from app.services.admin_log import record_admin_action
from app.services.coa_queue import enqueue_disconnect
from app.services.radius import (
    ACCOUNT_MATCH,
    account_match_params,
    close_device_sessions,
    remove_device_access,
    remove_user_access,
)


def _clear_radius(radius_db: Session, email: str, macs: list[str], now: datetime) -> None:
    """Remove RADIUS access and live sessions for one account. Accounting history is kept."""
    for mac in macs:
        close_device_sessions(radius_db, mac, now)
    remove_user_access(radius_db, email)
    for mac in macs:
        remove_device_access(radius_db, mac)


def _delete_user_rows(db: Session, user_ids: list[int]) -> None:
    """Remove everything the database holds for these accounts, except payments (detached)."""
    if not user_ids:
        return
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(user_ids))).all())
    # Keep payments: remember who paid, then detach the row from the account.
    for uid, email in emails.items():
        db.execute(
            update(Ledger)
            .where(Ledger.user_id == uid)
            .values(user_id=None, device_id=None, payer_email=email)
        )
    db.execute(delete(PointsEntry).where(PointsEntry.user_id.in_(user_ids)))
    db.execute(update(SessionLog).where(SessionLog.user_id.in_(user_ids)).values(user_id=None))
    db.execute(update(AdminAction).where(AdminAction.target_user_id.in_(user_ids))
               .values(target_user_id=None))
    db.execute(delete(UserDevice).where(UserDevice.user_id.in_(user_ids)))
    for email in emails.values():
        # Login tokens for the bare email and every device username ("<email>|<MAC>").
        db.execute(
            delete(LoginToken).where(
                (LoginToken.username == email)
                | LoginToken.username.startswith(email + "|", autoescape=True)
            )
        )
    db.execute(delete(User).where(User.id.in_(user_ids)))


def delete_account(db: Session, radius_db: Session, admin: User, user: User, now: datetime) -> None:
    """Delete one account completely. Refuses admins and the admin's own account."""
    if user.id == admin.id:
        raise AdminActionError("You cannot delete your own account.")
    if user.is_admin:
        raise AdminActionError("Admin accounts cannot be deleted here.")
    email = user.email
    macs = list(db.scalars(select(UserDevice.mac_address).where(UserDevice.user_id == user.id)).all())
    _clear_radius(radius_db, email, macs, now)
    for mac in macs:
        enqueue_disconnect(db, "device_removed", now, mac=mac)
    _delete_user_rows(db, [user.id])
    record_admin_action(db, admin, "delete_account", None, f"deleted {email}", now)
    db.commit()


def reset_database(db: Session, radius_db: Session, admin: User, now: datetime) -> int:
    """Delete every non-admin account and their data. Returns how many accounts were deleted.

    Keeps: admin accounts, plans, announcements, blocked MACs and the audit log.
    """
    users = db.scalars(select(User).where(User.is_admin.is_(False), User.id != admin.id)).all()
    if not users:
        record_admin_action(db, admin, "reset_database", None, "0 accounts", now)
        db.commit()
        return 0

    user_ids = [u.id for u in users]
    grouped: dict[int, list[str]] = {}
    for uid, mac in db.execute(
        select(UserDevice.user_id, UserDevice.mac_address).where(UserDevice.user_id.in_(user_ids))
    ).all():
        grouped.setdefault(uid, []).append(mac)

    for user in users:
        macs = grouped.get(user.id, [])
        _clear_radius(radius_db, user.email, macs, now)
        enqueue_disconnect(db, "account_disabled", now, username=user.email)
        # Accounting history for this account. FreeRADIUS writes radacct; clearing it here is
        # part of the reset, not a routine change.
        radius_db.execute(
            text(f"DELETE FROM radacct WHERE {ACCOUNT_MATCH}"),
            account_match_params(user.email),
        )
    radius_db.commit()

    _delete_user_rows(db, user_ids)
    record_admin_action(db, admin, "reset_database", None, f"{len(user_ids)} accounts deleted", now)
    db.commit()
    return len(user_ids)
