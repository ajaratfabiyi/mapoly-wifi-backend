"""RADIUS Disconnect-Request (CoA) to the MikroTik, UDP 3799 (RFC 5176).

Used when a plan expires or a device is removed, so an active session ends now
instead of at the next reconnect. Timeouts and rejects return False; they never raise
into a user request.
"""

import hashlib
import os
import socket
import struct

from sqlalchemy import text
from sqlalchemy.orm import Session

CODE_DISCONNECT_REQUEST = 40
CODE_DISCONNECT_ACK = 41
COA_PORT = 3799

ATTR_USER_NAME = 1
ATTR_CALLING_STATION_ID = 31


def _attr(attr_type: int, value: bytes) -> bytes:
    return struct.pack("!BB", attr_type, len(value) + 2) + value


def build_disconnect_request(
    secret: bytes, identifier: int, attributes: list[tuple[int, bytes]]
) -> tuple[bytes, bytes]:
    """Return (packet, request_authenticator).

    CoA request authenticator = MD5(Code | ID | Length | 16 zero bytes | Attributes | Secret).
    """
    attrs = b"".join(_attr(t, v) for t, v in attributes)
    length = 20 + len(attrs)
    header = struct.pack("!BBH", CODE_DISCONNECT_REQUEST, identifier, length)
    authenticator = hashlib.md5(header + b"\x00" * 16 + attrs + secret).digest()
    return header + authenticator + attrs, authenticator


def _valid_response(data: bytes, identifier: int, request_auth: bytes, secret: bytes) -> bool:
    if len(data) < 20:
        return False
    code, ident, length = struct.unpack("!BBH", data[:4])
    if code != CODE_DISCONNECT_ACK or ident != identifier or length != len(data):
        return False
    expected = hashlib.md5(data[:4] + request_auth + data[20:length] + secret).digest()
    return data[4:20] == expected


def send_disconnect(
    host: str,
    secret: str,
    username: str,
    calling_station_id: str,
    timeout: float = 3.0,
) -> bool:
    """Send one Disconnect-Request and return True only on a valid ACK."""
    secret_b = secret.encode()
    identifier = os.urandom(1)[0]
    packet, request_auth = build_disconnect_request(
        secret_b,
        identifier,
        [
            (ATTR_USER_NAME, username.encode()),
            (ATTR_CALLING_STATION_ID, calling_station_id.encode()),
        ],
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (host, COA_PORT))
            data, _ = sock.recvfrom(4096)
    except OSError:
        return False
    return _valid_response(data, identifier, request_auth, secret_b)


def disconnect_user(radius_db: Session, username: str, nas_host: str, secret: str) -> int:
    """Disconnect every open session for this user. Returns the number acknowledged.

    Open sessions are rows in radacct with no acctstoptime.
    """
    if not nas_host or not secret:
        return 0
    from app.services.radius import ACCOUNT_MATCH, account_match_params

    rows = radius_db.execute(
        text(
            "SELECT username, callingstationid FROM radacct "
            f"WHERE {ACCOUNT_MATCH} AND acctstoptime IS NULL"
        ),
        account_match_params(username),
    ).all()
    acked = 0
    for session_user, calling_station in rows:
        if send_disconnect(nas_host, secret, session_user, calling_station):
            acked += 1
    return acked
