"""Accounts: registration, sign-in and sign-out, the password, and per-learner settings.

Sign-in is rate limited in the database, so a restart does not reset the
count: every attempt counts against the client address, and failed attempts
also count against the username (only failures, so a stranger cannot lock a
known learner out by signing in as them successfully, and the lock lasts
one short window).
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app import curriculum
from app.auth import (
    PasswordPolicyError,
    UsernameError,
    hash_password,
    new_session_token,
    normalize_username,
    token_hash,
    verify_password,
)
from app.database import Database
from app.settings import Settings

from .deps import DB, SESSION_COOKIE, AppSettings, CurrentUser, Limiter, request_ip, utc_now

router = APIRouter(prefix="/api")

LOGIN_WINDOW = timedelta(minutes=15)
REGISTER_WINDOW = timedelta(hours=1)
TOO_MANY = "too many attempts, try again later"
BAD_LOGIN = "invalid username or password"
# Checked when the username does not exist, so an unknown name costs the same
# bcrypt work as a wrong password and response times do not reveal accounts.
_DUMMY_HASH = "$2b$12$wvoG67EQ.utW9bo9C/WuN.Pao9XfxzVxwpxIH.1r97e362a4adbKi"

# Generous upper bounds: the real rules (and their messages) live in app.auth.
_NAME_MAX = 64
_PASSWORD_MAX = 1024


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=_NAME_MAX)
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=_PASSWORD_MAX)
    new: str = Field(min_length=1, max_length=_PASSWORD_MAX)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_hint_by_default: StrictBool | None = None


def _start_session(db: Database, settings: Settings, response: Response, user_id: str) -> None:
    raw, digest = new_session_token()
    expires = utc_now() + timedelta(days=settings.session_days)
    db.create_auth_session(digest, user_id, expires.isoformat())
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=settings.session_days * 86400,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/register")
def register(
    body: Credentials, request: Request, response: Response, db: DB, settings: AppSettings, limiter: Limiter
) -> dict:
    if not settings.registration_open:
        raise HTTPException(status_code=403, detail="registration is closed")
    bucket = f"register-ip:{request_ip(request, settings)}"
    allowed = limiter.allowed(bucket, settings.register_limit_per_hour, REGISTER_WINDOW)
    limiter.hit(bucket)
    if not allowed:
        raise HTTPException(status_code=429, detail=TOO_MANY)
    try:
        username = normalize_username(body.username)
        password_hash = hash_password(body.password)
    except (UsernameError, PasswordPolicyError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if db.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="username already taken")
    try:
        user_id = db.create_user(username, password_hash)
    except sqlite3.IntegrityError:  # taken by a concurrent request
        raise HTTPException(status_code=409, detail="username already taken") from None
    _start_session(db, settings, response, user_id)
    return {"username": username}


@router.post("/login")
def login(
    body: Credentials, request: Request, response: Response, db: DB, settings: AppSettings, limiter: Limiter
) -> dict:
    try:
        username: str | None = normalize_username(body.username)
    except UsernameError:
        username = None
    ip_bucket = f"login-ip:{request_ip(request, settings)}"
    user_bucket = f"login-fail-user:{username or body.username.strip().lower()}"
    allowed = limiter.allowed(ip_bucket, settings.login_ip_limit, LOGIN_WINDOW) and limiter.allowed(
        user_bucket, settings.login_user_fail_limit, LOGIN_WINDOW
    )
    limiter.hit(ip_bucket)
    if not allowed:
        raise HTTPException(status_code=429, detail=TOO_MANY)

    user = db.get_user_by_username(username) if username else None
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
    if user is None or not verify_password(body.password, user["password_hash"]):
        limiter.hit(user_bucket)
        raise HTTPException(status_code=401, detail=BAD_LOGIN)

    db.purge_expired_sessions(utc_now().isoformat())
    _start_session(db, settings, response, user["id"])
    return {"username": user["username"]}


@router.post("/logout", status_code=204)
def logout(request: Request, db: DB, settings: AppSettings) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        db.delete_auth_session(token_hash(raw))
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return response


@router.get("/account")
def account(user: CurrentUser, db: DB) -> dict:
    record = db.get_user(user.user_id)
    if record is None:
        raise HTTPException(status_code=401, detail="login required")
    return {"username": record["username"]}


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, user: CurrentUser, db: DB) -> Response:
    record = db.get_user(user.user_id)
    if record is None or not verify_password(body.current, record["password_hash"]):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    try:
        password_hash = hash_password(body.new)
    except PasswordPolicyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    db.set_password(user.user_id, password_hash)
    # Whoever else holds a session (a lost phone, a shared computer) is signed out.
    db.delete_user_sessions(user.user_id, except_hash=user.token_hash)
    return Response(status_code=204)


def _settings_view(db: Database, user_id: str) -> dict:
    return {"show_hint_by_default": curriculum.show_hint_by_default(db, user_id)}


@router.get("/settings")
def get_settings(user: CurrentUser, db: DB) -> dict:
    return _settings_view(db, user.user_id)


@router.post("/settings")
def update_settings(body: SettingsUpdate, user: CurrentUser, db: DB) -> dict:
    if body.show_hint_by_default is not None:
        curriculum.set_show_hint_by_default(db, user.user_id, body.show_hint_by_default)
    return _settings_view(db, user.user_id)
