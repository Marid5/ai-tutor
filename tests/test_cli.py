"""The admin CLI, exercised the way an owner runs it: as a subprocess with env settings."""

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.auth import new_session_token, verify_password
from app.database import Database

ROOT = Path(__file__).parent.parent
GOOD_PASSWORD = "correct horse battery"


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_PATH", "CONTENT_DIR"}}
    env["DATABASE_PATH"] = str(tmp_path / "data" / "ai_tutor.db")
    env["CONTENT_DIR"] = str(tmp_path / "no-content-here")
    return env


def run_cli(env: dict[str, str], *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )


def open_db(env: dict[str, str]) -> Database:
    return Database(Path(env["DATABASE_PATH"]))


def sign_in_user(db: Database, user_id: str) -> str:
    _, digest = new_session_token()
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    db.create_auth_session(digest, user_id, expires)
    return digest


def test_create_user_with_stdin_password(cli_env):
    result = run_cli(cli_env, "create-user", "Ada", "--password-stdin", stdin=GOOD_PASSWORD + "\n")

    assert result.returncode == 0, result.stderr
    assert "ada" in result.stdout
    user = open_db(cli_env).get_user_by_username("ada")
    assert user is not None
    assert verify_password(GOOD_PASSWORD, user["password_hash"])
    assert GOOD_PASSWORD not in result.stdout + result.stderr
    assert user["password_hash"] not in result.stdout + result.stderr


def test_create_duplicate_user_fails(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)

    result = run_cli(cli_env, "create-user", "ADA", "--password-stdin", stdin=GOOD_PASSWORD)

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert len(open_db(cli_env).list_users()) == 1


def test_password_policy_enforced(cli_env):
    short = run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin="tooshort\n")
    long = run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin="x" * 73 + "\n")
    bad_name = run_cli(cli_env, "create-user", "a!", "--password-stdin", stdin=GOOD_PASSWORD)

    for result in (short, long, bad_name):
        assert result.returncode == 1
        assert result.stderr.strip()
        assert "Traceback" not in result.stderr
    assert "password" in short.stderr
    assert "username" in bad_name.stderr
    assert open_db(cli_env).list_users() == []


def test_reset_password_revokes_sessions(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)
    run_cli(cli_env, "create-user", "grace", "--password-stdin", stdin=GOOD_PASSWORD)
    db = open_db(cli_env)
    ada = db.get_user_by_username("ada")
    grace = db.get_user_by_username("grace")
    ada_session = sign_in_user(db, ada["id"])
    grace_session = sign_in_user(db, grace["id"])
    now = datetime.now(UTC).isoformat()

    result = run_cli(cli_env, "reset-password", "ada", "--password-stdin", stdin="a brand new secret\n")

    assert result.returncode == 0, result.stderr
    assert verify_password("a brand new secret", db.get_user_by_username("ada")["password_hash"])
    assert db.auth_session_user(ada_session, now) is None
    assert db.auth_session_user(grace_session, now) == grace["id"]


def test_reset_password_unknown_user_fails(cli_env):
    result = run_cli(cli_env, "reset-password", "nobody", "--password-stdin", stdin=GOOD_PASSWORD)

    assert result.returncode == 1
    assert "no user" in result.stderr


def test_list_users_shows_names_not_hashes(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)
    run_cli(cli_env, "create-user", "grace", "--password-stdin", stdin=GOOD_PASSWORD)
    hashes = [open_db(cli_env).get_user_by_username(n)["password_hash"] for n in ("ada", "grace")]

    result = run_cli(cli_env, "list-users")

    assert result.returncode == 0
    assert "ada" in result.stdout and "grace" in result.stdout
    assert not any(h in result.stdout or "$2b$" in result.stdout for h in hashes)


def test_list_users_on_fresh_install_is_friendly(cli_env):
    result = run_cli(cli_env, "list-users")

    assert result.returncode == 0
    assert "No users" in result.stdout


def test_delete_user_requires_yes(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)

    result = run_cli(cli_env, "delete-user", "ada")

    assert result.returncode == 1
    assert "--yes" in result.stderr
    assert open_db(cli_env).get_user_by_username("ada") is not None


def test_delete_user_revokes_and_removes(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)
    db = open_db(cli_env)
    user = db.get_user_by_username("ada")
    session = sign_in_user(db, user["id"])

    result = run_cli(cli_env, "delete-user", "ada", "--yes")

    assert result.returncode == 0, result.stderr
    assert db.get_user_by_username("ada") is None
    assert db.auth_session_user(session, datetime.now(UTC).isoformat()) is None


def test_delete_unknown_user_fails(cli_env):
    result = run_cli(cli_env, "delete-user", "nobody", "--yes")

    assert result.returncode == 1
    assert "no user" in result.stderr


def test_backup_creates_consistent_copy_and_rotates(cli_env, tmp_path):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)
    out = tmp_path / "backups"
    out.mkdir()
    for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
        (out / f"ai_tutor-{stamp}.db").write_bytes(b"old")
    (out / "notes.txt").write_text("not a backup")

    result = run_cli(cli_env, "backup", "--out", str(out), "--keep", "2")

    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in out.glob("ai_tutor-*.db"))
    assert len(names) == 2
    assert names[0] == "ai_tutor-20200103-000000.db"
    assert (out / "notes.txt").exists()
    written = Path(result.stdout.strip().splitlines()[-1].split(": ", 1)[-1])
    assert written.parent == out and written.name == names[1]
    copy = sqlite3.connect(written)
    try:
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert copy.execute("SELECT username FROM users").fetchall() == [("ada",)]
    finally:
        copy.close()
    assert not list(out.glob("*.partial"))


def test_backup_defaults_next_to_the_database(cli_env):
    run_cli(cli_env, "create-user", "ada", "--password-stdin", stdin=GOOD_PASSWORD)

    result = run_cli(cli_env, "backup")

    assert result.returncode == 0, result.stderr
    default_dir = Path(cli_env["DATABASE_PATH"]).parent / "backups"
    assert len(list(default_dir.glob("ai_tutor-*.db"))) == 1


def test_backup_without_a_database_fails_cleanly(cli_env):
    result = run_cli(cli_env, "backup")

    assert result.returncode == 1
    assert "database" in result.stderr


def test_usage_errors_exit_2(cli_env):
    assert run_cli(cli_env).returncode == 2
    assert run_cli(cli_env, "frobnicate").returncode == 2
    assert run_cli(cli_env, "backup", "--keep", "0").returncode == 2


def test_help_documents_commands(cli_env):
    result = run_cli(cli_env, "--help")

    assert result.returncode == 0
    for command in ("create-user", "reset-password", "list-users", "delete-user", "backup"):
        assert command in result.stdout


def test_interactive_password_is_asked_twice(cli_env, monkeypatch, capsys):
    from app import cli

    answers = iter([GOOD_PASSWORD, GOOD_PASSWORD])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setenv("DATABASE_PATH", cli_env["DATABASE_PATH"])

    assert cli.main(["create-user", "ada"]) == 0

    assert verify_password(GOOD_PASSWORD, open_db(cli_env).get_user_by_username("ada")["password_hash"])
    assert GOOD_PASSWORD not in capsys.readouterr().out


def test_interactive_password_mismatch_creates_nothing(cli_env, monkeypatch, capsys):
    from app import cli

    answers = iter([GOOD_PASSWORD, "something else entirely"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setenv("DATABASE_PATH", cli_env["DATABASE_PATH"])

    assert cli.main(["create-user", "ada"]) == 1

    assert "do not match" in capsys.readouterr().err
    assert open_db(cli_env).list_users() == []
