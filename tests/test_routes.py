"""Route tests for account, devices, plans, payments and the webhook.

Uses SQLite (set by conftest.py) and Redis (REDIS_URL) at
throwaway databases. Paystack and CoA are mocked, so no network calls are made.
"""

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, Ledger, Plan, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
MAC1 = "A4:C3:F0:88:91:01"
MAC2 = "A4:C3:F0:88:91:02"
MAC3 = "A4:C3:F0:88:91:03"

RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    db = SessionLocal()
    db.add_all(
        [
            Plan(id="plan_2days", title="2-Day Campus Pass", duration_hours=48,
                 price_kobo=40000, speed_limit_mbps=25, is_active=True),
            Plan(id="plan_weekly", title="Weekly Academic Pass", duration_hours=168,
                 price_kobo=120000, speed_limit_mbps=35, is_active=True),
        ]
    )
    db.commit()
    db.close()
    get_redis().flushdb()
    yield


@pytest.fixture
def make_client():
    """Each call gives a fresh client, so each user has its own cookie jar.

    base_url is https because the session cookie is Secure."""

    def _make():
        return TestClient(app, base_url="https://testserver")

    return _make


def make_user(
    email="student@mapoly.edu.ng",
    status="active",
    user_type="student",
    access_type="paid",
    paid_hours=None,
    plan_mac=None,
) -> int:
    db = SessionLocal()
    now = utc_now()
    user = User(
        email=email,
        password_hash=None if status == "pre_registered" else hash_password(PASSWORD),
        status=status,
        user_type=user_type,
        access_type=access_type,
        max_devices=2,
        is_active=True,
    )
    db.add(user)
    db.flush()
    if paid_hours:
        # A plan belongs to a device. MAC1 is the default device these tests sign in with.
        # A MAC can belong to one account only, so a second paid account passes its own MAC.
        db.add(
            UserDevice(
                user_id=user.id,
                mac_address=plan_mac or MAC1,
                bound_at=now,
                access_expires_at=now + timedelta(hours=paid_hours),
            )
        )
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def sign_in(client, email="student@mapoly.edu.ng", mac=MAC1, password=PASSWORD) -> dict:
    resp = client.post(
        "/api/login",
        json={"email": email, "password": password, "mac": mac, "ip": "10.30.1.25"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def my_device_id(client, mac=MAC1) -> int:
    devices = client.get("/api/me").json()["devices"]
    return next(d["id"] for d in devices if d["mac_address"] == mac)


def make_ledger(user_id, device_id, reference, plan_id="plan_weekly", status="pending"):
    db = SessionLocal()
    plan = db.get(Plan, plan_id)
    db.add(
        Ledger(
            reference=reference,
            user_id=user_id,
            device_id=device_id,
            plan_id=plan_id,
            amount_kobo=plan.price_kobo,
            status=status,
            created_at=utc_now(),
        )
    )
    db.commit()
    db.close()


def db_user(email) -> User:
    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    db.expunge(user)
    db.close()
    return user


def db_device_expiry(email, mac):
    """The plan expiry of one device. Plans are per device, not per account."""
    db = SessionLocal()
    device = db.scalar(
        select(UserDevice).join(User, User.id == UserDevice.user_id)
        .where(User.email == email, UserDevice.mac_address == mac)
    )
    expiry = device.access_expires_at if device else None
    db.close()
    return expiry


def radius_count(sql, params) -> int:
    with radius_engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


# ---------- session and account ----------


def test_me_requires_a_session(make_client):
    assert make_client().get("/api/me").status_code == 401


def test_register_then_sign_in_and_see_device(make_client):
    client = make_client()
    resp = client.post(
        "/api/auth/register",
        json={
            "user_type": "student",
            "email": "new@mapoly.edu.ng",
            "password": PASSWORD,
            "mac": MAC1,
            "matric_no": "24/345/0046",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["ok"] is True

    sign_in(client, email="new@mapoly.edu.ng")
    me = client.get("/api/me").json()
    assert me["email"] == "new@mapoly.edu.ng"
    assert me["matric_no"] == "24/345/0046"
    assert me["plan_name"] is None
    assert len(me["devices"]) == 1


def test_gated_email_not_on_list_is_refused(make_client):
    resp = make_client().post(
        "/api/auth/register",
        json={"user_type": "gated", "email": "x@company.com", "password": PASSWORD, "mac": MAC1},
    )
    assert resp.status_code == 403
    assert "gated access list" in resp.json()["detail"]


def test_gated_pre_registered_account_can_register(make_client):
    make_user(email="partner@company.com", status="pre_registered", user_type="gated",
              access_type="free")
    resp = make_client().post(
        "/api/auth/register",
        json={"user_type": "gated", "email": "partner@company.com", "password": PASSWORD,
              "mac": MAC1},
    )
    assert resp.status_code == 201


def test_register_existing_active_account_conflicts(make_client):
    make_user()
    resp = make_client().post(
        "/api/auth/register",
        json={"user_type": "student", "email": "student@mapoly.edu.ng",
              "password": PASSWORD, "mac": MAC2},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "This account is already registered. Sign in instead."


def test_register_with_mac_owned_by_another_account_conflicts(make_client):
    make_user(email="a@mapoly.edu.ng", paid_hours=48)
    sign_in(make_client(), email="a@mapoly.edu.ng", mac=MAC1)
    resp = make_client().post(
        "/api/auth/register",
        json={"user_type": "student", "email": "b@mapoly.edu.ng",
              "password": PASSWORD, "mac": MAC1},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "This device is already linked to another account. Contact ICT."
    )


def test_logout_ends_the_dashboard_session(make_client):
    make_user()
    client = make_client()
    sign_in(client)
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_password_change(make_client):
    make_user()
    client = make_client()
    sign_in(client)
    bad = client.post("/api/auth/password",
                      json={"current_password": "wrong-one", "new_password": "newsecret1"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/password",
                     json={"current_password": PASSWORD, "new_password": "newsecret1"})
    assert ok.status_code == 200
    # The new password now works for a fresh sign-in.
    sign_in(make_client(), password="newsecret1")


def test_update_phone(make_client):
    make_user()
    client = make_client()
    sign_in(client)
    resp = client.patch("/api/me", json={"phone": "08123456789"})
    assert resp.status_code == 200
    assert resp.json()["phone"] == "08123456789"


def test_email_change_is_blocked_while_plan_is_active(make_client):
    make_user(paid_hours=48)
    client = make_client()
    sign_in(client)
    resp = client.patch("/api/me", json={"email": "new@mapoly.edu.ng"})
    assert resp.status_code == 409
    assert "plan is active" in resp.json()["detail"]


def test_email_change_allowed_without_active_plan(make_client):
    make_user(paid_hours=None)
    client = make_client()
    sign_in_resp = client.post(
        "/api/login",
        json={"email": "student@mapoly.edu.ng", "password": PASSWORD, "mac": MAC1},
    )
    assert sign_in_resp.status_code == 200
    resp = client.patch("/api/me", json={"email": "New@Mapoly.edu.ng"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@mapoly.edu.ng"


# ---------- devices ----------


def test_add_device_and_enforce_limit(make_client):
    make_user(paid_hours=48)
    client = make_client()
    sign_in(client, mac=MAC1)
    assert client.post("/api/devices", json={"name": "Laptop", "mac": MAC2}).status_code == 201
    resp = client.post("/api/devices", json={"name": "Tablet", "mac": MAC3})
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("You have reached the device limit")


def test_delete_device_frees_the_slot(make_client):
    make_user(paid_hours=48)
    client = make_client()
    sign_in(client, mac=MAC1)
    device_id = my_device_id(client, MAC1)

    assert client.delete(f"/api/devices/{device_id}").status_code == 200

    db = SessionLocal()
    remaining = db.scalar(select(func.count()).select_from(UserDevice))
    db.close()
    assert remaining == 0


def test_cannot_touch_another_users_device(make_client):
    make_user(email="a@mapoly.edu.ng", paid_hours=48)
    make_user(email="b@mapoly.edu.ng", paid_hours=48, plan_mac=MAC2)
    a = make_client()
    sign_in(a, email="a@mapoly.edu.ng", mac=MAC1)
    a_device = my_device_id(a, MAC1)

    b = make_client()
    sign_in(b, email="b@mapoly.edu.ng", mac=MAC2)
    assert b.patch(f"/api/devices/{a_device}", json={"name": "Hijacked"}).status_code == 404
    assert b.delete(f"/api/devices/{a_device}").status_code == 404


# ---------- plans ----------


def test_plans_are_public_and_mark_the_popular_one(make_client):
    resp = make_client().get("/api/plans")
    assert resp.status_code == 200
    plans = {p["id"]: p for p in resp.json()}
    assert plans["plan_weekly"]["popular"] is True
    assert plans["plan_weekly"]["price_naira"] == 1200
    assert plans["plan_2days"]["popular"] is False


# ---------- payments ----------


def test_gated_accounts_cannot_buy_plans(make_client):
    make_user(email="partner@company.com", user_type="gated", access_type="free")
    client = make_client()
    sign_in(client, email="partner@company.com")
    resp = client.post("/api/payments/initialize", json={"plan_id": "plan_weekly", "device_id": 1})
    assert resp.status_code == 403


def test_initialize_creates_pending_ledger_and_returns_checkout(make_client, monkeypatch):
    make_user()
    client = make_client()
    sign_in(client)
    device_id = my_device_id(client)

    def fake_initialize(email, amount_kobo, reference, callback_url=None):
        assert email == "student@mapoly.edu.ng"
        assert amount_kobo == 120000
        return {"reference": reference, "authorization_url": "https://checkout.example/x",
                "access_code": "abc123"}

    monkeypatch.setattr("app.routers.payments.initialize_transaction", fake_initialize)
    resp = client.post("/api/payments/initialize",
                       json={"plan_id": "plan_weekly", "device_id": device_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_code"] == "abc123"

    db = SessionLocal()
    ledger = db.scalar(select(Ledger).where(Ledger.reference == body["reference"]))
    assert ledger.status == "pending"
    db.close()


def test_initialize_refuses_another_users_device(make_client, monkeypatch):
    make_user(email="a@mapoly.edu.ng")
    make_user(email="b@mapoly.edu.ng")
    a = make_client()
    sign_in(a, email="a@mapoly.edu.ng", mac=MAC1)
    a_device = my_device_id(a, MAC1)

    b = make_client()
    sign_in(b, email="b@mapoly.edu.ng", mac=MAC2)
    resp = b.post("/api/payments/initialize", json={"plan_id": "plan_weekly", "device_id": a_device})
    assert resp.status_code == 403


def test_verify_applies_success_once_and_rejects_wrong_amount(make_client, monkeypatch):
    user_id = make_user()
    client = make_client()
    sign_in(client)
    device_id = my_device_id(client)
    make_ledger(user_id, device_id, "ref_ok")
    make_ledger(user_id, device_id, "ref_short")

    def fake_verify(reference):
        if reference == "ref_ok":
            return {"status": "success", "amount": 120000,
                    "paid_at": "2026-11-12T12:00:00.000Z"}
        return {"status": "success", "amount": 100, "paid_at": "2026-11-12T12:00:00.000Z"}

    monkeypatch.setattr("app.routers.payments.verify_transaction", fake_verify)

    first = client.get("/api/payments/verify/ref_ok")
    second = client.get("/api/payments/verify/ref_ok")
    assert first.json()["status"] == "success"
    assert second.json()["status"] == "success"
    # Two verify calls must not extend access twice.
    assert first.json()["access_expires_at"] == second.json()["access_expires_at"]

    short = client.get("/api/payments/verify/ref_short")
    assert short.json()["status"] == "failed"


def test_verify_refuses_another_users_reference(make_client, monkeypatch):
    user_id = make_user(email="a@mapoly.edu.ng")
    make_user(email="b@mapoly.edu.ng")
    a = make_client()
    sign_in(a, email="a@mapoly.edu.ng", mac=MAC1)
    make_ledger(user_id, my_device_id(a, MAC1), "ref_a")

    b = make_client()
    sign_in(b, email="b@mapoly.edu.ng", mac=MAC2)
    assert b.get("/api/payments/verify/ref_a").status_code == 404


# ---------- webhook ----------


def _signed(payload: dict) -> tuple[bytes, str]:
    raw = json.dumps(payload).encode()
    key = get_settings().PAYSTACK_SECRET_KEY.encode()
    return raw, hmac.new(key, raw, hashlib.sha512).hexdigest()


def test_webhook_rejects_bad_signature(make_client):
    raw, _ = _signed({"event": "charge.success", "data": {}})
    resp = make_client().post(
        "/api/v1/payment/webhook-listener",
        content=raw,
        headers={"x-paystack-signature": "deadbeef", "content-type": "application/json"},
    )
    assert resp.status_code == 401


def test_webhook_applies_charge_and_is_idempotent(make_client):
    user_id = make_user()
    client = make_client()
    sign_in(client)
    make_ledger(user_id, my_device_id(client), "ref_hook")

    payload = {
        "event": "charge.success",
        "data": {"reference": "ref_hook", "amount": 120000,
                 "paid_at": "2026-11-12T12:00:00.000Z"},
    }
    raw, sig = _signed(payload)
    headers = {"x-paystack-signature": sig, "content-type": "application/json"}

    resp = make_client().post("/api/v1/payment/webhook-listener", content=raw, headers=headers)
    assert resp.status_code == 200
    after_first = db_device_expiry("student@mapoly.edu.ng", MAC1)
    assert after_first is not None

    # Paystack retries. Access must not be extended a second time.
    make_client().post("/api/v1/payment/webhook-listener", content=raw, headers=headers)
    after_second = db_device_expiry("student@mapoly.edu.ng", MAC1)
    assert after_first == after_second


def test_webhook_ignores_other_events(make_client):
    raw, sig = _signed({"event": "transfer.success", "data": {}})
    resp = make_client().post(
        "/api/v1/payment/webhook-listener",
        content=raw,
        headers={"x-paystack-signature": sig, "content-type": "application/json"},
    )
    assert resp.status_code == 200
