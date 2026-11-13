"""Plans end when the account is used (login or dashboard), with no cron job.

Covers app/services/expiry.py and the RADIUS Expiration written with each login token.
Uses SQLite or PostgreSQL, as set by conftest.py.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.services.expiry import expire_user_devices
from app.services.radius import device_username
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
EMAIL = "lazy@mapoly.edu.ng"
MAC = "A4:C3:F0:88:91:20"
RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    get_redis().flushdb()
    yield


def make_user(hours_from_now: float) -> None:
    """A paid student with one device whose plan ends `hours_from_now` hours from now."""
    db = SessionLocal()
    now = utc_now()
    user = User(
        email=EMAIL,
        password_hash=hash_password(PASSWORD),
        status="active",
        user_type="student",
        access_type="paid",
        max_devices=2,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        UserDevice(
            user_id=user.id,
            mac_address=MAC,
            bound_at=now,
            access_expires_at=now + timedelta(hours=hours_from_now),
        )
    )
    db.commit()
    db.close()


def add_device_rows() -> None:
    """The RADIUS rows a device has after an earlier login."""
    name = device_username(EMAIL, MAC)
    with radius_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO radcheck (username, attribute, op, value) "
                "VALUES (:u, 'Cleartext-Password', ':=', 'old-token')"
            ),
            {"u": name},
        )
        conn.execute(
            text(
                "INSERT INTO radcheck (username, attribute, op, value) "
                "VALUES (:u, 'Calling-Station-Id', '==', 'A4-C3-F0-88-91-20')"
            ),
            {"u": name},
        )
        conn.execute(
            text(
                "INSERT INTO radusergroup (username, groupname, priority) "
                "VALUES (:u, 'restricted_users', 1)"
            ),
            {"u": name},
        )


def radcheck_count() -> int:
    with radius_engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM radcheck WHERE username = :u"),
            {"u": device_username(EMAIL, MAC)},
        ).scalar()


def login() -> dict:
    resp = TestClient(app).post(
        "/api/login",
        json={"email": EMAIL, "password": PASSWORD, "mac": MAC, "ip": "10.30.1.25"},
    )
    assert resp.status_code == 200
    return resp.json()


def test_expired_device_is_cleaned_up_on_login():
    make_user(hours_from_now=-1)  # the plan ended an hour ago
    add_device_rows()

    body = login()

    assert body["has_active_plan"] is False
    assert body["token"] is None
    assert radcheck_count() == 0

    db = SessionLocal()
    device = db.query(UserDevice).one()
    assert device.expiry_handled_for == device.access_expires_at
    db.close()


def test_expiry_is_handled_once():
    make_user(hours_from_now=-1)
    add_device_rows()
    db = SessionLocal()
    radius_db = Session(radius_engine)
    user = db.query(User).one()
    now = utc_now()

    assert expire_user_devices(db, radius_db, get_redis(), user, now) == 1
    assert expire_user_devices(db, radius_db, get_redis(), user, now) == 0

    db.close()
    radius_db.close()


def test_active_plan_is_left_alone():
    make_user(hours_from_now=5)
    add_device_rows()
    db = SessionLocal()
    radius_db = Session(radius_engine)
    user = db.query(User).one()

    assert expire_user_devices(db, radius_db, get_redis(), user, utc_now()) == 0
    assert radcheck_count() == 2

    db.close()
    radius_db.close()


def test_login_token_carries_its_own_expiry():
    make_user(hours_from_now=5)

    body = login()

    assert body["token"] is not None
    with radius_engine.connect() as conn:
        value = conn.execute(
            text(
                "SELECT value FROM radcheck "
                "WHERE username = :u AND attribute = 'Expiration'"
            ),
            {"u": device_username(EMAIL, MAC)},
        ).scalar()
    assert value is not None
    expires = datetime.strptime(value, "%b %d %Y %H:%M:%S")
    target = utc_now() + timedelta(seconds=get_settings().LOGIN_TOKEN_TTL_SECONDS)
    assert abs((expires - target).total_seconds()) < 120
