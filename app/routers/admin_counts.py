from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import User
from app.schemas.counts import UserCounts

router = APIRouter(prefix="/api/admin", tags=["admin"])

USER_TYPES = ("student", "lecturer", "guest", "gated")


@router.get("/users/counts", response_model=UserCounts)
def user_counts(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserCounts:
    """How many accounts there are, by type, and by status. Counts every account, not a page."""
    by_type = {t: 0 for t in USER_TYPES}
    for user_type, count in db.execute(
        select(User.user_type, func.count(User.id)).group_by(User.user_type)
    ).all():
        by_type[user_type] = count

    def count(*conditions) -> int:
        return db.scalar(select(func.count(User.id)).where(*conditions)) or 0

    return UserCounts(
        total=count(),
        by_type=by_type,
        active=count(User.is_active.is_(True)),
        disabled=count(User.is_active.is_(False)),
        admins=count(User.is_admin.is_(True)),
        paid=count(User.access_type == "paid"),
        free=count(User.access_type == "free"),
    )
