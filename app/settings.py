"""Runtime configuration, read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    database_path: Path
    content_dir: Path
    static_dir: Path
    registration_open: bool
    cookie_secure: bool
    trust_proxy: bool
    git_sha: str
    register_limit_per_hour: int
    login_ip_limit: int
    login_user_fail_limit: int
    session_days: int = 30


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of 1/true/yes/on or 0/false/no/off, got {value!r}")


def _number(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}") from None
    if number < 1:
        raise ValueError(f"{name} must be at least 1, got {number}")
    return number


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        database_path=Path(env.get("DATABASE_PATH") or ROOT / "data" / "ai_tutor.db"),
        content_dir=Path(env.get("CONTENT_DIR") or ROOT / "content"),
        static_dir=Path(env.get("STATIC_DIR") or ROOT / "static"),
        registration_open=env.get("REGISTRATION") == "open",
        cookie_secure=_flag(env, "COOKIE_SECURE", True),
        trust_proxy=_flag(env, "TRUST_PROXY", False),
        git_sha=env.get("GIT_SHA") or "dev",
        register_limit_per_hour=_number(env, "REGISTER_LIMIT_PER_HOUR", 5),
        login_ip_limit=_number(env, "LOGIN_IP_LIMIT", 20),
        login_user_fail_limit=_number(env, "LOGIN_USER_FAIL_LIMIT", 10),
    )
