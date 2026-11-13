import redis

from app.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """FastAPI dependency. One shared client; redis-py manages its own pool."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    return _client
