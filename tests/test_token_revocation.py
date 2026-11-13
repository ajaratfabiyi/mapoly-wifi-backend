"""Earlier unused login tokens are revoked before a new one is issued."""

from datetime import datetime
from unittest.mock import MagicMock

from app.models import LoginToken
from app.services.login_tokens import revoke_user_tokens


def _scalars_returning(rows):
    result = MagicMock()
    result.all.return_value = rows
    db = MagicMock()
    db.scalars.return_value = result
    return db


def test_no_tracked_tokens_still_clears_radius_rows():
    # A tracking row can be gone while its radcheck rows remain (cleanup removes the
    # tracking row only). The RADIUS rows must be cleared anyway, or they accumulate.
    db = _scalars_returning([])
    radius = MagicMock()
    r = MagicMock()

    assert revoke_user_tokens(db, radius, r, "a@mapoly.edu.ng") == 0
    sql = str(radius.execute.call_args[0][0])
    assert "DELETE FROM radcheck" in sql
    assert "Calling-Station-Id" in sql
    radius.commit.assert_called_once()
    r.delete.assert_not_called()
    db.execute.assert_not_called()


def test_earlier_tokens_removed_from_radius_redis_and_tracking():
    now = datetime(2026, 11, 12, 12, 0, 0)
    rows = [
        LoginToken(token="old1", username="a@mapoly.edu.ng", expires_at=now, created_at=now),
        LoginToken(token="old2", username="a@mapoly.edu.ng", expires_at=now, created_at=now),
    ]
    db = _scalars_returning(rows)
    radius = MagicMock()
    r = MagicMock()

    assert revoke_user_tokens(db, radius, r, "a@mapoly.edu.ng") == 2

    # RADIUS: the Cleartext-Password rows for this user are removed and committed.
    sql = str(radius.execute.call_args[0][0])
    assert "DELETE FROM radcheck" in sql
    assert "Cleartext-Password" in sql
    radius.commit.assert_called_once()

    # Redis: each old token key is removed.
    r.delete.assert_called_once_with("login_token:old1", "login_token:old2")

    # Tracking table: the rows for this user are deleted in the session.
    assert db.execute.called


def test_radius_failure_leaves_tracking_rows_for_cleanup():
    now = datetime(2026, 11, 12, 12, 0, 0)
    rows = [LoginToken(token="old1", username="a@mapoly.edu.ng", expires_at=now, created_at=now)]
    db = _scalars_returning(rows)
    radius = MagicMock()
    radius.execute.side_effect = RuntimeError("radius down")
    r = MagicMock()

    try:
        revoke_user_tokens(db, radius, r, "a@mapoly.edu.ng")
    except RuntimeError:
        pass

    # The tracking delete never ran, so the cleanup job can still find the old token.
    db.execute.assert_not_called()
    r.delete.assert_not_called()
