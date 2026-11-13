"""Router login tokens (Section 5.2, step 8).

Each token is written to three places:
- Redis, as login_token:<token>, expiring after the TTL
- the main database, in login_tokens, so the cleanup job can find unused tokens
- the radius database (radcheck, radreply, radusergroup), which FreeRADIUS reads

radcheck holds, per login:
- Cleartext-Password := <token>        FreeRADIUS deletes it when the token is accepted
- Calling-Station-Id == <device MAC>   FreeRADIUS rejects the token from any other MAC
- Simultaneous-Use := 1                one active session per device

The username is per device (<email>|<MAC>), so two bound devices can be online at once.
Only the rows for that device are replaced on its next login.
"""

import secrets
from datetime import datetime, timedelta

import redis
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import LoginToken
from app.utils.mac import to_radius_format

GROUP_RESTRICTED = "restricted_users"
GROUP_UNRESTRICTED = "unrestricted_users"


def group_for(user_type: str) -> str:
    """Gated users are unrestricted. Everyone else is capped (Section 7)."""
    return GROUP_UNRESTRICTED if user_type == "gated" else GROUP_RESTRICTED


def issue_token(
    db: Session,
    radius_db: Session,
    r: redis.Redis,
    username: str,
    user_type: str,
    mac: str,
    session_seconds: int | None,
    ttl_seconds: int,
    now: datetime,
) -> str:
    token = secrets.token_hex(32)  # 256 bits

    # Only one token per user may work at a time. Revoke earlier unused tokens first,
    # so logging in on a second device cannot leave the first device's token live.
    revoke_user_tokens(db, radius_db, r, username)

    # Redis first. If a later write fails, this key expires within the TTL and does nothing.
    r.set(f"login_token:{token}", username, ex=ttl_seconds)

    # Track the token before the RADIUS write. If RADIUS fails, cleanup still finds
    # this row and removes it, and nothing is left untracked.
    db.add(
        LoginToken(
            token=token,
            username=username,
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
        )
    )
    db.commit()

    radius_db.execute(
        text(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (:u, 'Cleartext-Password', ':=', :v)"
        ),
        {"u": username, "v": token},
    )
    # The token only works from the device it was issued to.
    radius_db.execute(
        text(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (:u, 'Calling-Station-Id', '==', :m)"
        ),
        {"u": username, "m": to_radius_format(mac)},
    )
    # One active session per account. Replace the row so it is never duplicated.
    radius_db.execute(
        text("DELETE FROM radcheck WHERE username = :u AND attribute = 'Simultaneous-Use'"),
        {"u": username},
    )
    radius_db.execute(
        text(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (:u, 'Simultaneous-Use', ':=', '1')"
        ),
        {"u": username},
    )

    radius_db.execute(
        text("DELETE FROM radreply WHERE username = :u AND attribute = 'Session-Timeout'"),
        {"u": username},
    )
    if session_seconds is not None:
        radius_db.execute(
            text(
                "INSERT INTO radreply (username, attribute, op, value) "
                "VALUES (:u, 'Session-Timeout', ':=', :v)"
            ),
            {"u": username, "v": str(session_seconds)},
        )

    radius_db.execute(text("DELETE FROM radusergroup WHERE username = :u"), {"u": username})
    radius_db.execute(
        text(
            "INSERT INTO radusergroup (username, groupname, priority) "
            "VALUES (:u, :g, 1)"
        ),
        {"u": username, "g": group_for(user_type)},
    )
    radius_db.commit()
    return token


def find_reusable_token(
    db: Session,
    radius_db: Session,
    r: redis.Redis,
    username: str,
    now: datetime,
) -> tuple[str, int] | None:
    """Return (token, seconds_left) for a token already issued to this device and still
    unused, or None. Used when the same device submits the login again (a double click
    or a retried request), so the first response's token is not rotated away.

    A token is reusable only if all three hold: it is tracked for this username and not
    expired, its Redis key still maps to this username, and its Cleartext-Password row is
    still in radcheck (FreeRADIUS deletes that row once the token is accepted).
    """
    rows = db.scalars(
        select(LoginToken).where(
            LoginToken.username == username,
            LoginToken.expires_at > now,
        )
    ).all()
    for row in rows:
        key = f"login_token:{row.token}"
        if r.get(key) != username:
            continue
        seconds_left = r.ttl(key)
        if seconds_left is None or seconds_left <= 0:
            continue
        unused = radius_db.execute(
            text(
                "SELECT 1 FROM radcheck "
                "WHERE username = :u AND attribute = 'Cleartext-Password' AND value = :v "
                "LIMIT 1"
            ),
            {"u": username, "v": row.token},
        ).first()
        if unused is not None:
            return row.token, int(seconds_left)
    return None


def revoke_user_tokens(db: Session, radius_db: Session, r: redis.Redis, username: str) -> int:
    """Remove every earlier token and device MAC row for this user. Returns the number
    of tracked tokens removed.

    The RADIUS rows are cleared even when no tracking row exists. The cleanup job deletes
    expired tracking rows but leaves their Calling-Station-Id rows, so checking the tracking
    table first would let those rows pile up as duplicates on each login.

    Each step only runs after the one before it succeeds, so a failure leaves the older
    token tracked and cleanup can still find it. Keeps Simultaneous-Use, which issue_token
    replaces. Commits the RADIUS delete; the caller commits the database delete with the
    new token.
    """
    old = db.scalars(select(LoginToken).where(LoginToken.username == username)).all()
    tokens = [row.token for row in old]

    radius_db.execute(
        text(
            "DELETE FROM radcheck "
            "WHERE username = :u "
            "AND attribute IN ('Cleartext-Password', 'Calling-Station-Id')"
        ),
        {"u": username},
    )
    radius_db.commit()

    if tokens:
        r.delete(*[f"login_token:{tok}" for tok in tokens])
        db.execute(delete(LoginToken).where(LoginToken.username == username))
    return len(tokens)
