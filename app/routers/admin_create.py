from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import User
from app.schemas.admin_create import AdminCreate, AdminCreated
from app.services.admin_accounts import AdminActionError
from app.services.admin_create import create_admin_account
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/admins", response_model=AdminCreated, status_code=201)
def create_admin(
    body: AdminCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminCreated:
    """Create another admin account. They sign in with the temporary password and must change it."""
    try:
        user = create_admin_account(db, admin, body.email, body.full_name,
                                    body.temporary_password, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    return AdminCreated(id=user.id, email=user.email)
