"""Removing RADIUS rows (Sections 4 and 7).

FreeRADIUS reads the radius database. The backend only writes to it here, for:
- plan expiry: remove a user's tokens, reply attributes, and group
- device removal: remove rows tied to that device's MAC

Radacct is written by FreeRADIUS and is never touched here.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.login_tokens import radius_expiration
from app.utils.mac import normalise_mac, to_radius_format

USER_TABLES = ("radcheck", "radreply", "radusergroup")

# Each device signs in with its own RADIUS username: <email>|<MAC in AA-BB-… form>.
# That gives each device its own token, MAC check, and single-session rule.
DEVICE_SEPARATOR = "|"


def device_username(email: str, mac: str) -> str:
    return f"{email}{DEVICE_SEPARATOR}{to_radius_format(mac)}"


def account_match_params(email: str) -> dict:
    """Parameters for ACCOUNT_MATCH. Matches the bare email and every device username.

    A prefix comparison is used instead of LIKE. LIKE needs escape characters for
    email text such as "_" and "%", and those escapes differ between MySQL and SQLite.
    Comparing a substring has no such problem.
    """
    prefix = email + DEVICE_SEPARATOR
    return {"u": email, "p": prefix, "n": len(prefix)}


# Column `username` matches the account: the bare email, or "<email>|<device>".
ACCOUNT_MATCH = "(username = :u OR substr(username, 1, :n) = :p)"


def remove_user_access(radius_db: Session, email: str) -> None:
    """Delete every token, reply attribute, and group row for this account's devices."""
    for table in USER_TABLES:
        radius_db.execute(
            text(f"DELETE FROM {table} WHERE {ACCOUNT_MATCH}"),
            account_match_params(email),
        )
    radius_db.commit()


def remove_device_access(radius_db: Session, mac: str) -> None:
    """Delete every row for this device: its MAC rows and its per-device username rows.

    Calling-Station-Id uses the router's colon format (AA:BB:...). Usernames use dashes.
    """
    radius_db.execute(
        text(
            "DELETE FROM radcheck "
            "WHERE attribute = 'Calling-Station-Id' AND value = :m"
        ),
        {"m": normalise_mac(mac)},
    )
    for table in USER_TABLES:
        radius_db.execute(
            text(f"DELETE FROM {table} WHERE username LIKE :p"),
            {"p": "%" + DEVICE_SEPARATOR + to_radius_format(mac)},
        )
    # The MAC-login row (see grant_mac_access) is keyed by the MAC itself.
    for table in USER_TABLES:
        radius_db.execute(
            text(f"DELETE FROM {table} WHERE username = :m"),
            {"m": normalise_mac(mac)},
        )
    radius_db.commit()


def grant_mac_access(
    radius_db: Session,
    mac: str,
    expires_at: datetime,
    group: str,
    now: datetime,
) -> None:
    """Let this device onto the network without a login until expires_at.

    The router sends the device's MAC as the username (radius-mac-format XX:XX:...).
    Auth-Type := Accept lets FreeRADIUS accept it without a password, and the
    Expiration row ends access when the plan ends. Re-running this replaces the rows,
    so a renewal simply moves the expiry on.
    """
    username = normalise_mac(mac)
    seconds = max(1, int((expires_at - now).total_seconds()))
    for table in USER_TABLES:
        radius_db.execute(text(f"DELETE FROM {table} WHERE username = :u"), {"u": username})
    radius_db.execute(
        text(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (:u, 'Auth-Type', ':=', 'Accept')"
        ),
        {"u": username},
    )
    radius_db.execute(
        text(
            "INSERT INTO radcheck (username, attribute, op, value) "
            "VALUES (:u, 'Expiration', ':=', :v)"
        ),
        {"u": username, "v": radius_expiration(expires_at)},
    )
    radius_db.execute(
        text(
            "INSERT INTO radreply (username, attribute, op, value) "
            "VALUES (:u, 'Session-Timeout', ':=', :v)"
        ),
        {"u": username, "v": str(seconds)},
    )
    radius_db.execute(
        text(
            "INSERT INTO radusergroup (username, groupname, priority) "
            "VALUES (:u, :g, 1)"
        ),
        {"u": username, "g": group},
    )
    radius_db.commit()


CLOSE_ON_LOGIN_CAUSE = "Lost-Service"


def close_device_sessions(radius_db: Session, mac: str, now) -> int:
    """Close this device's open radacct sessions before a new login.

    A device can have only one session. If the router missed the stop message for an
    earlier session, that record would block the new login. A new login means the old
    session is over, so close it now. Returns the number of rows closed.
    """
    result = radius_db.execute(
        text(
            "UPDATE radacct SET acctstoptime = :now, acctterminatecause = :cause "
            "WHERE callingstationid = :m AND acctstoptime IS NULL"
        ),
        {"now": now, "cause": CLOSE_ON_LOGIN_CAUSE, "m": normalise_mac(mac)},
    )
    radius_db.commit()
    return result.rowcount or 0
