"""Paystack calls and webhook signature check (Section 6).

Only the secret key is used here. The public key goes to the frontend and is never
needed on the server.
"""

import hashlib
import hmac

import httpx

from app.config import get_settings

BASE_URL = "https://api.paystack.co"
TIMEOUT_SECONDS = 10.0


class PaystackError(Exception):
    """Paystack is unreachable or refused the request. Callers map this to 502."""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _unwrap(resp: httpx.Response) -> dict:
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaystackError(str(exc)) from exc
    body = resp.json()
    if not body.get("status"):
        raise PaystackError(body.get("message", "Paystack returned no status"))
    return body["data"]


def initialize_transaction(
    email: str,
    amount_kobo: int,
    reference: str,
    callback_url: str | None = None,
) -> dict:
    """Return Paystack's data: authorization_url, access_code, reference."""
    payload: dict = {"email": email, "amount": amount_kobo, "reference": reference}
    if callback_url:
        payload["callback_url"] = callback_url
    try:
        resp = httpx.post(
            f"{BASE_URL}/transaction/initialize",
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PaystackError(str(exc)) from exc
    return _unwrap(resp)


def verify_transaction(reference: str) -> dict:
    """Ask Paystack directly about a reference. Never trust a redirect alone (Section 5.10)."""
    try:
        resp = httpx.get(
            f"{BASE_URL}/transaction/verify/{reference}",
            headers=_headers(),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise PaystackError(str(exc)) from exc
    return _unwrap(resp)


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """HMAC-SHA512 of the exact raw bytes, compared in constant time (Section 6, step 2)."""
    if not signature:
        return False
    key = get_settings().PAYSTACK_SECRET_KEY.encode()
    expected = hmac.new(key, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
