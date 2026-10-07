"""Password hashing, session tokens and username rules.

Passwords are hashed with bcrypt, which only looks at the first 72 bytes, so
longer input is rejected rather than silently truncated. Session tokens are
random and opaque; only their SHA-256 digest is ever stored.
"""

import hashlib
import re
import secrets

import bcrypt

PASSWORD_MIN_BYTES = 10
PASSWORD_MAX_BYTES = 72
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")


class PasswordPolicyError(ValueError):
    """The password is outside the accepted length range."""


class UsernameError(ValueError):
    """The username does not match the accepted pattern."""


def hash_password(password: str) -> str:
    try:
        size = len(password.encode("utf-8"))
    except UnicodeEncodeError:  # a lone surrogate, which JSON allows
        raise PasswordPolicyError("password must be valid Unicode text") from None
    if not PASSWORD_MIN_BYTES <= size <= PASSWORD_MAX_BYTES:
        raise PasswordPolicyError(f"password must be {PASSWORD_MIN_BYTES}-{PASSWORD_MAX_BYTES} bytes (UTF-8)")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """True only for a matching password; never raises, whatever the input."""
    if not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > PASSWORD_MAX_BYTES:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except (ValueError, TypeError, AttributeError):
        return False


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    """A fresh session token as `(raw, sha256_hex)`; the raw value goes in the cookie."""
    raw = secrets.token_urlsafe(32)
    return raw, token_hash(raw)


def normalize_username(name: str) -> str:
    """Trim and lowercase `name`; raise `UsernameError` if it is not a valid username."""
    normalized = name.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise UsernameError("username must be 3-32 characters: a-z, 0-9, '_', '.', '-'")
    return normalized
