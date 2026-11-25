from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import AdminAction, PointsEntry, SessionLog, User
from app.radius_database import get_radius_db
from app.schemas.admin_tools import (
    AuditOut,
    LoginEventOut,
    PasswordReset,
    PointsAdjust,
    PointsEntryOut,
    PointsSummary,
    UserPatch,
    UserRow,
)
from app.security.passwords import hash_password
from app.services.admin_accounts import AdminActionError, adjust_points, disable_account, enable_account
from app.services.admin_log import record_admin_action
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])

USER_NOT_FOUND = "Account not found."


def _row(u: User) -> UserRow:
    return UserRow(
        id=u.id, email=u.email, full_name=u.full_name, matric_no=u.matric_no,
        user_type=u.user_type, access_type=u.access_type, status=u.status,
        is_active=bool(u.is_active), is_admin=bool(u.is_admin), points=u.points or 0,
        max_devices=u.max_devices, gated_until=u.gated_until,
    )


def _user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    return user


@router.get("/users", response_model=list[UserRow])
def list_users(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserRow]:
    """Search by email, full name or matric number. Leave q out to list everyone."""
    stmt = select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    if q:
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(User.email).like(term),
            func.lower(User.full_name).like(term),
            func.lower(User.matric_no).like(term),
        ))
    return [_row(u) for u in db.scalars(stmt).all()]


@router.get("/users/{user_id}", response_model=UserRow)
def get_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserRow:
    return _row(_user_or_404(db, user_id))


@router.patch("/users/{user_id}", response_model=UserRow)
def update_user(
    user_id: int,
    body: UserPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> UserRow:
    """Disable or re-enable an account, or change its device limit (null means no limit)."""
    user = _user_or_404(db, user_id)
    now = utc_now()

    if "is_active" in body.model_fields_set and body.is_active is not None:
        if body.is_active is False:
            if user.id == admin.id:
                raise HTTPException(status_code=422, detail="You cannot disable your own account.")
            disable_account(db, radius_db, admin, user, now)
        else:
            enable_account(db, admin, user, now)

    if "max_devices" in body.model_fields_set:
        old = user.max_devices
        user.max_devices = body.max_devices
        record_admin_action(db, admin, "set_device_limit", user.id,
                            f"{old} -> {body.max_devices}", now)
        db.commit()

    db.refresh(user)
    return _row(user)


@router.post("/users/{user_id}/reset-password", status_code=204)
def reset_password(
    user_id: int,
    body: PasswordReset,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Set a temporary password. The user must change it at their next sign-in. The password is never logged."""
    user = _user_or_404(db, user_id)
    user.password_hash = hash_password(body.temporary_password)
    user.must_change_password = True
    record_admin_action(db, admin, "reset_password", user.id, user.email, utc_now())
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/points")
def adjust_user_points(
    user_id: int,
    body: PointsAdjust,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = _user_or_404(db, user_id)
    try:
        balance = adjust_points(db, admin, user, body.delta, body.reason.strip(), utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    return {"points": balance}


@router.get("/users/{user_id}/points", response_model=list[PointsEntryOut])
def user_points_history(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[PointsEntryOut]:
    _user_or_404(db, user_id)
    rows = db.scalars(
        select(PointsEntry).where(PointsEntry.user_id == user_id)
        .order_by(desc(PointsEntry.id)).limit(200)
    ).all()
    return [PointsEntryOut(id=r.id, delta=r.delta, balance_after=r.balance_after,
                           reason=r.reason, reference=r.reference, created_at=r.created_at)
            for r in rows]


@router.get("/points/summary", response_model=PointsSummary)
def points_summary(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PointsSummary:
    """Total points held by all accounts. Points are worth money, so this is the amount owed to users."""
    total = db.scalar(select(func.coalesce(func.sum(User.points), 0)))
    holders = db.scalar(select(func.count()).select_from(User).where(User.points > 0))
    return PointsSummary(total_points=int(total or 0), accounts_with_points=int(holders or 0))


@router.get("/audit", response_model=list[AuditOut])
def admin_audit(
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditOut]:
    """Every change an admin has made, newest first."""
    rows = db.execute(
        select(AdminAction, User.email)
        .outerjoin(User, User.id == AdminAction.admin_user_id)
        .order_by(desc(AdminAction.id)).limit(limit)
    ).all()
    return [AuditOut(created_at=a.created_at, admin_email=email, action=a.action,
                     target_user_id=a.target_user_id, detail=a.detail)
            for a, email in rows]


@router.get("/audit/logins", response_model=list[LoginEventOut])
def login_audit(
    user_id: int | None = Query(default=None),
    mac: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[LoginEventOut]:
    """Sign-in, failed sign-in and device-limit events. Filter by account or device."""
    stmt = select(SessionLog).order_by(desc(SessionLog.id)).limit(limit)
    if user_id is not None:
        stmt = stmt.where(SessionLog.user_id == user_id)
    if mac:
        stmt = stmt.where(SessionLog.mac_address == mac.upper())
    return [LoginEventOut(created_at=r.created_at, event=r.event, user_id=r.user_id,
                          mac_address=r.mac_address, ip_address=r.ip_address)
            for r in db.scalars(stmt).all()]
