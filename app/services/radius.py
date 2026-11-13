"""Removing RADIUS rows (Sections 4 and 7).

FreeRADIUS reads the radius database. The backend only writes to it here, for:
- plan expiry: remove a user's tokens, reply attributes, and group
- device removal: remove rows tied to that device's MAC

Radacct is written by FreeRADIUS and is never touched here.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.mac import to_radius_format

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

    Stored in RADIUS format (AA-BB-…), which is what the router sends.
    """
    radius_db.execute(
        text(
            "DELETE FROM radcheck "
            "WHERE attribute = 'Calling-Station-Id' AND value = :m"
        ),
        {"m": to_radius_format(mac)},
    )
    for table in USER_TABLES:
        radius_db.execute(
            text(f"DELETE FROM {table} WHERE username LIKE :p"),
            {"p": "%" + DEVICE_SEPARATOR + to_radius_format(mac)},
        )
    radius_db.commit()
