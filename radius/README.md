# FreeRADIUS configuration (MAPOLY WiFi)

These files are the server-side rules the backend depends on. They live in the
repository so they can be reviewed and version-controlled. Nothing here is applied
automatically. They must be copied onto the FreeRADIUS server (the AAA VM) by hand.

**Status: written, not yet validated.** The authoring environment had no FreeRADIUS
binary and no root access, so `freeradius -Cx` and the test script have not been run.
Run both on the AAA VM before any student uses the network.

## What each rule enforces

| Rule | Where it is set | What it does |
|---|---|---|
| Token is single-use | `sql_queries.post-auth.conf` | After Access-Accept, deletes the matching `Cleartext-Password` row |
| Token only works from its device | Backend writes `Calling-Station-Id == <MAC>` to `radcheck`; FreeRADIUS checks it via `authorize` (default `sql` module) | A token used from another MAC is rejected and not consumed |
| One session per device | Backend writes `Simultaneous-Use := 1` under the device's own username (`<email>\|<MAC>`); FreeRADIUS counts open rows in `radacct` (default `session { sql }`) | A second session from the same device is refused. Two different devices can be online together. |

The backend's rows are written in `app/services/login_tokens.py`.

## Files

| File | Copy to | Action |
|---|---|---|
| `clients.conf.snippet` | `/etc/freeradius/3.0/clients.conf` | Append, then set the router IP and secret |
| `sql_queries.post-auth.conf` | `/etc/freeradius/3.0/mods-config/sql/main/postgresql/queries.conf` | Replace the existing `post-auth` block with this one |
| `sites_default.snippet` | `/etc/freeradius/3.0/sites-enabled/default` | Make the sections match. Check each one against the snippet. |
| `test_radius.sh` | Any machine that can reach the RADIUS server | Run after a token is issued |

## Apply

1. Install FreeRADIUS with the PostgreSQL driver: `apt install freeradius freeradius-postgresql`.
2. Enable the SQL module: `ln -s ../mods-available/sql /etc/freeradius/3.0/mods-enabled/sql`, and set `driver = "rlm_sql_postgresql"` in its config.
3. Set the `sql` module's connection block to the Supabase `radius` database, using the host, port 6543, and credentials from `RADIUS_DATABASE_URL`. Set `sslmode = require` (TLS is mandatory on Supabase).
4. Import FreeRADIUS's PostgreSQL `schema.sql` into the database. Its username columns are VARCHAR(64), so widen them to 255, because a device username is the email plus 18 characters:
   `ALTER TABLE radcheck ALTER COLUMN username TYPE VARCHAR(255);`
   and the same for `radreply`, `radusergroup`, and `radacct`. (`db_setup` does not do this on PostgreSQL.)
5. Apply the three files above.
6. Validate the configuration: `freeradius -Cx -lstdout`. It must end with `Configuration appears to be OK`.
7. Restart: `systemctl restart freeradius`.
8. Set the router to use this server, with the same secret, and enable accounting (router setup, RADIUS and accounting integration).

## Test

Create a test account through the backend, sign in, and take the token it issues. Then run:

```
./test_radius.sh <email> <token> <device-MAC> <other-MAC> <RADIUS host> <shared secret>
```

Give it a second token for the same device (7th argument) and a token for a second
device (8th argument) to run every step. The script checks, in order:
1. A token used from the wrong MAC is rejected.
2. The same token from the right MAC is accepted.
3. The token is rejected on reuse.
4. A second session from the same device is refused while the first is open.
5. A second device is accepted while the first is online, so two devices work at once.
6. After the first session stops, the device can connect again with a fresh token.

Then test on a real phone connected to the campus WiFi.

## Known limits

- **Two devices at once depends on the per-device username.** Each device signs in as
  `<email>|<MAC>`. Anything that matches the account by email alone (for example, an
  admin search) must use `ACCOUNT_MATCH` in `app/services/radius.py`, or it will miss
  devices.
- **A lost accounting Stop closes itself after 15 minutes.** If MikroTik never sends a
  Stop, the `radacct` row stays open and blocks that device. The backend job
  `app/jobs/stale_sessions.py` closes any open row with no interim update for
  `STALE_SESSION_MINUTES` (default 15). It needs MikroTik's interim updates every 5
  minutes (`radius-interim-update=5m`), which the router setup already sets. Check that
  `acctupdatetime` is being written on the first real session.
- **Timestamps come from the database clock.** The stale-session job compares against the
  database's own clock, so it compares like with like. Supabase stores timestamps in UTC;
  keep the FreeRADIUS server's clock synced (NTP) too.
- **MAC matching is exact text.** MikroTik sends `Calling-Station-Id` in uppercase with
  dashes. The backend writes that format. If a router or phone model sends lowercase or
  colons, the MAC check fails. Confirm on the first real device.
- **Disconnects go through a queue.** The backend writes a row to `coa_queue` on plan
  expiry and device removal. The CoA worker (`app/jobs/coa_worker.py`) runs on the AAA VM,
  which is on the campus network, and sends the Disconnect-Request to the router. Failed
  sends are retried with a growing delay, capped at 30 minutes. The worker needs
  `COA_NAS_HOST`, `RADIUS_COA_SECRET`, `DATABASE_URL`, and `RADIUS_DATABASE_URL`. Run it
  every minute from cron on the AAA VM. The router must allow CoA from the AAA VM:
  `/radius incoming set accept=yes` with the same secret.
