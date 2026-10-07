"""Client IP resolution, persistent rate limiting and response security headers."""

import ipaddress
import json
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


# Peers allowed to vouch for `X-Forwarded-For`: this host and private networks
# (a reverse proxy on the same machine or in the Docker network). Narrower than
# `ipaddress.is_private`, which would also admit unspecified, link-local and
# documentation addresses.
_TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_trusted_proxy(peer: str) -> bool:
    address = _parse_ip(peer)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address is None:
        return False
    return any(address.version == net.version and address in net for net in _TRUSTED_PROXY_NETWORKS)


def client_ip(peer: str | None, xff: str | None, trust_proxy: bool) -> str:
    """The address rate limits are keyed on.

    With `trust_proxy`, the right-most non-empty `X-Forwarded-For` entry (the one
    our own reverse proxy appended) is used, in canonical form, but only when
    the direct peer is a loopback or private-network address, so a client
    reaching the app directly cannot pick its own identity. Anything that is
    not a plain IP address (ports, brackets, garbage) falls back to the peer.
    """
    if peer is None:
        return "unknown"
    if trust_proxy and xff and _is_trusted_proxy(peer):
        entries = [entry.strip() for entry in xff.split(",")]
        entries = [entry for entry in entries if entry]
        if entries:
            forwarded = _parse_ip(entries[-1])
            if forwarded is not None:
                return str(forwarded)
    return peer


class RateLimiter:
    """Sliding-window counters stored in SQLite, so limits survive restarts."""

    def __init__(self, db: Database, clock: Callable[[], datetime]) -> None:
        self._db = db
        self._clock = clock

    def allowed(self, bucket: str, limit: int, window: timedelta) -> bool:
        """Whether `bucket` has fewer than `limit` hits within `window`. Does not count.

        Check-then-`hit` is not atomic, so concurrent requests may overshoot the limit slightly.
        """
        if window > _HIT_RETENTION:
            raise ValueError(f"window {window} exceeds the {_HIT_RETENTION} hit retention")
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
            body = json.dumps({"detail": "internal server error"}).encode()
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Refuses API request bodies over `limit` bytes with 413, before any route reads them.

    The declared `Content-Length` is checked first; a body without one (or
    one that lies) is counted while it streams in. An accepted body is
    buffered and handed on unchanged; API bodies are small JSON documents.
    """

    def __init__(self, app: ASGIApp, limit: int, prefix: str = "/api") -> None:
        self.app = app
        self.limit = limit
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length" and value.strip().isdigit() and int(value) > self.limit:
                await self._too_large(send)
                return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client went away; let the app see that as usual.
                break
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                await self._too_large(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False
        pending = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        if message["type"] != "http.request":
            pending = message

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return pending
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = json.dumps({"detail": "request body too large"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
