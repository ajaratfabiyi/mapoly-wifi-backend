import hashlib
import hmac
import struct

from app.config import get_settings
from app.services.coa import (
    ATTR_USER_NAME,
    CODE_DISCONNECT_REQUEST,
    _valid_response,
    build_disconnect_request,
)
from app.services.paystack import verify_webhook_signature


def test_webhook_signature_accepts_correct_hmac():
    body = b'{"event":"charge.success"}'
    key = get_settings().PAYSTACK_SECRET_KEY.encode()
    sig = hmac.new(key, body, hashlib.sha512).hexdigest()
    assert verify_webhook_signature(body, sig) is True


def test_webhook_signature_rejects_wrong_body_and_missing_header():
    body = b'{"event":"charge.success"}'
    key = get_settings().PAYSTACK_SECRET_KEY.encode()
    sig = hmac.new(key, body, hashlib.sha512).hexdigest()
    assert verify_webhook_signature(b'{"event":"forged"}', sig) is False
    assert verify_webhook_signature(body, None) is False


def test_coa_packet_has_correct_length_and_authenticator():
    secret = b"shared-secret"
    packet, auth = build_disconnect_request(secret, 7, [(ATTR_USER_NAME, b"a@mapoly.edu.ng")])
    code, ident, length = struct.unpack("!BBH", packet[:4])
    assert code == CODE_DISCONNECT_REQUEST
    assert ident == 7
    assert length == len(packet)
    expected = hashlib.md5(packet[:4] + b"\x00" * 16 + packet[20:] + secret).digest()
    assert auth == expected


def test_coa_response_authenticator_is_checked():
    secret = b"shared-secret"
    req_auth = b"R" * 16
    body = b""
    length = 20 + len(body)
    head = struct.pack("!BBH", 41, 9, length)
    good = head + hashlib.md5(head + req_auth + body + secret).digest() + body
    bad = head + b"\x00" * 16 + body
    assert _valid_response(good, 9, req_auth, secret) is True
    assert _valid_response(bad, 9, req_auth, secret) is False
