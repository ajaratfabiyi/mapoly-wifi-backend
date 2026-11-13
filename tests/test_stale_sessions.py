"""Open accounting sessions with no recent update are closed."""

from unittest.mock import MagicMock

from app.jobs.stale_sessions import CLOSE_CAUSE, close_stale_sessions


def test_closes_only_open_stale_rows_using_database_clock():
    radius = MagicMock()
    radius.execute.return_value.rowcount = 3

    assert close_stale_sessions(radius, 15) == 3

    stmt, params = str(radius.execute.call_args[0][0]), radius.execute.call_args[0][1]
    assert "UPDATE radacct" in stmt
    assert "acctstoptime IS NULL" in stmt          # only open sessions
    assert "NOW() - INTERVAL :minutes MINUTE" in stmt  # database clock
    assert "COALESCE(acctupdatetime, acctstarttime)" in stmt  # last interim update
    assert params == {"cause": CLOSE_CAUSE, "minutes": 15}
    radius.commit.assert_called_once()


def test_no_stale_rows_returns_zero():
    radius = MagicMock()
    radius.execute.return_value.rowcount = 0
    assert close_stale_sessions(radius, 15) == 0
