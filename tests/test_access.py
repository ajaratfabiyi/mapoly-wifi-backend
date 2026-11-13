from datetime import datetime, timedelta

from app.models import User, UserDevice
from app.services.access import (
    any_device_has_access,
    device_seconds_left,
    extend_device_access,
    has_device_access,
)

NOW = datetime(2026, 11, 12, 12, 0, 0)
PHONE = "A4:C3:F0:88:91:01"
LAPTOP = "A4:C3:F0:88:91:02"


def account(access_type="paid", active=True, user_type="student"):
    return User(
        email="a@mapoly.edu.ng",
        user_type=user_type,
        status="active",
        access_type=access_type,
        is_active=active,
        max_devices=2,
    )


def device(mac=PHONE, expires=None):
    return UserDevice(user_id=1, mac_address=mac, access_expires_at=expires)


def test_paid_device_with_future_expiry_has_access():
    assert has_device_access(account(), device(expires=NOW + timedelta(hours=1)), NOW) is True


def test_paid_device_with_past_expiry_has_no_access():
    assert has_device_access(account(), device(expires=NOW - timedelta(minutes=1)), NOW) is False


def test_paid_device_without_plan_has_no_access():
    assert has_device_access(account(), device(expires=None), NOW) is False


def test_disabled_account_has_no_access_even_with_a_plan():
    user = account(active=False)
    assert has_device_access(user, device(expires=NOW + timedelta(days=1)), NOW) is False


def test_plan_on_one_device_does_not_work_on_another():
    # The rule this file exists for: a plan bought for the phone does not cover the laptop.
    user = account()
    phone = device(mac=PHONE, expires=NOW + timedelta(days=2))
    laptop = device(mac=LAPTOP, expires=None)
    assert has_device_access(user, phone, NOW) is True
    assert has_device_access(user, laptop, NOW) is False
    assert any_device_has_access(user, [phone, laptop], NOW) is True


def test_no_device_has_access_when_none_has_a_plan():
    user = account()
    assert any_device_has_access(user, [device(mac=PHONE), device(mac=LAPTOP)], NOW) is False


def test_free_user_has_access_on_each_device_without_expiry():
    user = account(access_type="free", user_type="gated")
    assert has_device_access(user, device(mac=PHONE), NOW) is True
    assert has_device_access(user, device(mac=LAPTOP), NOW) is True
    assert device_seconds_left(user, device(mac=PHONE), NOW) is None


def test_renewal_extends_this_device_from_the_later_of_now_or_its_expiry():
    active = device(expires=NOW + timedelta(hours=10))
    assert extend_device_access(active, 48, NOW) == NOW + timedelta(hours=58)

    expired = device(expires=NOW - timedelta(hours=10))
    assert extend_device_access(expired, 48, NOW) == NOW + timedelta(hours=48)


def test_renewal_on_one_device_leaves_the_other_untouched():
    phone = device(mac=PHONE, expires=NOW + timedelta(hours=1))
    laptop = device(mac=LAPTOP, expires=None)
    extend_device_access(phone, 48, NOW)
    assert laptop.access_expires_at is None


def test_seconds_left_for_paid_device():
    assert device_seconds_left(account(), device(expires=NOW + timedelta(seconds=90)), NOW) == 90
