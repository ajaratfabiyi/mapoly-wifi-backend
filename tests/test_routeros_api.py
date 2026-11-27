import socket
from unittest.mock import MagicMock

import pytest

from app.jobs import coa_worker
from app.models import CoaQueue
from app.services.routeros_api import RouterApi, RouterApiError, encode_length, encode_word


@pytest.mark.parametrize("n", [0, 1, 0x7F, 0x80, 0x3FFF, 0x4000, 0x1FFFFF, 0x200000, 0x0FFFFFFF, 0x10000000])
def test_length_round_trip(n):
    a, b = socket.socketpair()
    try:
        b.sendall(encode_length(n))
        assert RouterApi(a)._read_length() == n
    finally:
        a.close()
        b.close()


def test_word_is_length_prefixed():
    assert encode_word("/login") == bytes([6]) + b"/login"


def test_session_lookup_sends_mac_and_reads_ids():
    client, router = socket.socketpair()
    try:
        # The router's reply, written before the call: one session, then done.
        router.sendall(
            encode_word("!re") + encode_word("=.id=*1") + b"\x00"
            + encode_word("!done") + b"\x00"
        )
        ids = RouterApi(client).hotspot_session_ids("c8:3c:85:33:5b:47")
        assert ids == ["*1"]
        sent = router.recv(4096)
        assert b"/ip/hotspot/active/print" in sent
        assert b"?mac-address=C8:3C:85:33:5B:47" in sent
    finally:
        client.close()
        router.close()


def test_trap_raises_with_router_message():
    client, router = socket.socketpair()
    try:
        router.sendall(encode_word("!trap") + encode_word("=message=no such user") + b"\x00"
                       + encode_word("!done") + b"\x00")
        with pytest.raises(RouterApiError, match="no such user"):
            RouterApi(client).login("x", "y")
    finally:
        client.close()
        router.close()


def test_failed_router_call_keeps_row_for_retry(monkeypatch):
    monkeypatch.setattr(coa_worker, "open_sessions", lambda radius_db, row: [])

    def broken(mac):
        raise RouterApiError("router unreachable")

    row = CoaQueue(id=9, reason="device_removed", mac_address="C8-3C-85-33-5B-47", attempts=0)
    db = MagicMock()
    done = coa_worker.process_row(db, MagicMock(), row, "10.10.10.1", "secret", MagicMock(),
                                  router_end=broken)
    assert done is False
    assert row.attempts == 1
    assert row.sent_at is None


def test_router_end_called_with_device_mac(monkeypatch):
    monkeypatch.setattr(coa_worker, "open_sessions", lambda radius_db, row: [])
    seen = []
    row = CoaQueue(id=10, reason="device_removed", mac_address="C8-3C-85-33-5B-47", attempts=0)
    db = MagicMock()
    done = coa_worker.process_row(db, MagicMock(), row, "10.10.10.1", "secret", MagicMock(),
                                  router_end=seen.append)
    assert done is True
    assert seen == ["C8-3C-85-33-5B-47"]
