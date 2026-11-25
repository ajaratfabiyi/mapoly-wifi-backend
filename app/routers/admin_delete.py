from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import User
from app.radius_database import get_radius_db
from app.schemas.account_delete import DeleteAccountIn, ResetDatabaseIn, ResetDatabaseOut
from app.services.account_delete import delete_account, reset_database
from app.services.admin_accounts import AdminActionError
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/users/{user_id}/delete", status_code=204)
def delete_user_account(
    user_id: int,
    body: DeleteAccountIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> Response:
    """Delete an account permanently. Payments are kept, detached from the account."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    if body.confirm_email.strip().lower() != user.email.lower():
        raise HTTPException(status_code=422, detail="Type the account's email exactly to confirm.")
    try:
        delete_account(db, radius_db, admin, user, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    return Response(status_code=204)


@router.post("/database/reset", response_model=ResetDatabaseOut)
def reset_all_accounts(
    body: ResetDatabaseIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> ResetDatabaseOut:
    """Delete every account except admins. Plans, announcements, blocked MACs and the audit log stay."""
    try:
        count = reset_database(db, radius_db, admin, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    return ResetDatabaseOut(accounts_deleted=count)
