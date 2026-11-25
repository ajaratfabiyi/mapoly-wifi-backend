"""Creating admin accounts. Only an existing admin can call this (see the router)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.security.passwords import hash_password
from app.services.admin_accounts import AdminActionError
from app.services.admin_log import record_admin_action


def create_admin_account(
    db: Session, admin: User, email: str, full_name: str, temporary_password: str, now: datetime
) -> User:
    """A new admin account with a temporary password. The new admin must change it at first sign-in."""
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise AdminActionError("This email is already registered.")
    user = User(
        email=email,
        password_hash=hash_password(temporary_password),
        status="active",
        user_type="gated",
        access_type="free",
        full_name=full_name.strip(),
        is_active=True,
        is_admin=True,
        must_change_password=True,
        gated_until=None,
    )
    db.add(user)
    db.flush()
    record_admin_action(db, admin, "create_admin", user.id, f"created admin {email}", now)
    db.commit()
    return user
