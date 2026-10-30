"""Portal passwords (AUTH_MODE=password): hashing, checking and generating.

Passwords are stored only as a salted scrypt hash (Python standard library), never in a readable form,
so not even HR or a database admin can see them. HR gives each manager a generated password; the
manager can change it later.
"""

import hashlib
import hmac
import secrets

MIN_LENGTH = 8
_N, _R, _P = 2**14, 8, 1  # scrypt cost: about 50 ms per check
# No look-alike characters (0/O, 1/l/I), so a password read out over the phone is typed correctly.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Same work as a real check, so the response time does not reveal whether the account exists.
        hashlib.scrypt(password.encode(), salt=b"0" * 16, n=_N, r=_R, p=_P)
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


def generate_password() -> str:
    """Like 'Kp7m-Rx4q-Zt9w': 12 random characters in three groups, easy to read and type."""
    chars = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return "-".join(chars[i:i + 4] for i in range(0, 12, 4))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_LENGTH:
        return f"The password must have at least {MIN_LENGTH} characters."
    if len(password) > 200:
        return "The password is too long."
    return None
