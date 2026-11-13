"""Route tests for POST /api/login (Section 10, tests 4 to 7, 9, 15 and the MAC rules).

These use SQLite (set by conftest.py) and Redis (REDIS_URL).
Run them against throwaway databases only.
"""

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
from app.services.devices import bind_device  # noqa: F401  (imported so models load)
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
INVALID = "Invalid email or password."
DEVICE_OWNED = "This device is already linked to another account. Contact ICT."
DEVICE_LIMIT = "You have reached the device limit. Remove a device from your dashboard first."

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


@pytest.fixture
def client():
    return TestClient(app)


def make_user(
    email="student@mapoly.edu.ng",
    paid_hours=None,
    status="active",
    user_type="student",
    plan_mac=None,
):
    """Create a user. With paid_hours, the device `plan_mac` (default: the login MAC)
    is bound with a plan. A MAC can belong to only one account, so pass plan_mac when
    creating a second account that also needs a device with a plan."""
    db = SessionLocal()
    now = utc_now()
    user = User(
        email=email,
        password_hash=None if status == "pre_registered" else hash_password(PASSWORD),
        status=status,
        user_type=user_type,
        access_type="paid",
        max_devices=2,
        is_active=True,
    )
    db.add(user)
    db.flush()
    if paid_hours:
        db.add(
            UserDevice(
                user_id=user.id,
                mac_address=plan_mac or "A4:C3:F0:88:91:2E",
                bound_at=now,
                access_expires_at=now + timedelta(hours=paid_hours),
            )
        )
    db.commit()
    db.close()


def login_body(email="student@mapoly.edu.ng", mac="A4:C3:F0:88:91:2E", password=PASSWORD):
    return {"email": email, "password": password, "mac": mac, "ip": "10.30.1.25"}


def radius_count(sql, params):
    with radius_engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def test_wrong_password_returns_401(client):
    make_user()
    resp = client.post("/api/login", json=login_body(password="wrong-pass"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID


def test_unknown_email_returns_same_401(client):
    resp = client.post("/api/login", json=login_body(email="nobody@mapoly.edu.ng"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID


def test_no_plan_returns_no_token(client):  # test 5
    make_user(paid_hours=None)
    resp = client.post("/api/login", json=login_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_active_plan"] is False
    assert body["token"] is None


def test_active_plan_issues_token_and_radcheck_row(client):  # test 6
    make_user(paid_hours=48)
    resp = client.post("/api/login", json=login_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_active_plan"] is True
    assert len(body["token"]) == 64
    assert body["access_expires_at"].endswith("Z")

    assert radius_count(
        "SELECT COUNT(*) FROM radcheck WHERE username = :u AND value = :t",
        {"u": "student@mapoly.edu.ng|A4-C3-F0-88-91-2E", "t": body["token"]},
    ) == 1
    assert radius_count(
        "SELECT COUNT(*) FROM radusergroup WHERE username = :u AND groupname = 'restricted_users'",
        {"u": "student@mapoly.edu.ng|A4-C3-F0-88-91-2E"},
    ) == 1


def test_third_device_is_refused(client):  # test 7
    # The account already has one device (the login MAC, with a plan). The second device
    # is allowed; the third must be refused. Here the second is 01, so 02 is the third.
    make_user(paid_hours=48)
    assert client.post("/api/login", json=login_body(mac="A4:C3:F0:88:91:01")).status_code == 200
    resp = client.post("/api/login", json=login_body(mac="A4:C3:F0:88:91:02"))
    assert resp.status_code == 409
    assert resp.json()["detail"] == DEVICE_LIMIT


def test_mac_owned_by_another_account_is_refused(client):
    make_user(email="a@mapoly.edu.ng", paid_hours=48)  # binds the login MAC to a
    make_user(email="b@mapoly.edu.ng")                 # b has no devices yet
    assert client.post("/api/login", json=login_body(email="a@mapoly.edu.ng")).status_code == 200
    resp = client.post("/api/login", json=login_body(email="b@mapoly.edu.ng"))
    assert resp.status_code == 409
    assert resp.json()["detail"] == DEVICE_OWNED


def test_invalid_mac_returns_422_before_any_work(client):
    resp = client.post("/api/login", json=login_body(mac="not-a-mac"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Invalid device address."


def test_pre_registered_account_must_register_first(client):
    make_user(status="pre_registered")
    resp = client.post("/api/login", json=login_body())
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Please complete your registration first."


def test_sixth_failed_attempt_is_rate_limited(client):  # test 15
    make_user()
    for _ in range(5):
        assert client.post("/api/login", json=login_body(password="wrong-pass")).status_code == 401
    resp = client.post("/api/login", json=login_body(password="wrong-pass"))
    assert resp.status_code == 429
