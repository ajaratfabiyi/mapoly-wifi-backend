"""Repeated login reuses the live token; the device cache stays best-effort; the hash
scheme setting accepts only what is implemented."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import redis
from pydantic import ValidationError

from app.config import Settings
from app.models import LoginToken
from app.services.device_cache import cache_device_added, cache_device_removed, devices_key
from app.services.login_tokens import find_reusable_token

USER = "a@mapoly.edu.ng|A4-C3-F0-88-91-2E"
NOW = datetime(2026, 11, 12, 12, 0, 0)


def _db_with(rows):
    result = MagicMock()
    result.all.return_value = rows
    db = MagicMock()
    db.scalars.return_value = result
    return db


def _radius_row_present(present: bool):
    radius = MagicMock()
    radius.execute.return_value.first.return_value = ("x",) if present else None
    return radius


def _row(token):
    return LoginToken(token=token, username=USER, expires_at=NOW, created_at=NOW)


def test_live_unused_token_is_reused():
    r = MagicMock()
    r.get.return_value = USER
    r.ttl.return_value = 42
    result = find_reusable_token(_db_with([_row("tok")]), _radius_row_present(True), r, USER, NOW)
    assert result == ("tok", 42)


def test_token_already_used_on_router_is_not_reused():
    # FreeRADIUS deleted the Cleartext-Password row, so a reused token would fail.
    r = MagicMock()
    r.get.return_value = USER
    r.ttl.return_value = 42
    result = find_reusable_token(_db_with([_row("tok")]), _radius_row_present(False), r, USER, NOW)
    assert result is None


def test_token_whose_redis_key_expired_is_not_reused():
    r = MagicMock()
    r.get.return_value = None
    result = find_reusable_token(_db_with([_row("tok")]), _radius_row_present(True), r, USER, NOW)
    assert result is None


def test_no_tracked_tokens_means_no_reuse():
    r = MagicMock()
    assert find_reusable_token(_db_with([]), _radius_row_present(True), r, USER, NOW) is None
    r.get.assert_not_called()


def test_device_cache_add_and_remove_use_the_account_key():
    r = MagicMock()
    cache_device_added(r, "a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")
    r.sadd.assert_called_once_with("wifi:devices:a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")
    cache_device_removed(r, "a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")
    r.srem.assert_called_once_with("wifi:devices:a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")
    assert devices_key("a@mapoly.edu.ng") == "wifi:devices:a@mapoly.edu.ng"


def test_device_cache_failure_does_not_raise():
    r = MagicMock()
    r.sadd.side_effect = redis.RedisError("down")
    r.srem.side_effect = redis.RedisError("down")
    cache_device_added(r, "a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")
    cache_device_removed(r, "a@mapoly.edu.ng", "A4:C3:F0:88:91:2E")


@pytest.mark.parametrize("scheme", ["bcrypt", "md5", ""])
def test_unimplemented_hash_scheme_is_rejected_at_startup(scheme):
    with pytest.raises(ValidationError):
        Settings(
            DATABASE_URL="mysql+pymysql://x@h/d",
            RADIUS_DATABASE_URL="mysql+pymysql://x@h/r",
            REDIS_URL="redis://h:6379/0",
            SECRET_KEY="k" * 64,
            PAYSTACK_SECRET_KEY="sk_test_x",
            FRONTEND_ORIGIN="http://localhost",
            PASSWORD_HASH_SCHEME=scheme,
            _env_file=None,
        )


def test_argon2_is_accepted():
    s = Settings(
        DATABASE_URL="mysql+pymysql://x@h/d",
        RADIUS_DATABASE_URL="mysql+pymysql://x@h/r",
        REDIS_URL="redis://h:6379/0",
        SECRET_KEY="k" * 64,
        PAYSTACK_SECRET_KEY="sk_test_x",
        FRONTEND_ORIGIN="http://localhost",
        _env_file=None,
    )
    assert s.PASSWORD_HASH_SCHEME == "argon2"
