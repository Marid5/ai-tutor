"""Client IP resolution, persistent rate limiting and security headers."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.database import Database
from app.security import SECURITY_HEADERS, RateLimiter, SecurityHeadersMiddleware, client_ip

MIGRATIONS = Path(__file__).parent.parent / "migrations"


# --------------------------------------------------------------- client_ip
def test_client_ip_without_proxy_trust_ignores_forwarded_header():
    assert client_ip("127.0.0.1", "1.1.1.1, 2.2.2.2", trust_proxy=False) == "127.0.0.1"


def test_client_ip_trusted_loopback_peer_uses_rightmost_forwarded_entry():
    assert client_ip("127.0.0.1", "1.1.1.1, 2.2.2.2", trust_proxy=True) == "2.2.2.2"


@pytest.mark.parametrize("peer", ["::1", "10.0.0.5", "172.18.0.3", "192.168.1.9"])
def test_client_ip_trusts_loopback_and_private_peers(peer):
    assert client_ip(peer, "203.0.113.7", trust_proxy=True) == "203.0.113.7"


def test_client_ip_public_peer_cannot_spoof_forwarded_header():
    assert client_ip("8.8.8.8", "1.1.1.1", trust_proxy=True) == "8.8.8.8"


def test_client_ip_non_ip_peer_is_returned_as_is():
    assert client_ip("testclient", "1.1.1.1", trust_proxy=True) == "testclient"


def test_client_ip_missing_header_falls_back_to_peer():
    assert client_ip("127.0.0.1", None, trust_proxy=True) == "127.0.0.1"
    assert client_ip("127.0.0.1", "  ", trust_proxy=True) == "127.0.0.1"


def test_client_ip_ignores_a_non_ip_rightmost_entry():
    assert client_ip("127.0.0.1", "1.1.1.1, garbage", trust_proxy=True) == "127.0.0.1"


def test_client_ip_missing_peer_is_unknown():
    assert client_ip(None, "1.1.1.1", trust_proxy=True) == "unknown"
    assert client_ip(None, None, trust_proxy=False) == "unknown"


def test_client_ip_strips_whitespace_around_the_entry():
    assert client_ip("127.0.0.1", "1.1.1.1 ,  2.2.2.2  ", trust_proxy=True) == "2.2.2.2"


# ------------------------------------------------------------- RateLimiter
class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limiter(db: Database, clock: Clock) -> RateLimiter:
    return RateLimiter(db, clock)


WINDOW = timedelta(minutes=15)


def test_allowed_until_the_limit_is_reached(limiter: RateLimiter):
    for _ in range(3):
        assert limiter.allowed("ip:1.2.3.4", 3, WINDOW)
        limiter.hit("ip:1.2.3.4")
    assert not limiter.allowed("ip:1.2.3.4", 3, WINDOW)


def test_allowed_alone_does_not_count(limiter: RateLimiter):
    for _ in range(10):
        assert limiter.allowed("ip:1.2.3.4", 1, WINDOW)


def test_buckets_are_independent(limiter: RateLimiter):
    limiter.hit("ip:1.2.3.4")
    assert not limiter.allowed("ip:1.2.3.4", 1, WINDOW)
    assert limiter.allowed("ip:5.6.7.8", 1, WINDOW)
    assert limiter.allowed("user:alice", 1, WINDOW)


def test_hits_expire_with_the_window(limiter: RateLimiter, clock: Clock):
    limiter.hit("b")
    limiter.hit("b")
    assert not limiter.allowed("b", 2, WINDOW)
    clock.advance(minutes=14, seconds=59)
    assert not limiter.allowed("b", 2, WINDOW)
    clock.advance(seconds=2)
    assert limiter.allowed("b", 2, WINDOW)


def test_the_window_is_per_call(limiter: RateLimiter, clock: Clock):
    limiter.hit("b")
    clock.advance(minutes=30)
    assert limiter.allowed("b", 1, timedelta(hours=1)) is False
    assert limiter.allowed("b", 1, WINDOW) is True


def test_limits_survive_a_restart(tmp_path: Path, clock: Clock):
    path = tmp_path / "persist.db"
    first = Database(path)
    first.migrate(MIGRATIONS)
    RateLimiter(first, clock).hit("ip:9.9.9.9")
    second = Database(path)
    assert not RateLimiter(second, clock).allowed("ip:9.9.9.9", 1, WINDOW)


def test_hit_purges_records_older_than_a_day(db: Database, limiter: RateLimiter, clock: Clock):
    limiter.hit("old")
    clock.advance(hours=25)
    limiter.hit("new")
    assert db.count_hits("old", "2000-01-01T00:00:00+00:00") == 0
    assert db.count_hits("new", "2000-01-01T00:00:00+00:00") == 1


# ----------------------------------------------------------------- headers
def test_csp_is_the_exact_policy():
    assert SECURITY_HEADERS["Content-Security-Policy"] == (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )


def test_other_security_headers_are_exact():
    assert {k: v for k, v in SECURITY_HEADERS.items() if k != "Content-Security-Policy"} == {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    def plain() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/custom")
    def custom() -> PlainTextResponse:
        return PlainTextResponse("ok", headers={"Referrer-Policy": "no-referrer"})

    @app.get("/boom")
    def boom() -> PlainTextResponse:
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def test_middleware_adds_every_header_to_a_response(client: TestClient):
    response = client.get("/plain")
    assert response.status_code == 200
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_middleware_adds_headers_to_not_found_and_error_responses(client: TestClient):
    for path, status in (("/missing", 404), ("/boom", 500)):
        response = client.get(path)
        assert response.status_code == status
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value


def test_middleware_keeps_headers_a_route_set_explicitly(client: TestClient):
    response = client.get("/custom")
    assert response.headers.get_list("Referrer-Policy") == ["no-referrer"]
    assert response.headers["X-Frame-Options"] == "DENY"
