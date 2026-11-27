"""CoA queue: the backend writes rows, and the worker sends and retries them."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.jobs import coa_worker
from app.models import CoaQueue
from app.services.coa_queue import enqueue_disconnect

NOW = datetime(2026, 11, 12, 12, 0, 0)


def test_enqueue_device_removed_stores_radius_mac():
    db = MagicMock()
    row = enqueue_disconnect(db, "device_removed", NOW, mac="a4:c3:f0:88:91:2e")
    assert row.mac_address == "A4-C3-F0-88-91-2E"
    assert row.username is None
    db.add.assert_called_once_with(row)


def test_enqueue_plan_expired_needs_username():
    with pytest.raises(ValueError):
        enqueue_disconnect(MagicMock(), "plan_expired", NOW)


def test_enqueue_device_removed_needs_mac():
    with pytest.raises(ValueError):
        enqueue_disconnect(MagicMock(), "device_removed", NOW)


def test_enqueue_rejects_unknown_reason():
    with pytest.raises(ValueError):
        enqueue_disconnect(MagicMock(), "bogus", NOW, username="a@mapoly.edu.ng")


def _row(**kw):
    base = dict(id=1, reason="plan_expired", username="a@mapoly.edu.ng",
                mac_address=None, sent_at=None, attempts=0,
                next_attempt_at=None, last_error=None)
    base.update(kw)
    return CoaQueue(**base)


def test_row_marked_sent_when_every_session_acknowledged():
    db, radius = MagicMock(), MagicMock()
    radius.execute.return_value.all.return_value = [("a@mapoly.edu.ng|A4-C3-F0-88-91-2E", "A4-C3-F0-88-91-2E")]
    row = _row()
    with patch.object(coa_worker, "send_disconnect", return_value=True) as send:
        assert coa_worker.process_row(db, radius, row, "10.0.0.1", "secret", NOW) is True
    send.assert_called_once()
    assert row.sent_at == NOW
    db.commit.assert_called()


def test_row_retried_with_backoff_when_router_does_not_answer():
    db, radius = MagicMock(), MagicMock()
    radius.execute.return_value.all.return_value = [("a@mapoly.edu.ng|A4-C3-F0-88-91-2E", "A4-C3-F0-88-91-2E")]
    row = _row(attempts=2)
    with patch.object(coa_worker, "send_disconnect", return_value=False):
        assert coa_worker.process_row(db, radius, row, "10.0.0.1", "secret", NOW) is False
    assert row.sent_at is None
    assert row.attempts == 3
    assert row.next_attempt_at == datetime(2026, 11, 12, 12, 3, 0)
    assert "no CoA ACK" in row.last_error


def test_row_with_no_open_sessions_is_done_without_sending():
    db, radius = MagicMock(), MagicMock()
    radius.execute.return_value.all.return_value = []
    row = _row()
    with patch.object(coa_worker, "send_disconnect") as send:
        assert coa_worker.process_row(db, radius, row, "10.0.0.1", "secret", NOW) is True
    send.assert_not_called()
    assert row.sent_at == NOW


def test_backoff_is_capped():
    db, radius = MagicMock(), MagicMock()
    radius.execute.return_value.all.return_value = [("u", "A4-C3-F0-88-91-2E")]
    row = _row(attempts=500)
    with patch.object(coa_worker, "send_disconnect", return_value=False):
        coa_worker.process_row(db, radius, row, "10.0.0.1", "secret", NOW)
    assert row.next_attempt_at == datetime(2026, 11, 12, 12, 30, 0)


def test_device_removed_finds_sessions_by_mac():
    radius = MagicMock()
    radius.execute.return_value.all.return_value = []
    row = CoaQueue(id=2, reason="device_removed", mac_address="A4-C3-F0-88-91-2E")
    coa_worker.open_sessions(radius, row)
    sql = str(radius.execute.call_args[0][0])
    assert "callingstationid = :m" in sql
    # radacct stores Calling-Station-Id with colons, so the lookup must use colons.
    assert radius.execute.call_args[0][1] == {"m": "A4:C3:F0:88:91:2E"}
