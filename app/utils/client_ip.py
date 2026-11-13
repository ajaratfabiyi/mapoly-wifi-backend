"""Resolve the student's real IP address behind a reverse proxy.

On Render, every request reaches the app from Render's proxy, so request.client.host
is the proxy's address. Rate limits keyed on it would be shared by every student.

Render's proxy appends the address it received to X-Forwarded-For. The rightmost
entry is therefore the one written by the trusted proxy. Entries to its left can be
supplied by the client, so they are never used.

This is only correct when the app sits behind exactly one trusted proxy. Set
TRUST_PROXY_HEADERS=true on Render only, never locally, where a client could reach
the app directly and fake the header.
"""

from fastapi import Request

from app.config import get_settings


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if not get_settings().TRUST_PROXY_HEADERS:
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    entries = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not entries:
        return peer
    return entries[-1]
