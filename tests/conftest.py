"""Test environment. Must set env vars before anything imports app.config.

By default the tests use fresh SQLite files in a temporary folder.

To run the same tests against PostgreSQL, set MAPOLY_TEST_PG to a server URL without a
database name, for example:
    MAPOLY_TEST_PG=postgresql+psycopg://portal:portal@127.0.0.1:5432
The tests then use the databases mapoly_wifi_test and radius_test on that server. They
drop and recreate their tables, so never point this at a real database.

Either way, the URLs are forced here, not taken from the environment, so the tests
cannot reach production. Redis is still needed (REDIS_URL, database 15 by default).
"""

import os
import tempfile

_pg_base = os.environ.get("MAPOLY_TEST_PG")
if _pg_base:
    os.environ["DATABASE_URL"] = f"{_pg_base.rstrip('/')}/mapoly_wifi_test"
    os.environ["RADIUS_DATABASE_URL"] = f"{_pg_base.rstrip('/')}/radius_test"
else:
    _TEST_DIR = tempfile.mkdtemp(prefix="mapoly-tests-")
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/mapoly_wifi_test.db"
    os.environ["RADIUS_DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/radius_test.db"
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 50)
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:5173")
