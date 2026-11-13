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
3. Seeds the four storefront plans (updates prices and titles if they changed).
4. Widens the RADIUS username columns to VARCHAR(255), in the radius database, if the
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

PLANS = [
    ("plan_2days", "2-Day Campus Pass", 48, 40000, 25),
    ("plan_weekly", "Weekly Academic Pass", 168, 120000, 35),
    ("plan_biweekly", "Bi-Weekly Scholar Pass", 336, 220000, None),
    ("plan_monthly", "Monthly Semester Pro", 720, 400000, None),
]

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


def seed_plans(engine: Engine) -> None:
    """Insert or update the four storefront plans. Uses the ORM, so it works on both databases."""
    with Session(engine) as session:
        for plan_id, title, hours, price, speed in PLANS:
            plan = session.get(Plan, plan_id)
            if plan is None:
                plan = Plan(id=plan_id, is_active=True)
                session.add(plan)
            plan.title = title
            plan.duration_hours = hours
            plan.price_kobo = price
            plan.speed_limit_mbps = speed
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


def run() -> None:
    create_missing_tables(main_engine)
    upgrade_per_device_plans(main_engine)
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
