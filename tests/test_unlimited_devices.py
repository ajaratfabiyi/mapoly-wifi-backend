"""An account with no device limit (max_devices NULL) can bind as many devices as it needs.
An account with a number still stops at that number.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.db_setup import allow_unlimited_devices
from app.main import app
from app.models import Base, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
EMAIL = "many@mapoly.edu.ng"
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


def make_user(max_devices):
    db = SessionLocal()
    db.add(
        User(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            status="active",
            user_type="student",
            access_type="paid",
            max_devices=max_devices,
            is_active=True,
        )
    )
    db.commit()
    db.close()


def login_from(mac: str):
    return TestClient(app).post(
        "/api/login",
        json={"email": EMAIL, "password": PASSWORD, "mac": mac, "ip": "10.30.1.25"},
    )


def test_unlimited_account_binds_many_devices():
    make_user(max_devices=None)
    for last_octet in range(1, 6):
        resp = login_from(f"A4:C3:F0:88:91:{last_octet:02X}")
        assert resp.status_code == 200, resp.text

    db = SessionLocal()
    assert db.query(UserDevice).count() == 5
    db.close()


def test_numbered_limit_still_applies():
    make_user(max_devices=2)
    assert login_from("A4:C3:F0:88:91:01").status_code == 200
    assert login_from("A4:C3:F0:88:91:02").status_code == 200
    resp = login_from("A4:C3:F0:88:91:03")
    assert resp.status_code == 409


def test_migration_allows_null_and_is_idempotent():
    allow_unlimited_devices(engine)
    allow_unlimited_devices(engine)
    make_user(max_devices=None)
    db = SessionLocal()
    assert db.query(User).one().max_devices is None
    db.close()
