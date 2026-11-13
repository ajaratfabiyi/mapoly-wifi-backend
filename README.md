# MAPOLY WiFi Portal: Backend

FastAPI backend for the MAPOLY campus WiFi portal. It handles accounts, devices, plan
purchases (Paystack), and one-time login tokens for the MikroTik router, which checks
them through FreeRADIUS.

The design is in the backend design document (Sections 1 to 13). Section numbers are
cited in the code comments.

## Local development (SQLite)

SQLite is the database for now. No server is needed; the files are created in `./data`.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # then set SECRET_KEY, PAYSTACK_SECRET_KEY, and FRONTEND_ORIGIN
python -m app.db_setup             # creates the tables and the four plans
uvicorn app.main:app --reload
```

Redis is still required (`REDIS_URL`). Run it locally, for example with
`docker run -p 6379:6379 redis`.

Health check: `GET /api/health`.

## Database setup

`python -m app.db_setup` creates missing tables, upgrades older databases to per-device
plans, and seeds the four storefront plans. It is safe to run repeatedly. Run it once
for a new database and again after any deploy that changes the schema.

On SQLite and PostgreSQL everything works. On MySQL, the setup also widens the FreeRADIUS
username columns. PostgreSQL enforces VARCHAR length too, so those columns must be widened
by hand once (see `radius/README.md`, step 4).

## Tests

```bash
pytest
```

The tests use temporary SQLite files (set in `tests/conftest.py`), so they never touch
a real database. They do need Redis. Database 15 is used by default, and the tests flush
it.

Paystack and CoA are mocked, so nothing reaches the network.

## Deployment on Render (Docker, Supabase)

Render runs the web service and the cron jobs in separate containers. They cannot share
a SQLite file, so the shared database is Supabase (PostgreSQL). The image is built from `Dockerfile`, and
`render.yaml` defines one service:

| Service | Type | Runs |
|---|---|---|
| `mapoly-api` | web | the API. Runs `python -m app.db_setup` before each deploy |

There are no cron jobs on Render. Expired plans end when the account is used (login or
dashboard visit), and unused login tokens expire in RADIUS. The CoA worker runs on the AAA
VM. `python -m app.jobs.expiry` and `python -m app.jobs.cleanup` still work as optional
manual runs.

To deploy, in Render choose **New > Blueprint** and select this repository. Render then
asks for each secret marked `sync: false`:

- `DATABASE_URL`, `RADIUS_DATABASE_URL`: Supabase pooler URLs (port 6543) with TLS, for example
  `postgresql+psycopg://postgres.PROJECT_REF:...@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require`
- `REDIS_URL`
- `SECRET_KEY`, `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`
- `COA_NAS_HOST`, `RADIUS_COA_SECRET`

Render must be able to reach the MySQL and Redis servers. The CoA disconnect also needs
to reach the MikroTik over UDP 3799, which sits on the campus network. Until the expiry
job can reach the router, expired sessions continue until the user reconnects.

Take a Supabase backup (or confirm point-in-time recovery is on) before the first deploy
that runs `db_setup` against production. Supabase free projects pause after inactivity,
so use a paid project for production.

## Things to know

- **SQLite is for local development.** Do not use it on Render: the services would not
  share data, and the free disk is wiped on each deploy.
- **Login tokens** expire in 60 seconds (`LOGIN_TOKEN_TTL_SECONDS`). Each token carries an
  `Expiration` in RADIUS, so FreeRADIUS refuses it after that time. This needs the
  `expiration` module in FreeRADIUS and the AAA VM's clock set to UTC (see `radius/README.md`).
- **Payment webhook** replies 200 at once and applies the charge in the background. If
  that step fails, Paystack does not retry. The `verify` endpoint is the recovery path.
- **Dashboard sessions** are signed cookies with no server-side record, so they cannot
  be revoked before they expire (7 days).
- **Open decisions** from the design document (Section 11) include the matric number
  format, plan speed limits, the router's management address (`COA_NAS_HOST`), and
  whether payments can omit `device_id`.

## Project layout

```
app/
  main.py             FastAPI app, CORS, routers
  config.py           environment settings
  db_engine.py        builds SQLite, PostgreSQL, or MySQL engines
  services/expiry.py  ends a device's plan when the account is used (login, dashboard)
  db_setup.py         creates and upgrades the database
  database.py         main database session
  radius_database.py  radius database session
  redis_client.py     Redis client
  dependencies.py     get_current_user (dashboard session check)
  models/             SQLAlchemy models
  schemas/            request and response models
  routers/            HTTP routes, no business logic
  services/           business rules and external calls
  security/           password hashing and session cookies
  utils/              MAC handling, time helpers
  jobs/               expiry, cleanup, CoA worker, stale sessions
radius/               FreeRADIUS config snippets (applied by hand on the AAA VM)
tests/                pytest suite
```
