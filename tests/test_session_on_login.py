"""A new login closes any stuck open session on the same device (no cron job needed)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
EMAIL = "stuck@mapoly.edu.ng"
MAC = "A4:C3:F0:88:91:30"
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


def test_login_closes_stuck_session_on_same_device():
    db = SessionLocal()
    user = User(email=EMAIL, password_hash=hash_password(PASSWORD), status="active",
                user_type="student", access_type="paid", max_devices=2, is_active=True)
    db.add(user)
    db.flush()
    db.add(UserDevice(user_id=user.id, mac_address=MAC, bound_at=utc_now(),
                      access_expires_at=utc_now() + timedelta(hours=5)))
    db.commit()
    db.close()

    with radius_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO radacct (username, callingstationid, acctstoptime) "
            "VALUES ('x', 'A4-C3-F0-88-91-30', NULL)"))

    resp = TestClient(app).post("/api/login", json={
        "email": EMAIL, "password": PASSWORD, "mac": MAC, "ip": "10.30.1.25"})
    assert resp.status_code == 200

    with radius_engine.connect() as conn:
        open_rows = conn.execute(text(
            "SELECT COUNT(*) FROM radacct WHERE callingstationid = 'A4-C3-F0-88-91-30' "
            "AND acctstoptime IS NULL")).scalar()
    assert open_rows == 0
