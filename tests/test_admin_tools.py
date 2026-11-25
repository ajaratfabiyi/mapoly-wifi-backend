"""Admin tools: accounts, CSV upload, devices, blocks, points, payments, plans, announcements, audit.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import AdminAction, Ledger, Plan, User, UserDevice
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password
from app.utils.time import utc_now
from radius_schema import radius_tables

PASSWORD = "secret123"
ADMIN = "ict@mapoly.edu.ng"
STAFF = "staff@mapoly.edu.ng"
PAID = "paid@mapoly.edu.ng"
TEMP = "Temp-1234"
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
    user = User(email=email, password_hash=hash_password(PASSWORD), status="active",
                user_type=fields.pop("user_type", "student"),
                access_type=fields.pop("access_type", "paid"),
                is_active=True, max_devices=None, **fields)
    db.add(user)
    db.commit()
    uid = user.id
    db.close()
    return uid


def sign_in(email, mac, password=PASSWORD) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post("/api/login", json={"email": email, "password": password,
                                           "mac": mac, "ip": "10.30.1.25"})
    assert resp.status_code == 200, resp.text
    return client


def uid_of(email) -> int:
    db = SessionLocal()
    uid = db.scalar(select(User.id).where(User.email == email))
    db.close()
    return uid


def row(email) -> User:
    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    db.close()
    return user


@pytest.fixture
def admin():
    add_user(ADMIN, is_admin=True)
    return sign_in(ADMIN, MAC_ADMIN)


@pytest.fixture
def paid_user():
    add_user(PAID)
    client = sign_in(PAID, MAC_PAID)
    device = client.get("/api/me").json()["devices"][0]
    return client, device["id"]


# ---------- who may use the admin routes ----------


def test_non_admin_is_refused_on_admin_routes():
    add_user(STAFF)
    client = sign_in(STAFF, MAC_PAID)
    for method, path in [("get", "/api/admin/users"), ("get", "/api/admin/audit"),
                         ("get", "/api/admin/plans"), ("get", "/api/admin/payments"),
                         ("get", "/api/admin/blocks"), ("get", "/api/admin/reports/summary")]:
        assert getattr(client, method)(path).status_code == 403, path


def test_signed_out_is_refused_on_admin_routes():
    assert TestClient(app, base_url="https://testserver").get("/api/admin/users").status_code == 401


# ---------- accounts ----------


def test_search_finds_by_email_name_and_matric(admin):
    admin.post("/api/admin/gated-users", json={"email": "ade@mapoly.edu.ng", "full_name": "Ade Bello",
                                               "matric_no": "2025/777", "temporary_password": TEMP})
    assert any(u["email"] == "ade@mapoly.edu.ng" for u in admin.get("/api/admin/users?q=ade").json())
    assert any(u["full_name"] == "Ade Bello" for u in admin.get("/api/admin/users?q=bello").json())
    assert any(u["matric_no"] == "2025/777" for u in admin.get("/api/admin/users?q=2025/777").json())


def test_admin_disables_and_re_enables_an_account(admin):
    uid = add_user(PAID)
    assert admin.patch(f"/api/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert row(PAID).is_active is False
    refused = TestClient(app, base_url="https://testserver").post(
        "/api/login", json={"email": PAID, "password": PASSWORD, "mac": MAC_PAID, "ip": "10.0.0.1"})
    assert refused.status_code == 403

    admin.patch(f"/api/admin/users/{uid}", json={"is_active": True})
    assert row(PAID).is_active is True


def test_admin_cannot_disable_own_account(admin):
    resp = admin.patch(f"/api/admin/users/{uid_of(ADMIN)}", json={"is_active": False})
    assert resp.status_code == 422


def test_device_limit_can_be_set_and_removed(admin):
    uid = add_user(PAID)
    assert admin.patch(f"/api/admin/users/{uid}", json={"max_devices": 3}).json()["max_devices"] == 3
    assert admin.patch(f"/api/admin/users/{uid}", json={"max_devices": None}).json()["max_devices"] is None


def test_reset_password_gives_a_temporary_one(admin):
    uid = add_user(PAID)
    assert admin.post(f"/api/admin/users/{uid}/reset-password",
                      json={"temporary_password": "Reset-5678"}).status_code == 204
    user = row(PAID)
    assert user.must_change_password is True
    db = SessionLocal()
    log = db.scalar(select(AdminAction).where(AdminAction.action == "reset_password"))
    db.close()
    assert "Reset-5678" not in (log.detail or "")
    sign_in(PAID, MAC_PAID, password="Reset-5678")


# ---------- CSV and group disable ----------


def test_csv_creates_every_row_when_all_are_valid(admin):
    csv_text = ("email,full_name,temporary_password,matric_no,gated_until\n"
                "one@mapoly.edu.ng,One Person,Temp-0001,2025/001,2027-06-30\n"
                "two@mapoly.edu.ng,Two Person,Temp-0002,,\n")
    resp = admin.post("/api/admin/gated-users/bulk", json={"csv": csv_text})
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 2
    assert row("one@mapoly.edu.ng").gated_until is not None
    assert row("two@mapoly.edu.ng").matric_no is None


def test_csv_with_one_bad_row_saves_nothing(admin):
    csv_text = ("email,full_name,temporary_password\n"
                "good@mapoly.edu.ng,Good Person,Temp-0001\n"
                "bad-email,Bad Person,Temp-0002\n")
    resp = admin.post("/api/admin/gated-users/bulk", json={"csv": csv_text})
    assert resp.status_code == 422
    assert "Line 3" in resp.json()["detail"]
    assert row("good@mapoly.edu.ng") is None


def test_csv_needs_the_required_columns(admin):
    resp = admin.post("/api/admin/gated-users/bulk", json={"csv": "email,full_name\na@b.co,Name Here\n"})
    assert resp.status_code == 422


def test_csv_refuses_an_email_that_already_exists(admin):
    add_user("taken@mapoly.edu.ng")
    resp = admin.post("/api/admin/gated-users/bulk", json={
        "csv": "email,full_name,temporary_password\ntaken@mapoly.edu.ng,Taken Person,Temp-0001\n"})
    assert resp.status_code == 409
    assert row("taken@mapoly.edu.ng").user_type == "student"


def test_group_disable_only_touches_gated_accounts(admin):
    gated = admin.post("/api/admin/gated-users", json={
        "email": "g@mapoly.edu.ng", "full_name": "Gated One", "temporary_password": TEMP}).json()["id"]
    student = add_user("s@mapoly.edu.ng")

    resp = admin.post("/api/admin/gated-users/disable-group", json={"user_ids": [gated, student]})

    assert resp.status_code == 200
    assert resp.json()["disabled"] == 1
    assert row("g@mapoly.edu.ng").is_active is False
    assert row("s@mapoly.edu.ng").is_active is True


# ---------- points ----------


def test_admin_adds_and_removes_points_with_a_reason(admin):
    uid = add_user(PAID)
    assert admin.post(f"/api/admin/users/{uid}/points",
                      json={"delta": 50, "reason": "Refund for double charge"}).json()["points"] == 50
    assert admin.post(f"/api/admin/users/{uid}/points",
                      json={"delta": -20, "reason": "Correction"}).json()["points"] == 30

    history = admin.get(f"/api/admin/users/{uid}/points").json()
    assert [h["delta"] for h in history] == [-20, 50]
    assert admin.get("/api/admin/points/summary").json()["total_points"] == 30


def test_points_cannot_go_below_zero_or_be_zero(admin):
    uid = add_user(PAID, points=10)
    assert admin.post(f"/api/admin/users/{uid}/points",
                      json={"delta": -11, "reason": "Too much"}).status_code == 422
    assert admin.post(f"/api/admin/users/{uid}/points",
                      json={"delta": 0, "reason": "Nothing"}).status_code == 422
    assert row(PAID).points == 10


# ---------- devices, grants and blocks ----------


def test_admin_grants_a_plan_to_a_device(admin, paid_user):
    _, device_id = paid_user
    resp = admin.post(f"/api/admin/devices/{device_id}/grant", json={"plan_id": "plan_1day"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["access_expires_at"] is not None
    db = SessionLocal()
    free_row = db.scalar(select(Ledger).where(Ledger.reference.like("admin_%")))
    assert free_row.amount_kobo == 0
    assert free_row.status == "success"
    db.close()


def test_grant_refused_for_gated_accounts(admin):
    gid = admin.post("/api/admin/gated-users", json={
        "email": "gg@mapoly.edu.ng", "full_name": "Gated Two", "temporary_password": TEMP}).json()["id"]
    gated_client = sign_in("gg@mapoly.edu.ng", MAC_OTHER, password=TEMP)
    device_id = gated_client.get("/api/me").json()["devices"][0]["id"]

    resp = admin.post(f"/api/admin/devices/{device_id}/grant", json={"plan_id": "plan_1day"})

    assert resp.status_code == 422


def test_admin_lists_a_users_devices(admin, paid_user):
    client, device_id = paid_user
    uid = uid_of(PAID)
    devices = admin.get(f"/api/admin/users/{uid}/devices").json()
    assert [d["id"] for d in devices] == [device_id]


def test_admin_removes_a_device(admin, paid_user):
    _, device_id = paid_user
    assert admin.delete(f"/api/admin/devices/{device_id}").status_code == 204
    db = SessionLocal()
    assert db.get(UserDevice, device_id) is None
    db.close()


def test_admin_disconnects_without_removing(admin, paid_user):
    _, device_id = paid_user
    assert admin.post(f"/api/admin/devices/{device_id}/disconnect").status_code == 204
    db = SessionLocal()
    assert db.get(UserDevice, device_id) is not None
    db.close()


def test_blocked_mac_cannot_sign_in_with_any_account(admin):
    add_user(PAID)
    assert admin.post("/api/admin/blocks", json={"mac": MAC_PAID, "reason": "Abuse on campus"}).status_code == 200

    resp = TestClient(app, base_url="https://testserver").post(
        "/api/login", json={"email": PAID, "password": PASSWORD, "mac": MAC_PAID, "ip": "10.0.0.1"})
    assert resp.status_code == 403


def test_unblock_restores_sign_in(admin):
    add_user(PAID)
    admin.post("/api/admin/blocks", json={"mac": MAC_PAID, "reason": "Mistake"})
    assert admin.delete(f"/api/admin/blocks/{MAC_PAID}").status_code == 204
    sign_in(PAID, MAC_PAID)


def test_block_rejects_bad_mac_and_duplicates(admin):
    assert admin.post("/api/admin/blocks", json={"mac": "not-a-mac", "reason": "Bad"}).status_code == 422
    admin.post("/api/admin/blocks", json={"mac": MAC_OTHER, "reason": "First"})
    assert admin.post("/api/admin/blocks", json={"mac": MAC_OTHER, "reason": "Again"}).status_code == 409


def test_online_list_and_summary_respond(admin):
    assert admin.get("/api/admin/network/online").json() == []
    summary = admin.get("/api/admin/reports/summary?days=7").json()
    assert summary["days"] == 7
    assert summary["total_users"] >= 1


# ---------- payments and plans ----------


def _make_success_payment(user_id, device_id, reference="ref_paid"):
    db = SessionLocal()
    db.add(Ledger(reference=reference, user_id=user_id, device_id=device_id, plan_id="plan_1day",
                  amount_kobo=30000, status="success", paid_at=utc_now(), created_at=utc_now()))
    db.commit()
    db.close()


def test_payments_list_and_refund_marking(admin, paid_user):
    _, device_id = paid_user
    _make_success_payment(uid_of(PAID), device_id)

    listed = admin.get("/api/admin/payments?status=success").json()
    assert listed[0]["reference"] == "ref_paid"
    assert listed[0]["amount_naira"] == 300

    assert admin.post("/api/admin/payments/ref_paid/refund").status_code == 204
    assert admin.post("/api/admin/payments/ref_paid/refund").status_code == 409


def test_only_successful_payments_can_be_refunded(admin, paid_user):
    _, device_id = paid_user
    db = SessionLocal()
    db.add(Ledger(reference="ref_pending", user_id=uid_of(PAID), device_id=device_id,
                  plan_id="plan_1day", amount_kobo=30000, status="pending", created_at=utc_now()))
    db.commit()
    db.close()
    assert admin.post("/api/admin/payments/ref_pending/refund").status_code == 422


def test_admin_changes_a_plan_price_and_points(admin):
    resp = admin.patch("/api/admin/plans/plan_1day", json={"price_kobo": 35000, "points_award": 60,
                                                          "points_cost": 350})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["price_naira"], body["points_award"], body["points_cost"]) == (350, 60, 350)


def test_plan_cannot_be_given_an_empty_title_or_price(admin):
    assert admin.patch("/api/admin/plans/plan_1day", json={"title": None}).status_code == 422
    assert admin.patch("/api/admin/plans/plan_1day", json={"price_kobo": None}).status_code == 422


def test_admin_plan_list_shows_retired_plan_off_sale(admin):
    db = SessionLocal()
    db.add(Plan(id="plan_biweekly", title="Bi-Weekly", duration_hours=336,
                price_kobo=220000, speed_limit_mbps=None, is_active=False))
    db.commit()
    db.close()
    plans = {p["id"]: p for p in admin.get("/api/admin/plans").json()}
    assert plans["plan_biweekly"]["is_active"] is False


def test_switching_a_plan_off_hides_it_from_the_storefront(admin):
    admin.patch("/api/admin/plans/plan_1day", json={"is_active": False})
    public = TestClient(app, base_url="https://testserver").get("/api/plans").json()
    assert "plan_1day" not in [p["id"] for p in public]


# ---------- announcements and audit ----------


def test_announcement_shows_on_portal_until_switched_off(admin):
    created = admin.post("/api/admin/announcements", json={"message": "Network maintenance tonight"}).json()
    public = TestClient(app, base_url="https://testserver")
    assert [a["message"] for a in public.get("/api/announcements").json()] == ["Network maintenance tonight"]

    admin.patch(f"/api/admin/announcements/{created['id']}", json={"active": False})
    assert public.get("/api/announcements").json() == []


def test_audit_shows_who_did_what(admin):
    admin.post("/api/admin/announcements", json={"message": "Hello students"})
    audit = admin.get("/api/admin/audit").json()
    entry = next(a for a in audit if a["action"] == "create_announcement")
    assert entry["admin_email"] == ADMIN


def test_login_audit_lists_sign_ins(admin):
    sign_in(ADMIN, MAC_ADMIN)
    events = admin.get("/api/admin/audit/logins").json()
    assert len(events) >= 2  # the sign-ins above, recorded whether or not the account has a plan
