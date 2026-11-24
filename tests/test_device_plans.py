"""A plan belongs to one device. It works on that device and not on the account's others.

Uses SQLite (set by conftest.py) and Redis, as the other route tests do.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import Base, Ledger, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.services.payments import APPLIED, apply_successful_charge
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
EMAIL = "student@mapoly.edu.ng"
PHONE = "A4:C3:F0:88:91:01"
LAPTOP = "A4:C3:F0:88:91:02"

RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    seed_plans(engine)  # the ledger row needs a real plan to point at
    get_redis().flushdb()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def make_user_with_phone_plan() -> None:
    """A paid student whose phone has a 48-hour plan. The laptop has no plan."""
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
            mac_address=PHONE,
            bound_at=now,
            access_expires_at=now + timedelta(hours=48),
        )
    )
    db.commit()
    db.close()


def login(client, mac):
    return client.post(
        "/api/login",
        json={"email": EMAIL, "password": PASSWORD, "mac": mac, "ip": "10.30.1.25"},
    )


def test_plan_on_phone_does_not_let_laptop_connect(client):
    make_user_with_phone_plan()

    phone = login(client, PHONE)
    assert phone.status_code == 200
    assert phone.json()["has_active_plan"] is True
    assert phone.json()["token"] is not None

    laptop = login(client, LAPTOP)
    assert laptop.status_code == 200
    assert laptop.json()["has_active_plan"] is False
    assert laptop.json()["token"] is None


def test_charge_extends_only_the_device_it_paid_for(client):
    make_user_with_phone_plan()
    login(client, LAPTOP)  # binds the laptop with no plan yet

    now = utc_now()
    db = SessionLocal()
    laptop_device = db.scalar(select(UserDevice).where(UserDevice.mac_address == LAPTOP))
    user = db.scalar(select(User).where(User.email == EMAIL))
    db.add(
        Ledger(
            reference="ref_laptop",
            user_id=user.id,
            device_id=laptop_device.id,
            plan_id="plan_weekly",
            amount_kobo=200000,
            status="pending",
            created_at=now,
        )
    )
    db.commit()
    db.close()

    db = SessionLocal()
    result = apply_successful_charge(db, "ref_laptop", 200000, now, now)
    db.close()
    assert result == APPLIED

    db = SessionLocal()
    phone = db.scalar(select(UserDevice).where(UserDevice.mac_address == PHONE))
    laptop = db.scalar(select(UserDevice).where(UserDevice.mac_address == LAPTOP))
    phone_expiry, laptop_expiry = phone.access_expires_at, laptop.access_expires_at
    db.close()

    # The phone's 48-hour plan is untouched. The laptop now has its own week.
    # Allow a second of difference in case a database rounds timestamps.
    assert abs((laptop_expiry - (now + timedelta(hours=168))).total_seconds()) < 1
    assert phone_expiry != laptop_expiry

    assert login(client, LAPTOP).json()["has_active_plan"] is True
