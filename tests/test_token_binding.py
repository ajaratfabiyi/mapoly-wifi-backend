"""Each token is bound to its device MAC and the account allows one session at a time."""

from datetime import datetime
from unittest.mock import MagicMock

from app.services.login_tokens import issue_token


def _run_issue(mac: str):
    db = MagicMock()
    db.scalars.return_value.all.return_value = []  # no earlier tokens
    radius = MagicMock()
    r = MagicMock()
    token = issue_token(
        db=db,
        radius_db=radius,
        r=r,
        username="a@mapoly.edu.ng",
        user_type="student",
        mac=mac,
        session_seconds=3600,
        ttl_seconds=60,
        now=datetime(2026, 11, 12, 12, 0, 0),
    )
    calls = [(str(c[0][0]), c[0][1] if len(c[0]) > 1 else c[1]) for c in radius.execute.call_args_list]
    return token, calls


def test_token_row_bound_to_device_mac_in_router_format():
    # The router sends Calling-Station-Id with colons (confirmed by tcpdump).
    _, calls = _run_issue("a4:c3:f0:88:91:2e")
    mac_rows = [params for sql, params in calls if "Calling-Station-Id" in sql and "INSERT" in sql]
    assert mac_rows == [{"u": "a@mapoly.edu.ng", "m": "A4:C3:F0:88:91:2E"}]


def test_simultaneous_use_replaced_not_duplicated():
    _, calls = _run_issue("A4:C3:F0:88:91:2E")
    sqls = [sql for sql, _ in calls]
    deletes = [s for s in sqls if "DELETE FROM radcheck" in s and "Simultaneous-Use" in s]
    inserts = [s for s in sqls if "INSERT INTO radcheck" in s and "Simultaneous-Use" in s]
    assert len(deletes) == 1 and len(inserts) == 1
    # The delete must come before the insert, so the row is never doubled.
    assert sqls.index(deletes[0]) < sqls.index(inserts[0])


def test_token_row_written_alongside_mac_row():
    token, calls = _run_issue("A4:C3:F0:88:91:2E")
    cleartext = [params for sql, params in calls if "Cleartext-Password" in sql and "INSERT" in sql]
    assert cleartext == [{"u": "a@mapoly.edu.ng", "v": token}]
