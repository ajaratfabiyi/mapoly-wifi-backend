"""Admin account delete and database reset.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import AdminAction, Ledger, Plan, User
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from radius_schema import radius_tables

PASSWORD = "secret123"
ADMIN = "ict@mapoly.edu.ng"
STAFF = "staff@mapoly.edu.ng"
PAID = "paid@mapoly.edu.ng"
MAC_ADMIN = "A4:C3:F0:88:91:01"
MAC_PAID = "A4:C3:F0:88:91:02"
MAC_STAFF = "A4:C3:F0:88:91:03"

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


def sign_in(email, mac) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post("/api/login", json={"email": email, "password": PASSWORD,
                                           "mac": mac, "ip": "10.30.1.25"})
    assert resp.status_code == 200, resp.text
    return client


def uid_of(email):
    db = SessionLocal()
    uid = db.scalar(select(User.id).where(User.email == email))
    db.close()
    return uid


def exists(email) -> bool:
    return uid_of(email) is not None


def add_payment(uid, reference) -> None:
    db = SessionLocal()
    db.add(Ledger(reference=reference, user_id=uid, plan_id="plan_1day",
                  amount_kobo=30000, status="success", payment_method="card"))
    db.commit()
    db.close()


@pytest.fixture
def admin():
    add_user(ADMIN, is_admin=True)
    return sign_in(ADMIN, MAC_ADMIN)


# ---------- delete one account ----------


def test_delete_removes_the_account_and_keeps_its_payments(admin):
    uid = add_user(PAID)
    add_payment(uid, "ref-keep-1")

    resp = admin.post(f"/api/admin/users/{uid}/delete", json={"confirm_email": PAID})

    assert resp.status_code == 204, resp.text
    assert not exists(PAID)
    db = SessionLocal()
    payment = db.scalar(select(Ledger).where(Ledger.reference == "ref-keep-1"))
    assert payment is not None
    assert payment.user_id is None
    assert payment.payer_email == PAID
    db.close()


def test_delete_needs_the_email_typed_exactly(admin):
    uid = add_user(PAID)
    resp = admin.post(f"/api/admin/users/{uid}/delete", json={"confirm_email": "wrong@x.com"})
    assert resp.status_code == 422
    assert exists(PAID)


def test_admin_cannot_delete_own_account(admin):
    resp = admin.post(f"/api/admin/users/{uid_of(ADMIN)}/delete", json={"confirm_email": ADMIN})
    assert resp.status_code == 422
    assert exists(ADMIN)


def test_admin_accounts_cannot_be_deleted(admin):
    other_admin = add_user("second.admin@mapoly.edu.ng", is_admin=True)
    resp = admin.post(f"/api/admin/users/{other_admin}/delete",
                      json={"confirm_email": "second.admin@mapoly.edu.ng"})
    assert resp.status_code == 422
    assert exists("second.admin@mapoly.edu.ng")


def test_delete_is_recorded_in_the_audit_log(admin):
    uid = add_user(PAID)
    admin.post(f"/api/admin/users/{uid}/delete", json={"confirm_email": PAID})
    db = SessionLocal()
    action = db.scalar(select(AdminAction).where(AdminAction.action == "delete_account"))
    assert action is not None
    assert PAID in action.detail
    db.close()


# ---------- database reset ----------


def test_reset_needs_the_word_reset(admin):
    add_user(PAID)
    assert admin.post("/api/admin/database/reset", json={"confirm": "yes"}).status_code == 422
    assert exists(PAID)


def test_reset_keeps_admins_and_plans_and_removes_everyone_else(admin):
    add_user(PAID)
    add_user(STAFF)
    uid = add_user("another.admin@mapoly.edu.ng", is_admin=True)
    add_payment(uid_of(PAID), "ref-reset-1")

    resp = admin.post("/api/admin/database/reset", json={"confirm": "RESET"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["accounts_deleted"] == 2
    assert exists(ADMIN)
    assert exists("another.admin@mapoly.edu.ng")
    assert not exists(PAID)
    assert not exists(STAFF)
    db = SessionLocal()
    assert db.scalar(select(Plan).where(Plan.id == "plan_1day")) is not None
    payment = db.scalar(select(Ledger).where(Ledger.reference == "ref-reset-1"))
    assert payment is not None and payment.user_id is None and payment.payer_email == PAID
    db.close()


def test_non_admin_cannot_reset(admin):
    add_user(STAFF)
    client = sign_in(STAFF, MAC_STAFF)
    assert client.post("/api/admin/database/reset", json={"confirm": "RESET"}).status_code == 403
    assert exists(STAFF)
