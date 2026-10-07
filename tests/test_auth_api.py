"""Sign-in, accounts and rate limits over HTTP."""

from __future__ import annotations

from app.auth import token_hash
from tests.helpers import PASSWORD, api_db, api_user_id, sign_in

COOKIE = "ai_tutor_session"


def _login(client, username="learner", password=PASSWORD):
    return client.post("/api/login", json={"username": username, "password": password})


def _set_cookie_header(response) -> str:
    headers = [value for key, value in response.headers.multi_items() if key == "set-cookie"]
    assert len(headers) == 1, headers
    return headers[0]


def test_login_sets_httponly_samesite_cookie(client):
    sign_in(client)
    response = _login(client)
    assert response.status_code == 200
    assert response.json() == {"username": "learner"}
    header = _set_cookie_header(response).lower()
    assert header.startswith(f"{COOKIE}=")
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "max-age=2592000" in header
    assert "path=/" in header
    assert "secure" not in header
    raw = response.cookies[COOKIE]
    stored = api_db(client).scalar(
        "SELECT token_hash FROM auth_sessions WHERE token_hash=?", (token_hash(raw),)
    )
    assert stored == token_hash(raw), "only the hash of the token is stored"
    assert api_db(client).scalar("SELECT count(*) FROM auth_sessions WHERE token_hash=?", (raw,)) == 0


def test_cookie_secure_flag_follows_setting(make_client):
    client = sign_in(make_client(cookie_secure=True), "learner")
    response = _login(client)
    assert "secure" in _set_cookie_header(response).lower()


def test_logout_deletes_session(signed_in):
    raw = signed_in.cookies[COOKIE]
    assert signed_in.get("/api/account").status_code == 200
    response = signed_in.post("/api/logout")
    assert response.status_code == 204
    assert f"{COOKIE}=" in _set_cookie_header(response)
    assert api_db(signed_in).scalar("SELECT count(*) FROM auth_sessions") == 0
    # The old token is dead even if a copy of the cookie is replayed.
    signed_in.cookies.set(COOKIE, raw)
    assert signed_in.get("/api/account").status_code == 401


def test_password_change_revokes_other_sessions(make_client):
    first = sign_in(make_client())
    second = make_client()
    assert _login(second).status_code == 200
    assert second.get("/api/account").status_code == 200

    new_password = "a brand new passphrase"
    response = first.post("/api/password", json={"current": PASSWORD, "new": new_password})
    assert response.status_code == 204
    assert first.get("/api/account").status_code == 200, "the session making the change stays"
    assert second.get("/api/account").status_code == 401, "every other session is signed out"
    assert _login(second).status_code == 401
    assert _login(second, password=new_password).status_code == 200


def test_password_change_with_wrong_current_password_is_refused(signed_in):
    response = signed_in.post(
        "/api/password", json={"current": "not the password", "new": "another passphrase"}
    )
    assert response.status_code == 403
    assert _login(signed_in).status_code == 200


def test_password_policy_errors_are_422(signed_in):
    for new in ("short", "x" * 73, "é" * 37):
        response = signed_in.post("/api/password", json={"current": PASSWORD, "new": new})
        assert response.status_code == 422, new
        assert isinstance(response.json()["detail"], str)
    assert _login(signed_in).status_code == 200, "the password did not change"


def test_login_overlong_password_is_401(signed_in):
    response = _login(signed_in, password=PASSWORD + "x" * 100)
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid username or password"}


def test_unknown_user_and_wrong_password_look_the_same(client):
    sign_in(client)
    unknown = _login(client, "nobody")
    wrong = _login(client, password="wrong password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_registration_closed_returns_403(client):
    response = client.post("/api/register", json={"username": "newbie", "password": PASSWORD})
    assert response.status_code == 403
    assert response.json() == {"detail": "registration is closed"}
    assert api_db(client).get_user_by_username("newbie") is None


def test_registration_open_logs_in(make_client):
    client = make_client(registration_open=True)
    response = client.post("/api/register", json={"username": "  NewBie ", "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json() == {"username": "newbie"}
    assert client.get("/api/account").json() == {"username": "newbie"}
    taken = client.post("/api/register", json={"username": "newbie", "password": PASSWORD})
    assert taken.status_code == 409
    bad_name = client.post("/api/register", json={"username": "no spaces", "password": PASSWORD})
    assert bad_name.status_code == 422
    bad_password = client.post("/api/register", json={"username": "another", "password": "short"})
    assert bad_password.status_code == 422


def test_registration_is_rate_limited_per_ip(make_client):
    client = make_client(registration_open=True, register_limit_per_hour=2)
    for name in ("first-user", "second-user"):
        assert client.post("/api/register", json={"username": name, "password": PASSWORD}).status_code == 200
    blocked = client.post("/api/register", json={"username": "third-user", "password": PASSWORD})
    assert blocked.status_code == 429


def test_login_rate_limited_by_ip(make_client):
    client = sign_in(make_client(login_ip_limit=3))
    # The sign-in above was the first attempt from this address.
    assert _login(client).status_code == 200
    assert _login(client, password="wrong password").status_code == 401
    blocked = _login(client)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "too many attempts, try again later"}
    other_ip = make_client(peer=("8.8.4.4", 1234), login_ip_limit=3)
    assert _login(other_ip).status_code == 200


def test_failed_logins_limit_username_across_ips(make_client):
    sign_in(make_client(login_user_fail_limit=2), "victim")
    for address in ("8.8.8.1", "8.8.8.2"):
        client = make_client(peer=(address, 1), login_user_fail_limit=2)
        assert _login(client, "victim", "wrong password").status_code == 401
    third = make_client(peer=("8.8.8.3", 1), login_user_fail_limit=2)
    assert _login(third, "victim").status_code == 429, "even the right password waits out the window"
    assert _login(third, "victim", "wrong password").status_code == 429
    sign_in(third, "bystander")


def test_successful_logins_do_not_count_towards_username_limit(make_client):
    client = sign_in(make_client(login_user_fail_limit=2))
    for _ in range(4):
        assert _login(client).status_code == 200
    assert _login(client, password="wrong password").status_code == 401
    assert _login(client).status_code == 200


def test_rate_limit_survives_app_restart(make_client):
    first = sign_in(make_client(login_user_fail_limit=2))
    for _ in range(2):
        assert _login(first, password="wrong password").status_code == 401
    restarted = make_client(login_user_fail_limit=2)
    assert _login(restarted).status_code == 429


def test_trust_proxy_uses_rightmost_xff_from_private_peer(make_client):
    proxied = make_client(peer=("172.17.0.1", 5000), trust_proxy=True, login_ip_limit=1)
    sign_in(proxied)  # no X-Forwarded-For: counted against the proxy's own address

    def attempt(client, xff):
        return client.post(
            "/api/login",
            json={"username": "learner", "password": PASSWORD},
            headers=[("X-Forwarded-For", value) for value in xff],
        )

    # Two header lines are joined; the right-most entry (appended by our proxy) wins.
    assert attempt(proxied, ["1.1.1.1", "9.9.9.9, 8.8.8.8"]).status_code == 200
    assert attempt(proxied, ["7.7.7.7, 8.8.8.8"]).status_code == 429, "same client behind the proxy"
    assert attempt(proxied, ["8.8.8.8, 9.9.9.9"]).status_code == 200, "a different client"

    direct = make_client(peer=("8.8.4.4", 5000), trust_proxy=True, login_ip_limit=1)
    assert attempt(direct, ["1.2.3.4"]).status_code == 200
    assert attempt(direct, ["5.6.7.8"]).status_code == 429, "a public peer cannot choose its identity"


def test_account_shape(signed_in):
    assert signed_in.get("/api/account").json() == {"username": "learner"}


def test_protected_endpoints_need_a_session(client):
    for method, path in (
        ("get", "/api/account"),
        ("get", "/api/chapters"),
        ("get", "/api/settings"),
        ("get", "/api/progress"),
        ("get", "/api/session"),
        ("post", "/api/review/start"),
        ("post", "/api/practice/start"),
        ("post", "/api/lessons/first/start"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, path
        assert response.json() == {"detail": "login required"}
    client.cookies.set(COOKIE, "forged-token")
    assert client.get("/api/account").status_code == 401


def test_expired_sessions_are_purged_at_login(signed_in):
    with api_db(signed_in)._transaction() as conn:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            ("old", api_user_id(signed_in), "2020-01-01T00:00:00+00:00", "2020-01-31T00:00:00+00:00"),
        )
    assert _login(signed_in).status_code == 200
    assert api_db(signed_in).scalar("SELECT count(*) FROM auth_sessions WHERE token_hash='old'") == 0
