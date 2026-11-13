from app.security.sessions import MAX_AGE_SECONDS, create_session_value, read_session_value

NOW = 1_800_000_000


def test_round_trip():
    value = create_session_value(42, now=NOW)
    assert read_session_value(value, now=NOW + 10) == 42


def test_tampered_user_id_is_rejected():
    value = create_session_value(42, now=NOW)
    _, issued, sig = value.split(".")
    assert read_session_value(f"43.{issued}.{sig}", now=NOW) is None


def test_expired_session_is_rejected():
    value = create_session_value(42, now=NOW)
    assert read_session_value(value, now=NOW + MAX_AGE_SECONDS + 1) is None


def test_missing_or_malformed_is_rejected():
    assert read_session_value(None) is None
    assert read_session_value("") is None
    assert read_session_value("not.a.valid.cookie") is None
