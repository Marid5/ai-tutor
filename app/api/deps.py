"""What every route needs: the clock, the running app's state and the signed-in learner.

The database, the loaded program, the settings and the rate limiter live on
`app.state`, set up by `create_app`, so several apps (one per test, or a
restart on the same database) never share module-level state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.auth import token_hash
from app.content import Program
from app.database import Database
from app.security import RateLimiter, client_ip
from app.settings import Settings

SESSION_COOKIE = "ai_tutor_session"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _db(request: Request) -> Database:
    return request.app.state.db


def _program(request: Request) -> Program:
    return request.app.state.program


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


DB = Annotated[Database, Depends(_db)]
CourseProgram = Annotated[Program, Depends(_program)]
AppSettings = Annotated[Settings, Depends(_settings)]
Limiter = Annotated[RateLimiter, Depends(_limiter)]


@dataclass(frozen=True)
class SignedIn:
    """The learner behind a request, and which of their sessions made it."""

    user_id: str
    token_hash: str


def _signed_in(request: Request, db: DB) -> SignedIn:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        digest = token_hash(raw)
        user_id = db.auth_session_user(digest, utc_now().isoformat())
        if user_id:
            return SignedIn(user_id, digest)
    raise HTTPException(status_code=401, detail="login required")


CurrentUser = Annotated[SignedIn, Depends(_signed_in)]


def request_ip(request: Request, settings: Settings) -> str:
    """The address rate limits are keyed on (see `client_ip` for when a proxy is believed).

    A proxy chain may arrive as several `X-Forwarded-For` lines; joined in
    order they form one list whose right-most entry is the nearest hop.
    """
    forwarded = ", ".join(request.headers.getlist("x-forwarded-for")) or None
    peer = request.client.host if request.client else None
    return client_ip(peer, forwarded, settings.trust_proxy)
