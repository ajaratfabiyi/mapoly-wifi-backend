"""Login rate limits (Section 8): 5 failures per email and 20 per IP in 10 minutes.

Counters live in Redis. Only failed attempts are counted.
"""

import redis

EMAIL_LIMIT = 5
IP_LIMIT = 20
WINDOW_SECONDS = 600


def is_limited(r: redis.Redis, key: str, limit: int) -> bool:
    raw = r.get(key)
    return raw is not None and int(raw) >= limit


def record_failure(r: redis.Redis, key: str) -> None:
    count = r.incr(key)
    if count == 1:
        r.expire(key, WINDOW_SECONDS)
