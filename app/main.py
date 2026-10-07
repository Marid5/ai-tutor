"""The web application: API routes, security headers and the built client.

`create_app(settings)` builds an independent app. On start-up it migrates
the database, loads and validates the course (refusing to start, with every
problem listed, if the content is invalid), brings the stored course in line
with it and drops expired sign-in sessions. The module-level `app` is what
the server process runs; building it touches nothing on disk.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app.content import load_program_with_warnings
from app.database import Database
from app.security import BodySizeLimitMiddleware, RateLimiter, SecurityHeadersMiddleware
from app.settings import ROOT, Settings, load_settings

from .api import accounts, learning
from .api.deps import DB, AppSettings, CourseProgram

logger = logging.getLogger(__name__)

NOT_FOUND = "not found"
# Answer batches are the largest API bodies; 256 KB leaves ample room.
API_BODY_LIMIT = 256 * 1024
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _read_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _printable(part: object) -> str:
    """A location part (it can be a client-chosen key) without raw control characters, bounded."""
    text = str(part)
    if not text.isprintable():
        text = repr(text)
    return text if len(text) <= 64 else text[:61] + "..."


def _validation_message(error: RequestValidationError) -> str:
    """Pydantic's error list as one readable sentence, so every error body is `{"detail": str}`."""
    parts = []
    for item in error.errors():
        where = ".".join(
            _printable(part) for part in item.get("loc", ()) if part not in ("body", "query", "path")
        )
        parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
    return "; ".join(parts) or "invalid request"


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_path)
        db.migrate()
        # Raises ContentError listing every problem, so invalid content stops start-up.
        program, warnings = load_program_with_warnings(settings.content_dir)
        for warning in warnings:
            logger.warning("content: %s", warning)
        db.upsert_program(program)
        db.purge_expired_sessions(datetime.now(UTC).isoformat())
        app.state.db = db
        app.state.program = program
        app.state.limiter = RateLimiter(db, clock=lambda: datetime.now(UTC))
        app.state.version = _read_version()
        yield

    # No generated API docs: they would need inline scripts the CSP forbids,
    # and the API is documented for the client, not published.
    app = FastAPI(title="AI Tutor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    # Added last means outermost: the security headers also cover a 413.
    app.add_middleware(BodySizeLimitMiddleware, limit=API_BODY_LIMIT)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _validation_message(error)})

    # HEAD as well as GET, for uptime probes.
    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health(request: Request, db: DB, settings: AppSettings) -> dict:
        def live(table: str) -> int:
            return db.scalar(f"SELECT count(*) FROM {table} WHERE retired=0") or 0

        # Deliberately nothing about accounts: this endpoint is public.
        return {
            "status": "ok",
            "version": request.app.state.version,
            "git_sha": settings.git_sha,
            "program_version": db.program_version(),
            "chapters": live("chapters"),
            "lessons": live("lessons"),
            "cards": live("cards"),
        }

    @app.get("/api/config")
    def config(program: CourseProgram, settings: AppSettings) -> dict:
        return {
            "title": program.title,
            "description": program.description,
            "language": program.language,
            "registration_open": settings.registration_open,
        }

    app.include_router(accounts.router)
    app.include_router(learning.router)

    @app.api_route("/api/{rest:path}", methods=_ALL_METHODS, include_in_schema=False)
    def unknown_api(rest: str) -> None:
        # Without this, an unknown API path would fall through to the client
        # and come back as the HTML shell with status 200.
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def client(path: str, settings: AppSettings) -> FileResponse:
        if path == "api":
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        static = settings.static_dir.resolve()
        index = static / "index.html"
        # The path arrives percent-decoded ("%2e%2e" is already ".."), so it is
        # resolved and anything that lands outside the static directory (the
        # database lives next to it) is refused.
        try:
            candidate = (static / path).resolve()
            servable = bool(path) and candidate.is_relative_to(static) and candidate.is_file()
        except (OSError, ValueError):
            servable = False
        if servable and candidate != index:
            return FileResponse(candidate)
        if index.is_file():
            # The shell names the hashed bundles of the current build, so it must never be cached.
            return FileResponse(index, headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="client build missing; run the frontend build")

    return app


app = create_app(load_settings())
