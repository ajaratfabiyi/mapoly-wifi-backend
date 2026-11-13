from datetime import datetime, timezone


def parse_paystack_time(value: str | None, fallback: datetime) -> datetime:
    """Paystack sends ISO 8601 with a Z or offset. Return naive UTC, as the DB stores it."""
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
