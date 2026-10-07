"""Accounts, stored sign-in sessions and the persistent rate-limit log."""

import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.database import Database

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def test_create_and_find_user(db: Database):
    user_id = db.create_user("ada", "bcrypt-hash")

    assert db.get_user(user_id)["username"] == "ada"
    assert db.get_user_by_username("ada")["id"] == user_id
    assert db.get_user_by_username("ada")["password_hash"] == "bcrypt-hash"
    assert db.get_user_by_username("nobody") is None
    assert db.get_user("missing") is None


def test_username_is_unique(db: Database):
    db.create_user("ada", "hash")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("ada", "other")


def test_list_users_has_no_password_hash(db: Database):
    first = db.create_user("ada", "secret-1")
    second = db.create_user("grace", "secret-2")

    users = db.list_users()

    assert [user["id"] for user in users] == [first, second]
    assert [user["username"] for user in users] == ["ada", "grace"]
    assert all(set(user) == {"id", "username", "created_at"} for user in users)


def test_set_password(db: Database):
    user_id = db.create_user("ada", "old")
    db.set_password(user_id, "new")
    assert db.get_user(user_id)["password_hash"] == "new"


def test_session_stored_only_as_hash(db: Database, tmp_path):
    user_id = db.create_user("ada", "hash")
    token = secrets.token_urlsafe(32)
    db.create_auth_session(token_hash(token), user_id, iso(timedelta(days=30)))

    assert db.auth_session_user(token_hash(token), iso(timedelta())) == user_id
    # Presenting the raw token (instead of its hash) finds nothing.
    assert db.auth_session_user(token, iso(timedelta())) is None
    assert db.scalar("SELECT token_hash FROM auth_sessions") == token_hash(token)
    # The raw token appears nowhere in the database file, WAL included.
    raw = b"".join(path.read_bytes() for path in tmp_path.glob("test.db*"))
    assert token.encode() not in raw


def test_expired_session_returns_none(db: Database):
    user_id = db.create_user("ada", "hash")
    db.create_auth_session("live", user_id, iso(timedelta(days=1)))
    db.create_auth_session("expired", user_id, iso(timedelta(seconds=-1)))
    db.create_auth_session("exact", user_id, iso(timedelta()))

    assert db.auth_session_user("live", iso(timedelta())) == user_id
    assert db.auth_session_user("expired", iso(timedelta())) is None
    # A session expires at its expiry instant, not a moment after.
    assert db.auth_session_user("exact", iso(timedelta())) is None
    assert db.auth_session_user("unknown", iso(timedelta())) is None


def test_validating_a_session_does_not_extend_it(db: Database):
    user_id = db.create_user("ada", "hash")
    expires = iso(timedelta(days=1))
    db.create_auth_session("live", user_id, expires)

    db.auth_session_user("live", iso(timedelta()))

    assert db.scalar("SELECT expires_at FROM auth_sessions WHERE token_hash='live'") == expires


def test_delete_auth_session(db: Database):
    user_id = db.create_user("ada", "hash")
    db.create_auth_session("one", user_id, iso(timedelta(days=1)))
    db.delete_auth_session("one")
    assert db.auth_session_user("one", iso(timedelta())) is None


def test_purge_expired_sessions(db: Database):
    user_id = db.create_user("ada", "hash")
    db.create_auth_session("old-1", user_id, iso(timedelta(days=-2)))
    db.create_auth_session("old-2", user_id, iso(timedelta(seconds=-1)))
    db.create_auth_session("live", user_id, iso(timedelta(days=5)))

    assert db.purge_expired_sessions(iso(timedelta())) == 2

    assert db.scalar("SELECT count(*) FROM auth_sessions") == 1
    assert db.auth_session_user("live", iso(timedelta())) == user_id


def test_delete_user_sessions_except_current(db: Database):
    ada = db.create_user("ada", "hash")
    grace = db.create_user("grace", "hash")
    for name in ("a1", "a2", "a3"):
        db.create_auth_session(name, ada, iso(timedelta(days=1)))
    db.create_auth_session("g1", grace, iso(timedelta(days=1)))

    db.delete_user_sessions(ada, except_hash="a2")

    remaining = {row["token_hash"] for row in db.fetch_cards("SELECT token_hash FROM auth_sessions")}
    assert remaining == {"a2", "g1"}

    db.delete_user_sessions(ada)
    remaining = {row["token_hash"] for row in db.fetch_cards("SELECT token_hash FROM auth_sessions")}
    assert remaining == {"g1"}


def test_rate_limit_hits_persist_across_connections(db: Database):
    db.record_hit("login:ip:10.0.0.1", iso(timedelta(minutes=-20)))
    db.record_hit("login:ip:10.0.0.1", iso(timedelta(minutes=-5)))
    db.record_hit("login:ip:10.0.0.1", iso(timedelta(minutes=-1)))
    db.record_hit("login:user:ada", iso(timedelta(minutes=-1)))

    reopened = Database(db.path)

    assert reopened.count_hits("login:ip:10.0.0.1", iso(timedelta(minutes=-15))) == 2
    assert reopened.count_hits("login:ip:10.0.0.1", iso(timedelta(hours=-1))) == 3
    assert reopened.count_hits("login:user:ada", iso(timedelta(minutes=-15))) == 1
    assert reopened.count_hits("login:user:nobody", iso(timedelta(hours=-1))) == 0


def test_purge_hits(db: Database):
    db.record_hit("b", iso(timedelta(hours=-3)))
    db.record_hit("b", iso(timedelta(hours=-2)))
    db.record_hit("b", iso(timedelta(minutes=-1)))

    assert db.purge_hits(iso(timedelta(hours=-1))) == 2

    assert db.count_hits("b", iso(timedelta(days=-1))) == 1


def test_delete_user_cascades(seeded_db: Database):
    gone = seeded_db.create_user("gone", "hash")
    kept = seeded_db.create_user("kept", "hash")
    for user in (gone, kept):
        seeded_db.create_auth_session(f"session-{user}", user, iso(timedelta(days=1)))
        seeded_db.get_state(user, "capital-of-france")
        seeded_db.start_lesson(user, "first")
        seeded_db.set_user_meta(user, "show_hint_by_default", "1")
        seeded_db.create_review_session(f"review-{user}", user, "2026-10-07", ["capital-of-france"])
        seeded_db.record_event(
            user,
            {
                "id": f"event-{user}",
                "ts": iso(timedelta()),
                "session_id": "first",
                "card_id": "capital-of-france",
                "kind": "choice",
                "answer": "Paris",
                "elapsed_ms": 10,
                "check_version": "h",
            },
        )

    seeded_db.delete_user(gone)

    for table in (
        "auth_sessions",
        "card_state",
        "user_lesson_state",
        "user_meta",
        "study_sessions",
        "events",
    ):
        assert seeded_db.scalar(f"SELECT count(*) FROM {table} WHERE user_id=?", (gone,)) == 0, table
        assert seeded_db.scalar(f"SELECT count(*) FROM {table} WHERE user_id=?", (kept,)) == 1, table
    assert seeded_db.get_user(gone) is None
    assert seeded_db.get_user(kept) is not None
    # Course content is shared and survives.
    assert seeded_db.scalar("SELECT count(*) FROM cards") == 4


def test_connections_enforce_foreign_keys_and_use_wal(db: Database):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_auth_session("orphan", "no-such-user", iso(timedelta(days=1)))
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
