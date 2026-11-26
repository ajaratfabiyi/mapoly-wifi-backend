"""Dashboard session cookie (decision 7).

Format: <user_id>.<issued_at_unix>.<hex HMAC-SHA256 signature>
Signed with SECRET_KEY. Nothing is stored server-side, so a session cannot be
revoked before it expires. Password change does not revoke it either; add a
session version on the user if that matters.

SameSite is "none", not "lax". The frontend (vercel.app) and the API (eduwifi.name.ng)
are different sites, so the browser sends a Lax cookie on no cross-site fetch.
"None" requires Secure, which production always is (HTTPS only).

Caveat: Safari (iPhone and Mac) blocks third-party cookies even with SameSite=None, so
the dashboard cannot stay signed in there while the frontend and API are on different sites.
Serving the frontend from a subdomain of eduwifi.name.ng fixes this.
"""

import hashlib
import hmac
import time

from fastapi import Response

from app.config import get_settings

COOKIE_NAME = "mapoly_session"
MAX_AGE_SECONDS = 7 * 24 * 3600
COOKIE_SAMESITE = "none"


def _sign(payload: str) -> str:
    key = get_settings().SECRET_KEY.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def create_session_value(user_id: int, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = f"{user_id}.{issued}"
    return f"{payload}.{_sign(payload)}"


def read_session_value(value: str | None, now: int | None = None) -> int | None:
    """Return the user id if the cookie is genuine and unexpired, otherwise None."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    user_id_s, issued_s, signature = parts
    payload = f"{user_id_s}.{issued_s}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        user_id = int(user_id_s)
        issued = int(issued_s)
    except ValueError:
        return None
    current = int(now if now is not None else time.time())
    if current - issued > MAX_AGE_SECONDS or issued > current + 60:
        return None
    return user_id


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_session_value(user_id),
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite=COOKIE_SAMESITE,
    )
