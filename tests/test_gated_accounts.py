"""Admin-created gated accounts: temporary password, forced change, end date, admin log.

Uses SQLite (set by conftest.py) and Redis, like the other route tests.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.database import SessionLocal, engine
from app.db_setup import seed_plans
from app.main import app
from app.models import AdminAction, Base, User
from app.radius_database import radius_engine
from app.redis_client import get_redis
from app.security.passwords import hash_password, verify_password
from app.utils.time import utc_now
from radius_schema import radius_tables

ADMIN_EMAIL = "ict@mapoly.edu.ng"
ADMIN_PASSWORD = "adminpass1"
STAFF_EMAIL = "staff@mapoly.edu.ng"
STAFF_PASSWORD = "staffpass1"
TEMP_PASSWORD = "Temp-1234"
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


def add_user(email, password, **fields) -> int:
    db = SessionLocal()
    user = User(
        email=email,
        password_hash=hash_password(password),
        status="active",
        user_type=fields.pop("user_type", "student"),
        access_type=fields.pop("access_type", "paid"),
        is_active=True,
        **fields,
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return user_id


def sign_in(email, password, mac=MAC1) -> TestClient:
    client = TestClient(app, base_url="https://testserver")
    resp = client.post(
        "/api/login",
        json={"email": email, "password": password, "mac": mac, "ip": "10.30.1.25"},
    )
    assert resp.status_code == 200, resp.text
    return client


def db_user(email) -> User:
    db = SessionLocal()
    user = db.scalar(select(User).where(User.email == email))
    db.close()
    return user


def create_gated(client, email="gated@mapoly.edu.ng", **extra):
    body = {"email": email, "full_name": "Test Person",
            "temporary_password": TEMP_PASSWORD, **extra}
    return client.post("/api/admin/gated-users", json=body)


@pytest.fixture
def admin_client():
    add_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
    return sign_in(ADMIN_EMAIL, ADMIN_PASSWORD)


# ---------- creating a gated account ----------


def test_admin_creates_gated_user_with_temporary_password(admin_client):
    resp = create_gated(admin_client, matric_no="2025/001", department="Computer Science")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["must_change_password"] is True
    assert body["gated_until"] is None

    user = db_user("gated@mapoly.edu.ng")
    assert user.user_type == "gated"
    assert user.access_type == "free"
    assert user.must_change_password is True
    assert user.matric_no == "2025/001"
    assert user.full_name == "Test Person"
    # Only the hash is stored, never the password itself.
    assert user.password_hash != TEMP_PASSWORD
    assert verify_password(user.password_hash, TEMP_PASSWORD)


def test_matric_number_is_optional_but_name_is_required(admin_client):
    no_matric = create_gated(admin_client)
    assert no_matric.status_code == 200, no_matric.text
    assert db_user("gated@mapoly.edu.ng").matric_no is None

    no_name = admin_client.post(
        "/api/admin/gated-users",
        json={"email": "noname@mapoly.edu.ng", "temporary_password": TEMP_PASSWORD},
    )
    assert no_name.status_code == 422


def test_creating_gated_user_writes_an_admin_log_row(admin_client):
    create_gated(admin_client)

    db = SessionLocal()
    row = db.scalar(select(AdminAction).where(AdminAction.action == "create_gated_user"))
    admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
    assert row is not None
    assert row.admin_user_id == admin.id
    assert row.target_user_id == db_user("gated@mapoly.edu.ng").id
    assert TEMP_PASSWORD not in (row.detail or "")
    db.close()


def test_non_admin_cannot_create_gated_user():
    add_user(STAFF_EMAIL, STAFF_PASSWORD)
    client = sign_in(STAFF_EMAIL, STAFF_PASSWORD)

    resp = create_gated(client)

    assert resp.status_code == 403
    assert db_user("gated@mapoly.edu.ng") is None


def test_signed_out_user_cannot_create_gated_user():
    resp = create_gated(TestClient(app, base_url="https://testserver"))
    assert resp.status_code == 401


def test_duplicate_email_is_refused(admin_client):
    create_gated(admin_client)
    resp = create_gated(admin_client)
    assert resp.status_code == 409


def test_short_temporary_password_is_refused(admin_client):
    resp = admin_client.post(
        "/api/admin/gated-users",
        json={"email": "gated@mapoly.edu.ng", "temporary_password": "short"},
    )
    assert resp.status_code == 422


# ---------- signing in and changing the password ----------


def test_gated_user_signs_in_with_temporary_password_and_is_told_to_change_it(admin_client):
    create_gated(admin_client)

    client = sign_in("gated@mapoly.edu.ng", TEMP_PASSWORD, mac=MAC2)
    me = client.get("/api/me").json()

    assert me["must_change_password"] is True
    assert me["is_admin"] is False


def test_change_password_refused_with_wrong_current_password(admin_client):
    create_gated(admin_client)
    client = sign_in("gated@mapoly.edu.ng", TEMP_PASSWORD, mac=MAC2)

    resp = client.post(
        "/api/me/password",
        json={"current_password": "not-the-one", "new_password": "NewPass-9876"},
    )

    assert resp.status_code == 401
    assert db_user("gated@mapoly.edu.ng").must_change_password is True


def test_change_password_clears_the_flag_and_new_password_works(admin_client):
    create_gated(admin_client)
    client = sign_in("gated@mapoly.edu.ng", TEMP_PASSWORD, mac=MAC2)

    resp = client.post(
        "/api/me/password",
        json={"current_password": TEMP_PASSWORD, "new_password": "NewPass-9876"},
    )

    assert resp.status_code == 204
    assert client.get("/api/me").json()["must_change_password"] is False
    assert verify_password(db_user("gated@mapoly.edu.ng").password_hash, "NewPass-9876")
    # The old temporary password no longer works.
    fresh = TestClient(app, base_url="https://testserver")
    old = fresh.post(
        "/api/login",
        json={"email": "gated@mapoly.edu.ng", "password": TEMP_PASSWORD,
              "mac": MAC1, "ip": "10.30.1.25"},
    )
    assert old.status_code == 401


def test_new_password_must_be_different_and_long_enough(admin_client):
    create_gated(admin_client)
    client = sign_in("gated@mapoly.edu.ng", TEMP_PASSWORD, mac=MAC2)

    same = client.post(
        "/api/me/password",
        json={"current_password": TEMP_PASSWORD, "new_password": TEMP_PASSWORD},
    )
    short = client.post(
        "/api/me/password",
        json={"current_password": TEMP_PASSWORD, "new_password": "abc"},
    )

    assert same.status_code == 422
    assert short.status_code == 422


# ---------- end dates ----------


def test_gated_user_with_past_end_date_cannot_connect():
    add_user("old@mapoly.edu.ng", STAFF_PASSWORD, user_type="gated", access_type="free",
             gated_until=utc_now() - timedelta(days=1))

    resp = TestClient(app, base_url="https://testserver").post(
        "/api/login",
        json={"email": "old@mapoly.edu.ng", "password": STAFF_PASSWORD,
              "mac": MAC1, "ip": "10.30.1.25"},
    )

    assert resp.status_code == 200
    assert resp.json()["has_active_plan"] is False
    assert resp.json()["token"] is None


def test_gated_user_with_future_end_date_can_connect():
    add_user("new@mapoly.edu.ng", STAFF_PASSWORD, user_type="gated", access_type="free",
             gated_until=utc_now() + timedelta(days=30))

    resp = TestClient(app, base_url="https://testserver").post(
        "/api/login",
        json={"email": "new@mapoly.edu.ng", "password": STAFF_PASSWORD,
              "mac": MAC1, "ip": "10.30.1.25"},
    )

    assert resp.json()["has_active_plan"] is True
    assert resp.json()["token"] is not None


def test_admin_extends_end_date_and_writes_log(admin_client):
    create_gated(admin_client, gated_until=(utc_now() - timedelta(days=1)).isoformat())
    gated_id = db_user("gated@mapoly.edu.ng").id

    new_end = (utc_now() + timedelta(days=14)).isoformat()
    resp = admin_client.patch(f"/api/admin/gated-users/{gated_id}", json={"gated_until": new_end})

    assert resp.status_code == 200, resp.text
    assert db_user("gated@mapoly.edu.ng").gated_until > utc_now()
    db = SessionLocal()
    count = db.scalar(select(func.count()).select_from(AdminAction)
                      .where(AdminAction.action == "set_gated_end_date"))
    db.close()
    assert count == 1


def test_admin_can_remove_end_date(admin_client):
    create_gated(admin_client, gated_until=(utc_now() + timedelta(days=5)).isoformat())
    gated_id = db_user("gated@mapoly.edu.ng").id

    resp = admin_client.patch(f"/api/admin/gated-users/{gated_id}", json={"gated_until": None})

    assert resp.status_code == 200
    assert db_user("gated@mapoly.edu.ng").gated_until is None


def test_end_date_cannot_be_set_on_a_paid_account(admin_client):
    paid_id = add_user("paid@mapoly.edu.ng", STAFF_PASSWORD)

    resp = admin_client.patch(f"/api/admin/gated-users/{paid_id}",
                              json={"gated_until": (utc_now() + timedelta(days=5)).isoformat()})

    assert resp.status_code == 404
    assert db_user("paid@mapoly.edu.ng").gated_until is None


# ---------- durations and account disconnect ----------


def test_duration_in_days_sets_the_end_date_on_create(admin_client):
    before = utc_now()
    resp = create_gated(admin_client, duration_days=30)

    assert resp.status_code == 200, resp.text
    until = db_user("gated@mapoly.edu.ng").gated_until
    assert timedelta(days=29) < until - before < timedelta(days=31)


def test_end_date_and_duration_together_are_refused(admin_client):
    resp = create_gated(admin_client, duration_days=30,
                        gated_until=(utc_now() + timedelta(days=5)).isoformat())
    assert resp.status_code == 422


def test_duration_is_optional_on_create(admin_client):
    create_gated(admin_client)
    assert db_user("gated@mapoly.edu.ng").gated_until is None


def test_admin_extends_by_duration_from_today(admin_client):
    create_gated(admin_client, gated_until=(utc_now() - timedelta(days=1)).isoformat())
    gated_id = db_user("gated@mapoly.edu.ng").id

    before = utc_now()
    resp = admin_client.patch(f"/api/admin/gated-users/{gated_id}", json={"duration_days": 14})

    assert resp.status_code == 200, resp.text
    until = db_user("gated@mapoly.edu.ng").gated_until
    assert timedelta(days=13) < until - before < timedelta(days=15)


def test_extend_refuses_both_an_end_date_and_a_duration(admin_client):
    create_gated(admin_client)
    gated_id = db_user("gated@mapoly.edu.ng").id
    resp = admin_client.patch(f"/api/admin/gated-users/{gated_id}",
                              json={"duration_days": 3, "gated_until": None})
    assert resp.status_code == 422


def test_csv_accepts_a_duration_column(admin_client):
    csv_text = ("email,full_name,temporary_password,duration_days\n"
                "dur@mapoly.edu.ng,Dur Person,Temp-0001,60\n")
    resp = admin_client.post("/api/admin/gated-users/bulk", json={"csv": csv_text})
    assert resp.status_code == 200, resp.text
    assert db_user("dur@mapoly.edu.ng").gated_until is not None


def test_admin_disconnects_all_devices_of_a_gated_account_and_keeps_it_active(admin_client):
    create_gated(admin_client)
    gated_id = db_user("gated@mapoly.edu.ng").id
    gated_client = sign_in("gated@mapoly.edu.ng", TEMP_PASSWORD, mac=MAC2)
    assert gated_client.get("/api/me").status_code == 200

    # A session that is open on the router right now for this device.
    from app.radius_database import radius_engine as re_engine
    with re_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO radacct (username, callingstationid, acctstarttime) "
            "VALUES ('gated@mapoly.edu.ng|x', :m, :t)"
        ), {"m": MAC2, "t": utc_now()})

    resp = admin_client.post(f"/api/admin/users/{gated_id}/disconnect")

    assert resp.status_code == 200
    assert resp.json()["devices"] == 1
    with re_engine.connect() as conn:
        open_sessions = conn.execute(text(
            "SELECT COUNT(*) FROM radacct WHERE callingstationid = :m AND acctstoptime IS NULL"
        ), {"m": MAC2}).scalar()
    assert open_sessions == 0
    assert db_user("gated@mapoly.edu.ng").is_active is True
