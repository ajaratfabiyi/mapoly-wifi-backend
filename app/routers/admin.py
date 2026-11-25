from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import User
from app.radius_database import get_radius_db
from app.schemas.admin import GatedUserCreate, GatedUserOut, GatedUserUpdate
from app.schemas.admin_tools import BulkGatedUpload, GroupDisable
from app.services.admin_accounts import (
    AdminActionError,
    create_gated_account,
    create_gated_batch,
    disable_account,
    parse_gated_csv,
)
from app.services.admin_log import record_admin_action
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])

NOT_GATED = "This account is not a gated account."


def _out(user: User) -> GatedUserOut:
    return GatedUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        matric_no=user.matric_no,
        department=user.department,
        level=user.level,
        phone=user.phone,
        gated_until=user.gated_until,
        must_change_password=user.must_change_password,
    )


@router.post("/gated-users", response_model=GatedUserOut)
def create_gated_user(
    body: GatedUserCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> GatedUserOut:
    """Create one gated account with a temporary password. The user must change it at first sign-in."""
    try:
        user = create_gated_account(db, admin, body.model_dump(), utc_now())
        db.commit()
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email is already registered.")
    return _out(user)


@router.post("/gated-users/bulk")
def create_gated_users_from_csv(
    body: BulkGatedUpload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Create gated accounts from a CSV. Every row is checked first. If any row is wrong, nothing is saved.

    The first line must include: email, full_name, temporary_password. Optional columns:
    matric_no, department, level, phone, gated_until (YYYY-MM-DD) or duration_days.
    """
    rows, problems = parse_gated_csv(body.csv)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems[:20]))
    if not rows:
        raise HTTPException(status_code=422, detail="The file has no rows.")
    try:
        ids = create_gated_batch(db, admin, rows, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    return {"created": len(ids), "ids": ids}


@router.post("/gated-users/disable-group")
def disable_gated_group(
    body: GroupDisable,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> dict:
    """Disable many gated accounts at once, for example at the end of a semester."""
    users = db.scalars(
        select(User).where(User.id.in_(body.user_ids), User.user_type == "gated")
    ).all()
    now = utc_now()
    for user in users:
        if user.id != admin.id:
            disable_account(db, radius_db, admin, user, now)
    return {"disabled": len([u for u in users if u.id != admin.id])}


@router.patch("/gated-users/{user_id}", response_model=GatedUserOut)
def update_gated_user(
    user_id: int,
    body: GatedUserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> GatedUserOut:
    """Set, extend or remove a gated account's end date."""
    user = db.get(User, user_id)
    if user is None or user.user_type != "gated":
        raise HTTPException(status_code=404, detail=NOT_GATED)

    now = utc_now()
    if "gated_until" in body.model_fields_set or body.duration_days is not None:
        old = user.gated_until
        if body.duration_days is not None:
            user.gated_until = now + timedelta(days=body.duration_days)
            new_text = f"{body.duration_days} days from today ({user.gated_until:%Y-%m-%d})"
        else:
            user.gated_until = body.gated_until
            new_text = f"{body.gated_until:%Y-%m-%d}" if body.gated_until else "no end date"
        old_text = f"{old:%Y-%m-%d}" if old else "no end date"
        record_admin_action(
            db, admin, "set_gated_end_date", user.id,
            f"{user.email}: {old_text} -> {new_text}", now,
        )
    db.commit()
    return _out(user)
