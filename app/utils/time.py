from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive UTC datetime, matching how the MySQL DATETIME columns store values."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
