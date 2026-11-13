"""Minimal FreeRADIUS tables for the tests, in the syntax of the database under test.

Production uses FreeRADIUS's own schema.sql. These tables only hold the columns the
backend reads or writes, so tests can run without FreeRADIUS installed.
"""

import os


def _is_postgres() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith("postgresql")


def radius_tables() -> dict[str, str]:
    if _is_postgres():
        pk = "BIGSERIAL PRIMARY KEY"
        ts = "TIMESTAMP"
    else:
        pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
        ts = "DATETIME"
    return {
        "radcheck": (
            f"CREATE TABLE radcheck (id {pk}, username VARCHAR(255),"
            " attribute VARCHAR(64), op CHAR(2), value VARCHAR(253))"
        ),
        "radreply": (
            f"CREATE TABLE radreply (id {pk}, username VARCHAR(255),"
            " attribute VARCHAR(64), op CHAR(2), value VARCHAR(253))"
        ),
        "radusergroup": (
            f"CREATE TABLE radusergroup (id {pk}, username VARCHAR(255),"
            " groupname VARCHAR(64), priority INT)"
        ),
        "radacct": (
            f"CREATE TABLE radacct (radacctid {pk}, username VARCHAR(255),"
            f" callingstationid VARCHAR(32), acctstoptime {ts} NULL)"
        ),
    }
