from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AdminAction, User


def record_admin_action(
    db: Session,
    admin: User,
    action: str,
    target_user_id: int | None,
    detail: str | None,
    now: datetime,
) -> None:
    """Add an admin_actions row. The caller commits, in the same transaction as the change."""
    db.add(
        AdminAction(
            admin_user_id=admin.id,
            action=action,
            target_user_id=target_user_id,
            detail=detail,
            created_at=now,
        )
    )
