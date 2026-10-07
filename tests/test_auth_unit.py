"""Password, session-token and username primitives."""

import hashlib

import pytest

from app import auth


def test_password_below_minimum_is_rejected():
    with pytest.raises(auth.PasswordPolicyError):
        auth.hash_password("a" * 9)


def test_password_at_minimum_is_accepted():
    hashed = auth.hash_password("a" * 10)
    assert auth.verify_password("a" * 10, hashed)


def test_password_at_bcrypt_limit_is_accepted():
    password = "a" * 72
    hashed = auth.hash_password(password)
    assert auth.verify_password(password, hashed)


def test_password_above_bcrypt_limit_is_rejected():
    with pytest.raises(auth.PasswordPolicyError):
        auth.hash_password("a" * 73)


def test_password_limit_counts_utf8_bytes_not_characters():
    # 36 two-byte characters are exactly 72 bytes; one more crosses the limit.
    assert auth.verify_password("é" * 36, auth.hash_password("é" * 36))
    with pytest.raises(auth.PasswordPolicyError):
        auth.hash_password("é" * 36 + "a")
    with pytest.raises(auth.PasswordPolicyError):
        auth.hash_password("é" * 37)


def test_short_multibyte_password_counts_bytes_for_the_minimum():
    # Five two-byte characters are 10 bytes: long enough.
    assert auth.verify_password("é" * 5, auth.hash_password("é" * 5))


def test_verify_wrong_password_is_false():
    assert not auth.verify_password("wrong-password", auth.hash_password("right-password"))


def test_verify_overlong_password_is_false():
    hashed = auth.hash_password("a" * 72)
    assert auth.verify_password("a" * 73, hashed) is False


def test_verify_never_raises_on_a_malformed_hash():
    assert auth.verify_password("whatever-password", "not-a-bcrypt-hash") is False
    assert auth.verify_password("whatever-password", "") is False


def test_session_token_is_stored_as_its_sha256():
    raw, digest = auth.new_session_token()
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert auth.token_hash(raw) == digest
    assert raw != digest


def test_session_tokens_are_unique_and_long_enough():
    first, _ = auth.new_session_token()
    second, _ = auth.new_session_token()
    assert first != second
    assert len(first) >= 43  # token_urlsafe(32)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Alice  ", "alice"), ("Bob_Smith", "bob_smith"), ("a.b-c", "a.b-c"), ("abc", "abc")],
)
def test_normalize_username_lowercases_and_strips(raw, expected):
    assert auth.normalize_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["ab", "a" * 33, "has space", "emoji😀name", "slash/name", "", "   ", "semi;colon", "ünï"],
)
def test_normalize_username_rejects_invalid_names(raw):
    with pytest.raises(auth.UsernameError):
        auth.normalize_username(raw)


def test_normalize_username_accepts_boundary_lengths():
    assert auth.normalize_username("abc") == "abc"
    assert auth.normalize_username("a" * 32) == "a" * 32
