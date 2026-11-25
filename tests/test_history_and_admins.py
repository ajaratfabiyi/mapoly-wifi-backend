"""Student payment history and admin account creation.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import Ledger, User
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from radius_schema import radius_tables

PASSWORD = "secret123"
ADMIN = "ict@mapoly.edu.ng"
PAID = "paid@mapoly.edu.ng"
OTHER = "other@mapoly.edu.ng"
MAC_ADMIN = "A4:C3:F0:88:91:01"
MAC_PAID = "A4:C3:F0:88:91:02"
MAC_OTHER = "A4:C3:F0:88:91:03"

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
    password = fields.pop("password", PASSWORD)
    defaults = {"user_type": "student", "access_type": "paid", "is_active": True}
    defaults.update(fields)
    user = User(email=email, password_hash=hash_password(password), status="active",
                max_devices=None, **defaults)
    db.add(user)
    db.commit()
    uid = user.id
    db.close()
    return uid


def add_payment(uid, reference, amount_kobo=30000, status="success", method="card") -> None:
    db = SessionLocal()
    db.add(Ledger(reference=reference, user_id=uid, plan_id="plan_1day",
                  amount_kobo=amount_kobo, status=status, payment_method=method))
    db.commit()
    db.close()


def sign_in(email, mac, password=PASSWORD) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post("/api/login", json={"email": email, "password": password,
                                           "mac": mac, "ip": "10.30.1.25"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def admin():
    add_user(ADMIN, is_admin=True)
    return sign_in(ADMIN, MAC_ADMIN)


# ---------- payment history ----------


def test_payment_history_shows_only_my_payments():
    mine = add_user(PAID)
    theirs = add_user(OTHER)
    add_payment(mine, "pay-mine-1")
    add_payment(theirs, "pay-theirs-1")
    client = sign_in(PAID, MAC_PAID)

    resp = client.get("/api/me/payments")

    assert resp.status_code == 200, resp.text
    refs = [row["reference"] for row in resp.json()]
    assert refs == ["pay-mine-1"]


def test_payment_history_shows_amount_in_naira_and_method():
    uid = add_user(PAID)
    add_payment(uid, "pay-card-1", amount_kobo=30000, method="card")
    add_payment(uid, "points_20261125_abc", amount_kobo=30000, status="success", method=None)
    add_payment(uid, "admin_20261125_def", amount_kobo=0, status="success", method=None)
    client = sign_in(PAID, MAC_PAID)

    rows = {row["reference"]: row for row in client.get("/api/me/payments").json()}

    assert rows["pay-card-1"]["amount_naira"] == 300
    assert rows["pay-card-1"]["method"] == "card"
    assert rows["pay-card-1"]["plan_title"] == "1-Day Pass"
    assert rows["points_20261125_abc"]["method"] == "points"
    assert rows["admin_20261125_def"]["method"] == "admin"


def test_payment_history_needs_sign_in():
    resp = TestClient(app, base_url="https://testserver").get("/api/me/payments")
    assert resp.status_code == 401


# ---------- creating admins ----------


def test_admin_creates_an_admin_who_signs_in_with_the_temporary_password(admin):
    resp = admin.post("/api/admin/admins", json={
        "email": "new.admin@mapoly.edu.ng",
        "full_name": "New Admin",
        "temporary_password": "Temp-2026",
    })

    assert resp.status_code == 201, resp.text
    db = SessionLocal()
    user = db.query(User).filter(User.email == "new.admin@mapoly.edu.ng").one()
    assert user.is_admin is True
    assert user.must_change_password is True
    db.close()
    sign_in("new.admin@mapoly.edu.ng", MAC_OTHER, password="Temp-2026")


def test_creating_an_admin_with_a_taken_email_is_refused(admin):
    resp = admin.post("/api/admin/admins", json={
        "email": ADMIN, "full_name": "Again", "temporary_password": "Temp-2026",
    })
    assert resp.status_code == 422


def test_non_admin_cannot_create_an_admin():
    add_user(PAID)
    client = sign_in(PAID, MAC_PAID)
    resp = client.post("/api/admin/admins", json={
        "email": "sneaky@mapoly.edu.ng", "full_name": "Sneaky", "temporary_password": "Temp-2026",
    })
    assert resp.status_code == 403


# ---------- user counts for the admin dashboard ----------


def test_user_counts_by_type_and_status(admin):
    add_user(PAID)
    add_user(OTHER, user_type="lecturer", access_type="free")
    add_user("gated@mapoly.edu.ng", user_type="gated", access_type="free", is_active=False)

    resp = admin.get("/api/admin/users/counts")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert body["by_type"]["student"] == 2  # the admin is a student type too
    assert body["by_type"]["lecturer"] == 1
    assert body["by_type"]["guest"] == 0
    assert body["by_type"]["gated"] == 1
    assert body["admins"] == 1
    assert body["disabled"] == 1
    assert body["active"] == 3
    assert body["paid"] == 2
    assert body["free"] == 2


def test_user_counts_is_not_caught_by_single_user_route(admin):
    resp = admin.get("/api/admin/users/counts")
    assert resp.status_code == 200, resp.text
    assert "by_type" in resp.json()


def test_user_counts_refused_for_non_admin():
    add_user(PAID)
    client = sign_in(PAID, MAC_PAID)
    assert client.get("/api/admin/users/counts").status_code == 403
