"""Client IP resolution behind Render's proxy (app/utils/client_ip.py)."""

from types import SimpleNamespace

from starlette.requests import Request

from app.utils import client_ip as module


def _request(peer: str, forwarded: str | None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (peer, 1234),
    }
    return Request(scope)


def _set_trust(monkeypatch, trust: bool) -> None:
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(TRUST_PROXY_HEADERS=trust)
    )


def test_ignores_forwarded_header_when_not_trusted(monkeypatch):
    _set_trust(monkeypatch, False)
    req = _request("10.0.0.1", "198.51.100.7")
    assert module.client_ip(req) == "10.0.0.1"


def test_uses_rightmost_entry_when_trusted(monkeypatch):
    _set_trust(monkeypatch, True)
    # The client may send a fake leading entry; the proxy appends the real one last.
    req = _request("10.0.0.1", "203.0.113.9, 198.51.100.7")
    assert module.client_ip(req) == "198.51.100.7"


def test_falls_back_to_peer_when_header_missing(monkeypatch):
    _set_trust(monkeypatch, True)
    req = _request("10.0.0.1", None)
    assert module.client_ip(req) == "10.0.0.1"


def test_falls_back_to_peer_when_header_empty(monkeypatch):
    _set_trust(monkeypatch, True)
    req = _request("10.0.0.1", " , ")
    assert module.client_ip(req) == "10.0.0.1"
