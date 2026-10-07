from pathlib import Path

import pytest

from app.settings import ROOT, load_settings


def test_defaults():
    s = load_settings({})
    assert s.registration_open is False
    assert s.cookie_secure is True
    assert s.trust_proxy is False
    assert s.register_limit_per_hour == 5
    assert s.login_ip_limit == 20
    assert s.login_user_fail_limit == 10
    assert s.session_days == 30
    assert s.git_sha == "dev"
    assert s.database_path.name == "ai_tutor.db"
    assert s.database_path == ROOT / "data" / "ai_tutor.db"
    assert s.content_dir == ROOT / "content"
    assert s.static_dir == ROOT / "static"


def test_env_overrides():
    s = load_settings(
        {
            "REGISTRATION": "open",
            "COOKIE_SECURE": "false",
            "TRUST_PROXY": "true",
            "REGISTER_LIMIT_PER_HOUR": "7",
            "LOGIN_IP_LIMIT": "30",
            "LOGIN_USER_FAIL_LIMIT": "3",
            "GIT_SHA": "abc1234",
            "DATABASE_PATH": "/tmp/x/test.db",
            "CONTENT_DIR": "/tmp/x/content",
            "STATIC_DIR": "/tmp/x/static",
        }
    )
    assert s.registration_open is True
    assert s.cookie_secure is False
    assert s.trust_proxy is True
    assert s.register_limit_per_hour == 7
    assert s.login_ip_limit == 30
    assert s.login_user_fail_limit == 3
    assert s.git_sha == "abc1234"
    assert s.database_path == Path("/tmp/x/test.db")
    assert s.content_dir == Path("/tmp/x/content")
    assert s.static_dir == Path("/tmp/x/static")


def test_registration_anything_but_open_is_closed():
    assert load_settings({"REGISTRATION": "closed"}).registration_open is False
    assert load_settings({"REGISTRATION": ""}).registration_open is False


@pytest.mark.parametrize("var", ["REGISTER_LIMIT_PER_HOUR", "LOGIN_IP_LIMIT", "LOGIN_USER_FAIL_LIMIT"])
def test_invalid_number_names_the_variable(var):
    with pytest.raises(ValueError, match=var):
        load_settings({var: "lots"})


def test_defaults_to_process_environment(monkeypatch):
    monkeypatch.setenv("REGISTRATION", "open")
    assert load_settings().registration_open is True
