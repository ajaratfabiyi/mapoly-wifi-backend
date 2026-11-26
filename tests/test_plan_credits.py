"""A plan bought with no device, or covering more devices than are bound, links to devices.

Covers: a purchase without a device waits on the account; the first MAC bound takes it;
a purchase covers the named device and the others by priority; a device bound later takes
an open slot; devices can be put in priority order.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import AccountPlanCredit, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
STUDENT = "student@mapoly.edu.ng"
ADMIN = "ict@mapoly.edu.ng"
MAC_ADMIN = "A4:C3:F0:88:91:01"
MAC_A = "A4:C3:F0:88:91:02"
MAC_B = "A4:C3:F0:88:91:03"
MAC_C = "A4:C3:F0:88:91:04"
MAC_D = "A4:C3:F0:88:91:05"

RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_state():
    from app.models import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    seed_plans(engine)
    get_redis().flushdb()
    yield


def add_user(email, **fields) -> int:
    db = SessionLocal()
    user = User(email=email, password_hash=hash_password(PASSWORD), status="active",
                user_type="student", access_type="paid", is_active=True, max_devices=None,
                **fields)
    db.add(user)
    db.commit()
    uid = user.id
    db.close()
    return uid


def login_body(email, mac=None) -> dict:
    body = {"email": email, "password": PASSWORD, "ip": "10.30.1.25"}
    if mac is not None:
        body["mac"] = mac
    return body


def client_for(email, mac=None) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post("/api/login", json=login_body(email, mac))
    assert resp.status_code == 200, resp.text
    return client


def device_rows(email) -> list[UserDevice]:
    db = SessionLocal()
    rows = db.scalars(
        select(UserDevice).join(User).where(User.email == email).order_by(UserDevice.id)
    ).all()
    db.close()
    return rows


def device_by_mac(email, mac) -> UserDevice:
    return next(d for d in device_rows(email) if d.mac_address == mac)


def set_points(email, points):
    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    user.points = points
    db.commit()
    db.close()


def open_slots(email) -> list[int]:
    db = SessionLocal()
    uid = db.scalar(select(User.id).where(User.email == email))
    rows = db.scalars(select(AccountPlanCredit).where(AccountPlanCredit.user_id == uid)).all()
    slots = [c.slots_left for c in rows]
    db.close()
    return slots


def hours_left(device: UserDevice) -> float:
    now = utc_now().replace(tzinfo=None)
    return (device.access_expires_at - now).total_seconds() / 3600


@pytest.fixture
def admin():
    add_user(ADMIN, is_admin=True)
    return client_for(ADMIN, MAC_ADMIN)


def test_purchase_without_a_device_waits_on_the_account_until_a_mac_is_bound(admin):
    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 2})
    add_user(STUDENT)
    set_points(STUDENT, 5000)
    student = TestClient(app, base_url="https://testserver")
    assert student.post("/api/login", json=login_body(STUDENT)).status_code == 200

    resp = student.post("/api/points/redeem", json={"plan_id": "plan_weekly"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["access_expires_at"] is None
    assert device_rows(STUDENT) == []
    assert open_slots(STUDENT) == [2]

    client_for(STUDENT, MAC_A)

    first = device_by_mac(STUDENT, MAC_A)
    assert first.access_expires_at is not None
    assert 167 < hours_left(first) <= 168
    assert open_slots(STUDENT) == [1]


def test_a_device_bound_after_the_plan_ends_gets_nothing(admin):
    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 2})
    add_user(STUDENT)
    set_points(STUDENT, 5000)
    student = TestClient(app, base_url="https://testserver")
    assert student.post("/api/login", json=login_body(STUDENT)).status_code == 200
    assert student.post("/api/points/redeem", json={"plan_id": "plan_weekly"}).status_code == 200

    db = SessionLocal()
    credit = db.scalar(select(AccountPlanCredit))
    credit.expires_at = utc_now().replace(tzinfo=None) - timedelta(hours=1)
    db.commit()
    db.close()

    client_for(STUDENT, MAC_A)

    assert device_by_mac(STUDENT, MAC_A).access_expires_at is None
    assert open_slots(STUDENT) == [2]  # the open slots were not used and are now gone


def test_a_device_bound_later_takes_an_open_slot(admin):
    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 2})
    add_user(STUDENT)
    set_points(STUDENT, 5000)
    student = client_for(STUDENT, MAC_A)
    assert student.post("/api/points/redeem", json={"plan_id": "plan_weekly"}).status_code == 200
    # The device already bound has no plan, so it is covered at once and starts its clock.
    assert device_by_mac(STUDENT, MAC_A).access_expires_at is not None
    assert open_slots(STUDENT) == [1]

    # One slot is open, so the next device bound takes it, and the one after gets nothing.
    assert student.post("/api/devices", json={"name": "b", "mac": MAC_B}).status_code == 201
    assert device_by_mac(STUDENT, MAC_B).access_expires_at is not None
    assert open_slots(STUDENT) == [0]

    assert student.post("/api/devices", json={"name": "c", "mac": MAC_C}).status_code == 201
    assert device_by_mac(STUDENT, MAC_C).access_expires_at is None


def test_purchase_covers_the_named_device_and_the_others_then_keeps_the_rest(admin):
    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 3})
    add_user(STUDENT)
    set_points(STUDENT, 5000)
    student = client_for(STUDENT, MAC_A)
    assert student.post("/api/devices", json={"name": "b", "mac": MAC_B}).status_code == 201
    chosen = device_by_mac(STUDENT, MAC_B).id

    resp = student.post("/api/points/redeem", json={"plan_id": "plan_weekly", "device_id": chosen})

    assert resp.status_code == 200, resp.text
    assert device_by_mac(STUDENT, MAC_B).access_expires_at is not None
    assert device_by_mac(STUDENT, MAC_A).access_expires_at is not None
    assert open_slots(STUDENT) == [1]

    assert student.post("/api/devices", json={"name": "c", "mac": MAC_C}).status_code == 201
    assert device_by_mac(STUDENT, MAC_C).access_expires_at is not None
    assert open_slots(STUDENT) == [0]


def test_priority_decides_which_devices_a_purchase_covers(admin):
    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 2})
    add_user(STUDENT)
    set_points(STUDENT, 5000)
    student = client_for(STUDENT, MAC_A)
    assert student.post("/api/devices", json={"name": "b", "mac": MAC_B}).status_code == 201
    assert student.post("/api/devices", json={"name": "c", "mac": MAC_C}).status_code == 201
    c_id = device_by_mac(STUDENT, MAC_C).id
    a_id = device_by_mac(STUDENT, MAC_A).id

    order = student.put("/api/devices/priority", json={"device_ids": [c_id, a_id]})
    assert order.status_code == 200, order.text

    resp = student.post("/api/points/redeem", json={"plan_id": "plan_weekly"})

    assert resp.status_code == 200, resp.text
    assert device_by_mac(STUDENT, MAC_C).access_expires_at is not None
    assert device_by_mac(STUDENT, MAC_A).access_expires_at is not None
    assert device_by_mac(STUDENT, MAC_B).access_expires_at is None
    assert open_slots(STUDENT) == []  # both slots were used, so no credit is kept


def test_priority_refuses_a_device_that_is_not_the_students(admin):
    add_user(STUDENT)
    student = client_for(STUDENT, MAC_A)
    own = device_by_mac(STUDENT, MAC_A).id

    resp = student.put("/api/devices/priority", json={"device_ids": [own, 99999]})

    assert resp.status_code == 422
