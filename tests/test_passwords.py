from app.security.passwords import hash_password, verify_password


def test_correct_password_verifies():
    stored = hash_password("secret123")
    assert verify_password(stored, "secret123") is True


def test_wrong_password_fails():
    stored = hash_password("secret123")
    assert verify_password(stored, "wrong-password") is False


def test_unknown_account_always_false():
    assert verify_password(None, "anything-at-all") is False


def test_stored_value_is_not_plaintext():
    assert "secret123" not in hash_password("secret123")
