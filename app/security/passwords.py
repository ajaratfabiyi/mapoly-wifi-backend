from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Checked when the email is unknown, so an unknown account takes as long to
# answer as a real one (Section 5.2, step 4).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if stored_hash is None:
        _check(_DUMMY_HASH, password)
        return False
    return _check(stored_hash, password)


def _check(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False
