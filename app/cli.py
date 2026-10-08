"""User-management CLI: manage accounts and back up the database.

Registration is closed by default, so this is how accounts get created. Run it
as `python -m app.cli ...` (or `make user NAME=ada`); in Docker, prefix it with
`docker compose exec app`. It reads the same `DATABASE_PATH` setting as the
server and applies any pending migrations first, so it also works on a fresh
install before the server has ever started.
"""

import argparse
import getpass
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from app.auth import PasswordPolicyError, UsernameError, hash_password, normalize_username
from app.database import Database
from app.settings import load_settings

BACKUP_PREFIX = "ai_tutor-"
BACKUP_SUFFIX = ".db"
BACKUP_NAME = re.compile(r"ai_tutor-\d{8}-\d{6}\.db")
DEFAULT_KEEP = 14


class CliError(Exception):
    """A problem the owner can fix; shown as a one-line message with exit code 1."""


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        line = sys.stdin.readline()
        return line.removesuffix("\n").removesuffix("\r")
    try:
        first = getpass.getpass("Password: ")
        again = getpass.getpass("Repeat password: ")
    except EOFError:
        raise CliError("no password given") from None
    if first != again:
        raise CliError("the passwords do not match")
    return first


def _existing_user(db: Database, name: str) -> dict:
    username = _username(name)
    user = db.get_user_by_username(username)
    if user is None:
        raise CliError(f"no user named '{username}'")
    return user


def _username(name: str) -> str:
    try:
        return normalize_username(name)
    except UsernameError as error:
        raise CliError(str(error)) from None


def _password_hash(password: str) -> str:
    try:
        return hash_password(password)
    except PasswordPolicyError as error:
        raise CliError(str(error)) from None


def create_user(db: Database, args: argparse.Namespace) -> None:
    username = _username(args.name)
    if db.get_user_by_username(username):
        raise CliError(f"a user named '{username}' already exists")
    password_hash = _password_hash(_read_password(args.password_stdin))
    try:
        db.create_user(username, password_hash)
    except sqlite3.IntegrityError:  # created by someone else while we were prompting
        raise CliError(f"a user named '{username}' already exists") from None
    print(f"Created user '{username}'.")


def reset_password(db: Database, args: argparse.Namespace) -> None:
    user = _existing_user(db, args.name)
    password_hash = _password_hash(_read_password(args.password_stdin))
    db.set_password(user["id"], password_hash)
    db.delete_user_sessions(user["id"])
    print(f"Password changed for '{user['username']}'. They were signed out everywhere.")


def list_users(db: Database, args: argparse.Namespace) -> None:
    users = db.list_users()
    if not users:
        print("No users yet. Create one with: create-user NAME")
        return
    width = max(len(user["username"]) for user in users)
    for user in users:
        print(f"{user['username']:<{width}}  created {user['created_at']}")


def delete_user(db: Database, args: argparse.Namespace) -> None:
    user = _existing_user(db, args.name)
    if not args.yes:
        raise CliError(
            f"this permanently deletes '{user['username']}' and all their progress; "
            "repeat the command with --yes to confirm"
        )
    db.delete_user_sessions(user["id"])
    db.delete_user(user["id"])
    print(f"Deleted user '{user['username']}' and all their progress.")


def backup(db: Database, args: argparse.Namespace) -> None:
    if not db.path.exists():
        raise CliError(f"no database found at {db.path}")
    out = Path(args.out) if args.out else db.path.parent / "backups"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{BACKUP_PREFIX}{datetime.now():%Y%m%d-%H%M%S}{BACKUP_SUFFIX}"
    partial = target.with_name(target.name + ".partial")  # never mistaken for a finished backup
    source = sqlite3.connect(db.path, timeout=10)
    try:
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)  # consistent snapshot while the server keeps running
        finally:
            destination.close()
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    # The copy holds password hashes and session digests: owner-only, like the database itself.
    os.chmod(partial, 0o600)
    partial.replace(target)
    # Rotate only files this command names; the backup just written is always kept.
    others = sorted((p for p in out.iterdir() if BACKUP_NAME.fullmatch(p.name) and p != target), reverse=True)
    for stale in others[args.keep - 1 :]:
        stale.unlink()
    print(f"Backup written: {target}")


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "User-management CLI: manage AI Tutor accounts and back up the database. "
            "The database is chosen by the DATABASE_PATH environment variable. "
            "In Docker, run: docker compose exec app python -m app.cli COMMAND ..."
        ),
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def add(name: str, handler: Callable[[Database, argparse.Namespace], None], help_text: str):
        command = commands.add_parser(name, help=help_text, description=help_text)
        command.set_defaults(handler=handler)
        return command

    password_help = "read the password from one line of standard input instead of prompting"
    create = add("create-user", create_user, "Create an account (asks for the password twice).")
    create.add_argument("name", help="username: 3-32 characters from a-z, 0-9, '_', '.', '-'")
    create.add_argument("--password-stdin", action="store_true", help=password_help)

    reset = add("reset-password", reset_password, "Set a new password and sign the user out everywhere.")
    reset.add_argument("name", help="username")
    reset.add_argument("--password-stdin", action="store_true", help=password_help)

    add("list-users", list_users, "List accounts (names and creation dates only).")

    delete = add("delete-user", delete_user, "Permanently delete an account and all its progress.")
    delete.add_argument("name", help="username")
    delete.add_argument("--yes", action="store_true", help="confirm the deletion")

    save = add("backup", backup, "Write a consistent copy of the database; safe while the server runs.")
    save.add_argument(
        "--out", metavar="DIR", help="backup folder (default: a 'backups' folder next to the database)"
    )
    save.add_argument(
        "--keep",
        type=_positive,
        default=DEFAULT_KEEP,
        metavar="N",
        help=f"keep only the newest N backups in that folder (default: {DEFAULT_KEEP})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db = Database(load_settings().database_path)
        if args.command != "backup":
            db.migrate()
        args.handler(db, args)
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
