"""Database setup and upgrade, without a migration tool.

Run it once for a new database, and again after every deploy that changes the schema.
Every step is safe to run repeatedly: it checks what exists before changing anything.

    python -m app.db_setup

Works on SQLite (local development), PostgreSQL (Supabase, production) and MySQL. Steps
that only one database supports are skipped on the others, and the reason is logged.

What it does:
1. Creates any missing tables in the main database from the SQLAlchemy models.
2. Upgrades a database created by the older migrations (per-device plans):
   - adds the per-device plan columns to user_devices, copying each paid account's
     current plan onto its existing devices (so no one loses access);
   - removes the old account-level plan columns from users;
   - updates the coa_queue reason list (PostgreSQL and MySQL; SQLite stores it as plain text).
3. Adds the points columns (users.points, plans.points_award, plans.points_cost).
4. Seeds the storefront plans (updates prices, titles and points if they changed) and turns
   off the plans that were retired (plan_biweekly).
5. Widens the RADIUS username columns to VARCHAR(255), in the radius database, if the
   tables exist (MySQL only; SQLite and PostgreSQL do not need it here). Device usernames
   are the email plus 18 characters, which overflows FreeRADIUS's default VARCHAR(64) on
   MySQL. On PostgreSQL, widen the columns by hand (see radius/README.md).

The FreeRADIUS tables themselves (radcheck, radreply, radusergroup, radacct, ...) come
from FreeRADIUS's own schema.sql, imported once onto the radius database. This script
does not create them; it only checks and widens them.

Before running against production, take a backup.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine as main_engine
from app.models import Base, Plan
from app.radius_database import radius_engine

log = logging.getLogger(__name__)

COA_REASON_VALUES = ("plan_expired", "device_removed", "device_plan_expired", "account_disabled")
COA_REASONS = ",".join(f"'{r}'" for r in COA_REASON_VALUES)

# (id, title, duration hours, price in kobo, speed Mbps, points earned, points to buy)
# Points: 1 point = ₦1 when spent, so points_cost equals the price in naira.
PLANS = [
    ("plan_1day", "1-Day Pass", 24, 30000, None, 50, 300),
    ("plan_2days", "2-Day Campus Pass", 48, 50000, 25, 100, 500),
    ("plan_weekly", "Weekly Academic Pass", 168, 200000, 35, 200, 2000),
    ("plan_monthly", "Monthly Semester Pro", 720, 500000, None, 500, 5000),
]

# Plans that are no longer sold. They stay in the table so old ledger rows still resolve.
RETIRED_PLAN_IDS = ("plan_biweekly",)

RADIUS_USERNAME_TABLES = ("radcheck", "radreply", "radusergroup", "radacct")


def column_exists(engine: Engine, table: str, column: str) -> bool:
    if not table_exists(engine, table):
        return False
    return any(c["name"] == column for c in inspect(engine).get_columns(table))


def table_exists(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)


def datetime_sql(engine: Engine) -> str:
    """The column type for a timestamp in raw DDL. PostgreSQL has no DATETIME."""
    return "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"


def create_missing_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    log.info("tables checked")


def upgrade_per_device_plans(engine: Engine) -> None:
    """Bring a database from account-level plans to per-device plans. Idempotent."""
    dt = datetime_sql(engine)
    with engine.begin() as conn:
        if not column_exists(engine, "user_devices", "access_expires_at"):
            conn.execute(text(f"ALTER TABLE user_devices ADD COLUMN access_expires_at {dt} NULL"))
            log.info("added user_devices.access_expires_at")
        if not column_exists(engine, "user_devices", "expiry_handled_for"):
            conn.execute(text(f"ALTER TABLE user_devices ADD COLUMN expiry_handled_for {dt} NULL"))
            log.info("added user_devices.expiry_handled_for")

    # Old databases carried the plan on the account. Copy it to each paid account's
    # devices, then drop it from users. Runs only while the old columns still exist.
    # A correlated subquery is used instead of MySQL's UPDATE ... JOIN, so it runs on both.
    if column_exists(engine, "users", "access_expires_at"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE user_devices SET "
                    "access_expires_at = (SELECT u.access_expires_at FROM users u "
                    "    WHERE u.id = user_devices.user_id), "
                    "expiry_handled_for = (SELECT u.expiry_handled_for FROM users u "
                    "    WHERE u.id = user_devices.user_id) "
                    "WHERE access_expires_at IS NULL AND EXISTS ("
                    "    SELECT 1 FROM users u WHERE u.id = user_devices.user_id "
                    "    AND u.access_type = 'paid')"
                )
            )
            log.info("copied account plans to devices")
    for column in ("access_expires_at", "expiry_handled_for"):
        if column_exists(engine, "users", column):
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users DROP COLUMN {column}"))
            log.info("dropped users.%s", column)

    if engine.dialect.name == "mysql" and table_exists(engine, "coa_queue"):
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE coa_queue MODIFY reason ENUM({COA_REASONS}) NOT NULL"))
    elif engine.dialect.name == "postgresql":
        # PostgreSQL enums are named types. Add any reason the type does not have yet.
        with engine.begin() as conn:
            for reason in COA_REASON_VALUES:
                conn.execute(text(f"ALTER TYPE coa_reason ADD VALUE IF NOT EXISTS '{reason}'"))
        log.info("coa_reason enum checked")
    else:
        log.info("skipped coa_queue enum update (SQLite stores the reason as plain text)")


def upgrade_points_schema(engine: Engine) -> None:
    """Add the points columns to an existing database. Idempotent.

    Plain ADD COLUMN with a default works on SQLite, PostgreSQL and MySQL alike.
    """
    with engine.begin() as conn:
        if not column_exists(engine, "users", "points"):
            conn.execute(text("ALTER TABLE users ADD COLUMN points INTEGER NOT NULL DEFAULT 0"))
            log.info("added users.points")
        if not column_exists(engine, "plans", "points_award"):
            conn.execute(text("ALTER TABLE plans ADD COLUMN points_award INTEGER NOT NULL DEFAULT 0"))
            log.info("added plans.points_award")
        if not column_exists(engine, "plans", "points_cost"):
            conn.execute(text("ALTER TABLE plans ADD COLUMN points_cost INTEGER NULL"))
            log.info("added plans.points_cost")


def upgrade_account_schema(engine: Engine) -> None:
    """Add the admin, forced-password-change and gated end-date columns. Idempotent."""
    dt = datetime_sql(engine)
    with engine.begin() as conn:
        if not column_exists(engine, "users", "is_admin"):
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
            log.info("added users.is_admin")
        if not column_exists(engine, "users", "must_change_password"):
            conn.execute(
                text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE")
            )
            log.info("added users.must_change_password")
        if not column_exists(engine, "users", "gated_until"):
            conn.execute(text(f"ALTER TABLE users ADD COLUMN gated_until {dt} NULL"))
            log.info("added users.gated_until")
        if not column_exists(engine, "users", "full_name"):
            conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(100) NULL"))
            log.info("added users.full_name")
        if not column_exists(engine, "ledger", "refunded_at"):
            conn.execute(text(f"ALTER TABLE ledger ADD COLUMN refunded_at {dt} NULL"))
            log.info("added ledger.refunded_at")
        if not column_exists(engine, "plans", "device_count"):
            conn.execute(text("ALTER TABLE plans ADD COLUMN device_count INTEGER NOT NULL DEFAULT 1"))
            log.info("added plans.device_count")
        if not column_exists(engine, "user_devices", "priority"):
            conn.execute(text("ALTER TABLE user_devices ADD COLUMN priority INTEGER NULL"))
            log.info("added user_devices.priority")
        if not column_exists(engine, "ledger", "payer_email"):
            conn.execute(text("ALTER TABLE ledger ADD COLUMN payer_email VARCHAR(255) NULL"))
            log.info("added ledger.payer_email")


def seed_plans(engine: Engine) -> None:
    """Insert or update the storefront plans and retire the old ones. Uses the ORM,
    so it works on every database."""
    with Session(engine) as session:
        for plan_id, title, hours, price, speed, award, cost in PLANS:
            plan = session.get(Plan, plan_id)
            if plan is None:
                plan = Plan(id=plan_id)
                session.add(plan)
            plan.title = title
            plan.duration_hours = hours
            plan.price_kobo = price
            plan.speed_limit_mbps = speed
            plan.points_award = award
            plan.points_cost = cost
            plan.is_active = True
        for plan_id in RETIRED_PLAN_IDS:
            plan = session.get(Plan, plan_id)
            if plan is not None:
                plan.is_active = False
        session.commit()
    log.info("plans seeded")


def widen_radius_usernames(engine: Engine) -> None:
    """Make the RADIUS username columns long enough for per-device usernames. Idempotent.

    FreeRADIUS's PostgreSQL schema already uses text for usernames, so nothing to do there.
    SQLite does not enforce VARCHAR length, so nothing to do there either.
    """
    if engine.dialect.name == "postgresql":
        log.info("skipped RADIUS username widening (PostgreSQL usernames are text)")
        return
    if engine.dialect.name != "mysql":
        log.info("skipped RADIUS username widening (SQLite does not limit length)")
        return
    with engine.begin() as conn:
        for table in RADIUS_USERNAME_TABLES:
            if table_exists(engine, table):
                conn.execute(text(f"ALTER TABLE {table} MODIFY username VARCHAR(255) NOT NULL DEFAULT ''"))
                log.info("widened %s.username", table)


def allow_unlimited_devices(engine: Engine) -> None:
    """Let users.max_devices be NULL, which means no device limit. Idempotent.

    Existing accounts keep their current numbers. This only changes the column, not the rows.
    SQLite does not enforce NOT NULL on an existing column here, so nothing to do there.
    """
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ALTER COLUMN max_devices DROP NOT NULL"))
        log.info("users.max_devices now allows NULL (no limit)")
    elif engine.dialect.name == "mysql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users MODIFY max_devices INT NULL"))
        log.info("users.max_devices now allows NULL (no limit)")


def run() -> None:
    create_missing_tables(main_engine)
    upgrade_per_device_plans(main_engine)
    upgrade_points_schema(main_engine)
    upgrade_account_schema(main_engine)
    allow_unlimited_devices(main_engine)
    seed_plans(main_engine)
    widen_radius_usernames(radius_engine)
    log.info("database setup complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        run()
    except SQLAlchemyError:
        log.exception("database setup failed")
        raise SystemExit(1)
