"""Device MAC cache in Redis: wifi:devices:<email> is a set of the account's bound MACs.

The database (user_devices) is the source of truth. This set is a write-through copy,
updated only after the database change commits. A Redis failure is logged and never
fails the request: the next change to the same account rewrites the entry it touches,
and the database still answers every question.
"""

import logging

import redis

log = logging.getLogger(__name__)


def devices_key(email: str) -> str:
    return f"wifi:devices:{email}"


def cache_device_added(r: redis.Redis, email: str, mac: str) -> None:
    try:
        r.sadd(devices_key(email), mac)
    except redis.RedisError:
        log.warning("Could not add device to Redis cache for an account", exc_info=True)


def cache_device_removed(r: redis.Redis, email: str, mac: str) -> None:
    try:
        r.srem(devices_key(email), mac)
    except redis.RedisError:
        log.warning("Could not remove device from Redis cache for an account", exc_info=True)
