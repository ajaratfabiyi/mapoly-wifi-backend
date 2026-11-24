"""A paid device gets MAC-based access until its plan ends, and loses it when removed or expired."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.radius_database import RadiusSessionLocal, radius_engine
from app.services.login_tokens import radius_expiration
from app.services.radius import grant_mac_access, remove_device_access
from app.utils.time import utc_now
from radius_schema import radius_tables

MAC = "a4:c3:f0:88:91:30"
USERNAME = "A4:C3:F0:88:91:30"
RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_radius():
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    yield


def _rows(attribute: str, table: str = "radcheck") -> list[tuple]:
    with radius_engine.connect() as conn:
        return conn.execute(
            text(f"SELECT attribute, value FROM {table} WHERE username = :u AND attribute = :a"),
            {"u": USERNAME, "a": attribute},
        ).all()


def test_grant_writes_accept_expiry_and_session_limit():
    now = utc_now()
    expires = now + timedelta(hours=2)
    db = RadiusSessionLocal()
    try:
        grant_mac_access(db, MAC, expires, "restricted_users", now)
    finally:
        db.close()

    assert _rows("Auth-Type") == [("Auth-Type", "Accept")]
    assert _rows("Expiration") == [("Expiration", radius_expiration(expires))]
    session = _rows("Session-Timeout", table="radreply")
    assert len(session) == 1 and int(session[0][1]) in range(7199, 7201)


def test_grant_again_moves_expiry_without_duplicating_rows():
    now = utc_now()
    db = RadiusSessionLocal()
    try:
        grant_mac_access(db, MAC, now + timedelta(hours=2), "restricted_users", now)
        later = now + timedelta(days=2)
        grant_mac_access(db, MAC, later, "restricted_users", now)
    finally:
        db.close()

    assert len(_rows("Auth-Type")) == 1
    assert _rows("Expiration") == [("Expiration", radius_expiration(later))]


def test_remove_device_access_deletes_mac_login():
    now = utc_now()
    db = RadiusSessionLocal()
    try:
        grant_mac_access(db, MAC, now + timedelta(hours=2), "restricted_users", now)
        remove_device_access(db, MAC)
    finally:
        db.close()

    assert _rows("Auth-Type") == []
    assert _rows("Expiration") == []
