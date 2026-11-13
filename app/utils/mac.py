"""MAC address rules. The rule lives here so the whole app uses one format.

Stored format:  AA:BB:CC:DD:EE:FF  (uppercase, colons)
RADIUS format:  AA-BB-CC-DD-EE-FF  (what FreeRADIUS/MikroTik may send as Calling-Station-Id)
"""

import re

_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
_MAC_DASH_RE = re.compile(r"^([0-9A-F]{2}-){5}[0-9A-F]{2}$")


class InvalidMacError(ValueError):
    pass


def normalise_mac(raw: str) -> str:
    """Return the stored form, or raise InvalidMacError.

    Accepts colons or dashes in any case. Anything else is rejected.
    """
    if not isinstance(raw, str):
        raise InvalidMacError(raw)
    candidate = raw.strip().upper().replace("-", ":")
    if not _MAC_RE.match(candidate):
        raise InvalidMacError(raw)
    return candidate


def to_radius_format(stored: str) -> str:
    """AA:BB:CC:DD:EE:FF -> AA-BB-CC-DD-EE-FF"""
    return normalise_mac(stored).replace(":", "-")


def from_radius_format(radius: str) -> str:
    """AA-BB-CC-DD-EE-FF -> AA:BB:CC:DD:EE:FF"""
    if not _MAC_DASH_RE.match(radius.strip().upper()):
        raise InvalidMacError(radius)
    return normalise_mac(radius)
