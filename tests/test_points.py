"""Points: earned on cash purchases, spent on plans. 1 point = ₦1.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import Base, Ledger, Plan, PointsEntry, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.services.payments import ALREADY_APPLIED, APPLIED, apply_successful_charge
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
EMAIL = "student@mapoly.edu.ng"
OTHER_EMAIL = "other@mapoly.edu.ng"
MAC1 = "A4:C3:F0:88:91:01"
MAC2 = "A4:C3:F0:88:91:02"

RADIUS_TABLES = radius_tables()


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with radius_engine.begin() as conn:
        for name, ddl in RADIUS_TABLES.items():
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
            conn.execute(text(ddl))
    seed_plans(engine)
    get_redis().flushdb()
    yield


def make_user(email=EMAIL, points=0, user_type="student", access_type="paid") -> int:
    db = SessionLocal()
    user = User(
        email=email,
        password_hash=hash_password(PASSWORD),
        status="active",
        user_type=user_type,
        access_type=access_type,
        max_devices=2,
        is_active=True,
        points=points,
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def sign_in(email=EMAIL, mac=MAC1) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post(
        "/api/login",
        json={"email": email, "password": PASSWORD, "mac": mac, "ip": "10.30.1.25"},
    )
    assert resp.status_code == 200, resp.text
    return client


def device_id_for(client: TestClient, mac=MAC1) -> int:
    devices = client.get("/api/me").json()["devices"]
    return next(d["id"] for d in devices if d["mac_address"] == mac)


def add_pending_ledger(user_id, device_id, reference, plan_id, amount_kobo):
    db = SessionLocal()
    db.add(
        Ledger(
            reference=reference,
            user_id=user_id,
            device_id=device_id,
            plan_id=plan_id,
            amount_kobo=amount_kobo,
            status="pending",
            created_at=utc_now(),
        )
    )
    db.commit()
    db.close()


def points_of(email=EMAIL) -> int:
    db = SessionLocal()
    balance = db.scalar(select(User.points).where(User.email == email))
    db.close()
    return balance


def redeem(client: TestClient, plan_id: str, device_id: int):
    return client.post("/api/points/redeem", json={"plan_id": plan_id, "device_id": device_id})


# ---------- earning ----------


def test_cash_purchase_of_weekly_plan_earns_20_points():
    user_id = make_user()
    client = sign_in()
    did = device_id_for(client)
    add_pending_ledger(user_id, did, "ref_buy", "plan_weekly", 200000)

    db = SessionLocal()
    now = utc_now()
    assert apply_successful_charge(db, "ref_buy", 200000, now, now) == APPLIED
    db.close()

    assert points_of() == 20
    db = SessionLocal()
    entry = db.scalar(select(PointsEntry).where(PointsEntry.reference == "ref_buy"))
    assert (entry.delta, entry.balance_after, entry.reason) == (20, 20, "purchase")
    db.close()


def test_repeated_charge_does_not_award_points_twice():
    user_id = make_user()
    client = sign_in()
    add_pending_ledger(user_id, device_id_for(client), "ref_twice", "plan_1day", 30000)

    db = SessionLocal()
    now = utc_now()
    assert apply_successful_charge(db, "ref_twice", 30000, now, now) == APPLIED
    db.close()
    db = SessionLocal()
    assert apply_successful_charge(db, "ref_twice", 30000, now, now) == ALREADY_APPLIED
    db.close()

    assert points_of() == 5


# ---------- spending ----------


def test_redeem_300_points_buys_the_one_day_plan():
    make_user(points=300)
    client = sign_in()
    did = device_id_for(client)

    resp = redeem(client, "plan_1day", did)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["points_balance"] == 0
    assert body["access_expires_at"] is not None
    assert points_of() == 0

    db = SessionLocal()
    ledger = db.scalar(select(Ledger).where(Ledger.reference == body["reference"]))
    assert ledger.status == "success"
    assert ledger.payment_method is None
    assert ledger.amount_kobo == 30000
    entry = db.scalar(select(PointsEntry).where(PointsEntry.reference == body["reference"]))
    assert (entry.delta, entry.balance_after, entry.reason) == (-300, 0, "redeem")
    device = db.get(UserDevice, did)
    assert device.access_expires_at is not None
    db.close()


def test_redeem_refused_when_points_are_short():
    make_user(points=299)
    client = sign_in()
    did = device_id_for(client)

    resp = redeem(client, "plan_1day", did)

    assert resp.status_code == 402
    assert points_of() == 299
    db = SessionLocal()
    count = db.scalar(select(func.count()).select_from(Ledger).where(Ledger.reference.like("points_%")))
    assert count == 0
    db.close()


def test_redeem_refused_for_plan_without_points_price():
    make_user(points=10000)
    client = sign_in()
    did = device_id_for(client)
    db = SessionLocal()
    db.get(Plan, "plan_2days").points_cost = None
    db.commit()
    db.close()

    resp = redeem(client, "plan_2days", did)

    assert resp.status_code == 422
    assert points_of() == 10000


def test_redeem_refused_for_another_accounts_device():
    make_user(points=5000)
    make_user(email=OTHER_EMAIL)
    mine = sign_in()
    other = sign_in(email=OTHER_EMAIL, mac=MAC2)
    other_device = device_id_for(other, MAC2)

    resp = redeem(mine, "plan_1day", other_device)

    assert resp.status_code == 403
    assert points_of() == 5000


def test_gated_account_cannot_redeem_points():
    make_user(points=5000, user_type="gated", access_type="free")
    client = sign_in()
    resp = redeem(client, "plan_1day", device_id_for(client))
    assert resp.status_code == 403
    assert points_of() == 5000


def test_redeem_needs_a_signed_in_user():
    resp = TestClient(app, base_url="https://testserver").post(
        "/api/points/redeem", json={"plan_id": "plan_1day", "device_id": 1}
    )
    assert resp.status_code == 401


# ---------- what the user sees ----------


def test_me_shows_the_points_balance():
    make_user(points=123)
    client = sign_in()
    assert client.get("/api/me").json()["points"] == 123


def test_plans_show_points_earned_and_price_in_points():
    resp = TestClient(app, base_url="https://testserver").get("/api/plans")
    plans = {p["id"]: p for p in resp.json()}
    assert "plan_biweekly" not in plans
    assert plans["plan_1day"]["price_naira"] == 300
    assert plans["plan_1day"]["points_award"] == 5
    assert plans["plan_1day"]["points_cost"] == 300
    assert plans["plan_monthly"]["points_award"] == 50
    assert plans["plan_monthly"]["points_cost"] == 5000
