"""Sign-up and sign-in without a MAC, unbinding with confirmation, and plans that cover several devices.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import Plan, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
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


def login_body(email, mac=None, password=PASSWORD) -> dict:
    body = {"email": email, "password": password, "ip": "10.30.1.25"}
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


@pytest.fixture
def admin():
    add_user(ADMIN, is_admin=True)
    return client_for(ADMIN, MAC_ADMIN)


# ---------- sign-up and sign-in without a MAC ----------


def test_sign_up_without_a_mac_creates_the_account_with_no_device():
    resp = TestClient(app, base_url="https://testserver").post("/api/auth/register", json={
        "user_type": "student", "email": STUDENT, "password": PASSWORD,
    })
    assert resp.status_code == 201, resp.text
    assert "Account created" in resp.json()["message"]
    assert device_rows(STUDENT) == []


def test_sign_in_without_a_mac_opens_the_dashboard_and_binds_nothing():
    add_user(STUDENT)
    client = TestClient(app, base_url="https://testserver")

    resp = client.post("/api/login", json=login_body(STUDENT))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"] is None
    assert body["has_active_plan"] is False
    assert device_rows(STUDENT) == []
    assert client.get("/api/me").status_code == 200


def test_sign_in_with_a_mac_binds_it_to_the_account():
    add_user(STUDENT)
    assert device_rows(STUDENT) == []

    client_for(STUDENT, MAC_A)

    rows = device_rows(STUDENT)
    assert len(rows) == 1
    assert rows[0].mac_address == MAC_A


def test_sign_in_without_a_mac_still_rejects_a_wrong_password():
    add_user(STUDENT)
    resp = TestClient(app, base_url="https://testserver").post(
        "/api/login", json=login_body(STUDENT, password="wrong-pass"))
    assert resp.status_code == 401


# ---------- unbinding needs confirmation ----------


def test_unbinding_a_device_without_confirmation_is_refused():
    add_user(STUDENT)
    client = client_for(STUDENT, MAC_A)
    device_id = device_rows(STUDENT)[0].id

    resp = client.delete(f"/api/devices/{device_id}")

    assert resp.status_code == 422
    assert "Confirm" in resp.json()["detail"]
    assert len(device_rows(STUDENT)) == 1


def test_unbinding_a_device_with_confirmation_removes_it():
    add_user(STUDENT)
    client = client_for(STUDENT, MAC_A)
    device_id = device_rows(STUDENT)[0].id

    resp = client.delete(f"/api/devices/{device_id}?confirm=true")

    assert resp.status_code == 200, resp.text
    assert device_rows(STUDENT) == []


# ---------- plans that cover several devices ----------


def test_plan_list_shows_device_count():
    resp = TestClient(app, base_url="https://testserver").get("/api/plans")
    assert resp.status_code == 200
    assert all(p["device_count"] >= 1 for p in resp.json())


def test_admin_can_create_a_plan_for_several_devices(admin):
    resp = admin.post("/api/admin/plans", json={
        "id": "plan_family", "title": "Family Pass", "duration_hours": 168,
        "price_kobo": 300000, "device_count": 3,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["device_count"] == 3

    duplicate = admin.post("/api/admin/plans", json={
        "id": "plan_family", "title": "Again", "duration_hours": 24, "price_kobo": 1000,
    })
    assert duplicate.status_code == 422


def test_admin_can_change_a_plans_device_count(admin):
    resp = admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["device_count"] == 2


def test_one_purchase_covers_the_chosen_device_and_the_others_up_to_the_count(admin):
    # One student with four devices bound. A three-device plan covers the chosen device
    # and the two oldest others that have no active plan. The fourth stays uncovered.
    add_user(STUDENT)
    student = client_for(STUDENT, MAC_A)
    for mac in (MAC_B, MAC_C, MAC_D):
        assert student.post("/api/devices", json={"name": mac, "mac": mac}).status_code == 201

    admin.patch("/api/admin/plans/plan_weekly", json={"device_count": 3})
    chosen = next(d.id for d in device_rows(STUDENT) if d.mac_address == MAC_B)

    resp = admin.post(f"/api/admin/devices/{chosen}/grant", json={"plan_id": "plan_weekly"})
    assert resp.status_code == 200, resp.text

    covered = {d.mac_address for d in device_rows(STUDENT) if d.access_expires_at is not None}
    assert len(covered) == 3
    assert MAC_B in covered
    assert MAC_A in covered  # the oldest other device
    assert MAC_C in covered  # the next oldest
    assert MAC_D not in covered
