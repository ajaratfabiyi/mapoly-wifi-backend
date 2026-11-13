"""Access rules (Section 4). Access is per device.

- Each device has its own plan and expiry (user_devices.access_expires_at). A plan
  bought for one device works only on that device.
- Free users (gated) have access on every bound device while the account is active.
  They have no expiry.
- Renewal extends a device's plan from the later of now or that device's expiry.
"""

from datetime import datetime, timedelta

from app.models import User, UserDevice


def account_usable(user: User, now: datetime) -> bool:
    return user.is_active and user.status != "disabled"


def has_device_access(user: User, device: UserDevice, now: datetime) -> bool:
    """True if this device may connect right now."""
    if not account_usable(user, now):
        return False
    if user.access_type == "free":
        return True
    return device.access_expires_at is not None and device.access_expires_at > now


def any_device_has_access(user: User, devices: list[UserDevice], now: datetime) -> bool:
    return any(has_device_access(user, d, now) for d in devices)


def extend_device_access(device: UserDevice, duration_hours: int, now: datetime) -> datetime:
    """Set and return this device's new expiry. Does not commit."""
    base = now
    if device.access_expires_at is not None and device.access_expires_at > now:
        base = device.access_expires_at
    device.access_expires_at = base + timedelta(hours=duration_hours)
    return device.access_expires_at


def device_seconds_left(user: User, device: UserDevice, now: datetime) -> int | None:
    """Seconds until this device's expiry, for RADIUS Session-Timeout. None means no timeout."""
    if user.access_type == "free" or device.access_expires_at is None:
        return None
    return max(1, int((device.access_expires_at - now).total_seconds()))
