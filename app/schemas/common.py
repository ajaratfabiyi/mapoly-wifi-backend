from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def _to_utc_iso(value: datetime | None) -> str | None:
    """Stored values are naive UTC. Send them as ISO 8601 with a Z (Section 5)."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[
    datetime | None,
    PlainSerializer(_to_utc_iso, return_type=str | None),
]
