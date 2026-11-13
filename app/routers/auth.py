import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.redis_client import get_redis
from app.schemas.auth import MessageResponse, PasswordChangeRequest, RegisterRequest
from app.security.passwords import hash_password, verify_password
from app.security.sessions import clear_session_cookie
from app.services import rate_limit
from app.services.device_cache import cache_device_added
from app.services.devices import DeviceLimitReached, DeviceOwnedByAnotherUser, bind_device
from app.utils.client_ip import client_ip
from app.utils.mac import InvalidMacError, normalise_mac
from app.utils.time import utc_now

router = APIRouter(prefix="/api/auth", tags=["auth"])

REGISTER_LIMIT = 10  # attempts per IP per 10 minutes (Section 8)
ALREADY_REGISTERED = "This account is already registered. Sign in instead."
DEVICE_LIMIT = "You have reached the device limit. Remove a device from your dashboard first."
DEVICE_OWNED = "This device is already linked to another account. Contact ICT."
GATED_NOT_LISTED = (
    "This email is not on the gated access list. Please contact the ICT admin to be added."
)


@router.post("/register", status_code=201, response_model=MessageResponse)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> MessageResponse:
    settings = get_settings()
    ip = client_ip(request)
    ip_key = f"rl:register:ip:{ip}"
    if rate_limit.is_limited(r, ip_key, REGISTER_LIMIT):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 10 minutes.")
    rate_limit.record_failure(r, ip_key)

    try:
        mac = normalise_mac(body.mac)
    except InvalidMacError:
        raise HTTPException(status_code=422, detail="Invalid device address.")

    now = utc_now()
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))

    if user is None:
        if body.user_type == "gated":
            # Gated access is granted by the admin, never by self-registration.
            raise HTTPException(status_code=403, detail=GATED_NOT_LISTED)
        user = User(
            email=email,
            user_type=body.user_type,
            status="pre_registered",
            access_type="paid",
            max_devices=settings.DEFAULT_MAX_DEVICES,
            auto_renew=True,
            is_active=True,
        )
        db.add(user)
        db.flush()  # needs user.id before the device is bound
    elif user.status != "pre_registered" or user.password_hash:
        raise HTTPException(status_code=409, detail=ALREADY_REGISTERED)

    user.password_hash = hash_password(body.password)
    user.status = "active"
    if body.matric_no and user.user_type == "student":
        user.matric_no = body.matric_no

    try:
        bind_device(db, user, mac, body.ip, body.deviceName, now)
    except DeviceOwnedByAnotherUser:
        db.rollback()
        raise HTTPException(status_code=409, detail=DEVICE_OWNED)
    except DeviceLimitReached:
        db.rollback()
        raise HTTPException(status_code=409, detail=DEVICE_LIMIT)

    try:
        db.commit()
    except IntegrityError:
        # Two registrations for the same email raced past the check above.
        db.rollback()
        raise HTTPException(status_code=409, detail=ALREADY_REGISTERED)

    cache_device_added(r, email, mac)
    return MessageResponse(ok=True, message="Device registered. Sign in to connect.")


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response) -> MessageResponse:
    """Ends the dashboard session only. Internet access is controlled by the router."""
    clear_session_cookie(response)
    return MessageResponse(ok=True)


@router.post("/password", response_model=MessageResponse)
def change_password(
    body: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return MessageResponse(ok=True, message="Password updated.")
