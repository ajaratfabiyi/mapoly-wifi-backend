import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.schemas.login import LoginRequest, LoginResponse
from app.security.passwords import verify_password
from app.security.sessions import set_session_cookie
from app.services import access, rate_limit
from app.services.audit import log_event
from app.services.device_cache import cache_device_added
from app.services.devices import DeviceLimitReached, DeviceOwnedByAnotherUser, bind_device
from app.services.login_tokens import find_reusable_token, issue_token
from app.services.radius import device_username
from app.utils.client_ip import client_ip
from app.utils.mac import InvalidMacError, normalise_mac
from app.utils.time import utc_now

router = APIRouter(prefix="/api", tags=["auth"])

INVALID_CREDENTIALS = "Invalid email or password."
TOO_MANY_ATTEMPTS = "Too many attempts. Try again in 10 minutes."
DEVICE_LIMIT = "You have reached the device limit. Remove a device from your dashboard first."
DEVICE_OWNED = "This device is already linked to another account. Contact ICT."
UNAVAILABLE = "The login service is temporarily unavailable."


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> LoginResponse:
    settings = get_settings()

    # Step 1: reject a malformed MAC before any database work.
    try:
        mac = normalise_mac(body.mac)
    except InvalidMacError:
        raise HTTPException(status_code=422, detail="Invalid device address.")

    email = body.email.strip().lower()
    # Rate-limit on the connecting address, not the body (the body is client-supplied).
    ip = client_ip(request)
    device_ip = body.ip or ip
    now = utc_now()

    # Step 2: rate limits.
    email_key = f"rl:login:email:{email}"
    ip_key = f"rl:login:ip:{ip}"
    if rate_limit.is_limited(r, email_key, rate_limit.EMAIL_LIMIT) or rate_limit.is_limited(
        r, ip_key, rate_limit.IP_LIMIT
    ):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)

    # Steps 3 and 4: find the user, verify the password (dummy check if unknown).
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.status == "pre_registered":
        raise HTTPException(status_code=401, detail="Please complete your registration first.")

    password_ok = verify_password(user.password_hash if user else None, body.password)
    if user is None or not password_ok:
        rate_limit.record_failure(r, email_key)
        rate_limit.record_failure(r, ip_key)
        log_event(db, "login_fail", user.id if user else None, mac, device_ip, now)
        db.commit()
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    # Step 5: account state.
    if not user.is_active or user.status == "disabled":
        raise HTTPException(
            status_code=403, detail="This account has been disabled. Contact ICT."
        )

    # Step 6: bind the device. Each MAC belongs to one account; each account has max_devices.
    try:
        device = bind_device(db, user, mac, device_ip, body.deviceName, now)
    except DeviceOwnedByAnotherUser:
        log_event(db, "login_fail", user.id, mac, device_ip, now)
        db.commit()
        raise HTTPException(status_code=409, detail=DEVICE_OWNED)
    except DeviceLimitReached:
        log_event(db, "device_limit", user.id, mac, device_ip, now)
        db.commit()
        raise HTTPException(status_code=409, detail=DEVICE_LIMIT)

    # Commit the device binding first. The database is the source of truth, so a
    # binding that succeeded is never lost, even if the token step below fails.
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    cache_device_added(r, user.email, mac)

    # The RADIUS username names this device, so each device has its own token.
    radius_user = device_username(email, mac)

    # Step 7: access.
    # Access is checked on this device's own plan, not the account's.
    has_plan = access.has_device_access(user, device, now)
    expires_at = device.access_expires_at if has_plan and user.access_type == "paid" else None

    # Step 8: token, only when there is an active plan. This runs last, so a failure
    # here leaves no login_ok record and the user can simply retry.
    # A repeated submission from this device gets its existing unused token back, so the
    # first response is not invalidated by the second. Otherwise a new token is issued.
    token = None
    expires_in = 0
    if has_plan:
        try:
            reusable = find_reusable_token(db, radius_db, r, radius_user, now)
            if reusable is not None:
                token, expires_in = reusable
            else:
                token = issue_token(
                    db=db,
                    radius_db=radius_db,
                    r=r,
                    now=now,
                    username=radius_user,
                    user_type=user.user_type,
                    mac=mac,
                    session_seconds=access.device_seconds_left(user, device, now),
                    ttl_seconds=settings.LOGIN_TOKEN_TTL_SECONDS,
                )
                expires_in = settings.LOGIN_TOKEN_TTL_SECONDS
        except (SQLAlchemyError, redis.RedisError):
            raise HTTPException(status_code=503, detail=UNAVAILABLE)

    # Steps 9 and 10. If this write fails the login still succeeded; only the audit row is lost.
    try:
        log_event(db, "login_ok" if has_plan else "expired", user.id, mac, device_ip, now)
        db.commit()
    except SQLAlchemyError:
        db.rollback()

    # Dashboard session, set for every successful sign-in, with or without a plan.
    set_session_cookie(response, user.id)

    return LoginResponse(
        username=radius_user,
        token=token,
        expires_in=expires_in if token else 0,
        has_active_plan=has_plan,
        access_expires_at=expires_at,
    )
