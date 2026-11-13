"""Engine construction shared by the main database and the radius database.

Three backends are supported:
- SQLite: local development and tests. Foreign keys are switched on for every
  connection, because SQLite leaves them off by default.
- PostgreSQL (Supabase): production. Use a postgresql+psycopg:// URL.
- MySQL: kept working as an option. Use a mysql+pymysql:// URL.
"""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        db_path = parsed.database
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)

    if parsed.get_backend_name() == "postgresql":
        # Supabase's connection pooler (transaction mode) does not support server-side
        # prepared statements, so turn them off on every connection.
        @event.listens_for(engine, "connect")
        def _no_prepared_statements(dbapi_connection, _record):
            dbapi_connection.prepare_threshold = None

    return engine
