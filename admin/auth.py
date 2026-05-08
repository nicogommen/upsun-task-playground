import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def verify_password(plain: str) -> bool:
    expected_hash = os.environ.get("ADMIN_PASSWORD_HASH")
    if not expected_hash:
        return False
    try:
        _hasher.verify(expected_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True
