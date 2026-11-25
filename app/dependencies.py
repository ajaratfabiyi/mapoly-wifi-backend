from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security.sessions import COOKIE_NAME, read_session_value


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Dashboard routes depend on this. Rejects missing, forged, expired, or disabled sessions."""
    user_id = read_session_value(request.cookies.get(COOKIE_NAME))
    user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active or user.status == "disabled":
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Admin routes depend on this. Only accounts with is_admin set may use them."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only.")
    return user
