"""Client IP resolution, persistent rate limiting and response security headers."""

import ipaddress
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.database import Database

logger = logging.getLogger(__name__)

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Rate-limit records older than this are of no use to any window in use.
_HIT_RETENTION = timedelta(days=1)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def client_ip(peer: str | None, xff: str | None, trust_proxy: bool) -> str:
    """The address rate limits are keyed on.

    With `trust_proxy`, the right-most `X-Forwarded-For` entry (the one our own
    reverse proxy appended) is used, but only when the direct peer is a
    loopback or private address, so a client reaching the app directly cannot
    pick its own identity. Otherwise the direct peer is used.
    """
    if peer is None:
        return "unknown"
    if trust_proxy and xff:
        peer_ip = _parse_ip(peer)
        if peer_ip is not None and (peer_ip.is_loopback or peer_ip.is_private):
            forwarded = xff.rsplit(",", 1)[-1].strip()
            if _parse_ip(forwarded) is not None:
                return forwarded
    return peer


class RateLimiter:
    """Sliding-window counters stored in SQLite, so limits survive restarts."""

    def __init__(self, db: Database, clock: Callable[[], datetime]) -> None:
        self._db = db
        self._clock = clock

    def allowed(self, bucket: str, limit: int, window: timedelta) -> bool:
        """Whether `bucket` has fewer than `limit` hits within `window`. Does not count."""
        since = (self._clock() - window).isoformat()
        return self._db.count_hits(bucket, since) < limit

    def hit(self, bucket: str) -> None:
        now = self._clock()
        self._db.record_hit(bucket, now.isoformat())
        self._db.purge_hits((now - _HIT_RETENTION).isoformat())


class SecurityHeadersMiddleware:
    """Adds `SECURITY_HEADERS` to every HTTP response, leaving explicit route headers alone."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._headers = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in self._headers if k not in present]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            # Unhandled errors would otherwise be answered by the outermost
            # server-error layer, bypassing this middleware and its headers.
            if started:
                raise
            logger.exception("Unhandled error while handling %s", scope.get("path"))
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", b"21"),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": b"Internal Server Error"})
