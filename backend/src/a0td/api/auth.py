"""Optional authentication (ADM-03): password (signed cookie + CSRF) or bearer token.

Token mode is meant for API/CLI clients only: the SPA cannot send a Bearer header on EventSource or
``window.open``, so it shows a notice instead of the app when ``/info`` reports ``token``.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from a0td.api.errors import problem_response
from a0td.config import AppConfig
from a0td.domain.errors import ErrorCode

COOKIE_NAME = "a0td_session"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL_S = 12 * 3600
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 60.0
MIN_SECRET_BYTES = 32
API_PREFIX = "/api/v1/"
# Documentation endpoints live outside /api/v1/ but are protected like the API.
DOCS_PATHS = frozenset({"/api/docs", "/api/openapi.json"})
# Other non-/api/v1 routes that must not be public (Prometheus scrape: bearer token or session).
PROTECTED_PATHS = DOCS_PATHS | {"/metrics"}
# Reachable without credentials (the SPA needs them to know whether and how to log in).
OPEN_PATHS = frozenset(
    {"/api/v1/info", "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/session"}
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthConfigError(ValueError):
    """The authentication mode is selected but its secret is missing."""


def _write_key_file(path: Path, key: bytes, *, replace: bool) -> None:
    """Write ``key`` through a private temp file; ``replace`` overwrites, otherwise create-only."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # atomic, fails if another process created it first
    finally:
        tmp.unlink(missing_ok=True)


def load_secret_key(config: AppConfig) -> bytes:
    """``A0TD_SECRET_KEY`` or a random key generated once and persisted in the data dir.

    Keys shorter than 32 bytes are rejected: an explicit one aborts startup, a short or empty
    ``secret.key`` is regenerated (atomically).
    """
    if config.secret_key:
        key = config.secret_key.encode("utf-8")
        if len(key) < MIN_SECRET_BYTES:
            raise AuthConfigError(
                f"A0TD_SECRET_KEY is too short ({len(key)} bytes); "
                f"use at least {MIN_SECRET_BYTES} bytes (e.g. `openssl rand -hex 32`)"
            )
        return key
    path = config.data_dir / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.read_bytes().strip()
    except FileNotFoundError:
        existing = b""
        replace = False
    else:
        replace = True
    if len(existing) >= MIN_SECRET_BYTES:
        return existing
    key = secrets.token_hex(32).encode("ascii")
    try:
        _write_key_file(path, key, replace=replace)
    except FileExistsError:  # lost the creation race: use the winner's key
        winner = path.read_bytes().strip()
        if len(winner) >= MIN_SECRET_BYTES:
            return winner
        _write_key_file(path, key, replace=True)
    return key


class AuthService:
    """Verifies credentials, signs sessions and rate-limits logins. One per app."""

    def __init__(self, config: AppConfig) -> None:
        self.mode = config.auth_mode
        self._hasher = PasswordHasher()
        self._password_hash = config.password_hash
        self._token = config.token
        self._attempts: dict[str, deque[float]] = {}
        self._attempts_lock = threading.Lock()
        self._key = b""
        if self.mode == "password":
            if not self._password_hash:
                raise AuthConfigError("A0TD_AUTH_MODE=password requires A0TD_PASSWORD_HASH")
            self._key = load_secret_key(config)
        elif self.mode == "token" and not self._token:
            raise AuthConfigError("A0TD_AUTH_MODE=token requires A0TD_TOKEN")

    # ------------------------------------------------------------------ password mode

    def check_password(self, password: str) -> bool:
        assert self._password_hash
        try:
            return self._hasher.verify(self._password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def _sign(self, message: str) -> str:
        return hmac.new(self._key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        sid = secrets.token_hex(16)
        exp = int(time.time()) + SESSION_TTL_S
        return f"v1.{sid}.{exp}.{self._sign(f'{sid}.{exp}')}"

    def session_id(self, cookie: str | None) -> str | None:
        """The session id if the cookie is authentic and not expired."""
        if not cookie:
            return None
        parts = cookie.split(".")
        if len(parts) != 4 or parts[0] != "v1" or not parts[2].isdigit():
            return None
        _, sid, exp, sig = parts
        if not hmac.compare_digest(sig, self._sign(f"{sid}.{exp}")):
            return None
        return sid if int(exp) > time.time() else None

    def csrf_token(self, sid: str) -> str:
        return self._sign(f"csrf.{sid}")

    def csrf_ok(self, sid: str, header: str | None) -> bool:
        return bool(header) and hmac.compare_digest(header or "", self.csrf_token(sid))

    # ------------------------------------------------------------------ token mode

    def bearer_ok(self, header: str | None) -> bool:
        if not header or not self._token:
            return False
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            value.strip().encode("utf-8"), self._token.encode("utf-8")
        )

    # ------------------------------------------------------------------ rate limit

    def login_allowed(self, ip: str) -> bool:
        """True while the IP has fewer than the maximum failed attempts in the window."""
        now = time.monotonic()
        with self._attempts_lock:
            window = self._attempts.get(ip)
            if window is None:
                return True
            while window and now - window[0] > LOGIN_WINDOW_S:
                window.popleft()
            return len(window) < LOGIN_MAX_ATTEMPTS

    def record_failure(self, ip: str) -> None:
        """Count one failed login; successful logins never consume attempts."""
        now = time.monotonic()
        with self._attempts_lock:
            window = self._attempts.setdefault(ip, deque())
            while window and now - window[0] > LOGIN_WINDOW_S:
                window.popleft()
            window.append(now)
            if len(self._attempts) > 1024:  # forget idle IPs
                stale = [k for k, w in self._attempts.items() if not w or now - w[-1] > 60]
                for key in stale:
                    del self._attempts[key]


def _unauthorized(detail: str, code: str = ErrorCode.AUTH_REQUIRED.value) -> JSONResponse:
    response = problem_response(401, code, detail)
    if code == ErrorCode.AUTH_REQUIRED.value:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


class AuthMiddleware:
    """Guards ``/api/v1/*``, the API docs and ``/metrics`` (not open paths); SPA stays open."""

    def __init__(self, app: ASGIApp, auth: AuthService) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or self.auth.mode == "none"
            or not (path.startswith(API_PREFIX) or path in PROTECTED_PATHS)
            or path in OPEN_PATHS
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        denial = self._check(request)
        if denial is not None:
            await denial(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _check(self, request: Request) -> JSONResponse | None:
        auth = self.auth
        if auth.mode == "token":
            if auth.bearer_ok(request.headers.get("authorization")):
                return None
            return _unauthorized("a valid bearer token is required")
        sid = auth.session_id(request.cookies.get(COOKIE_NAME))
        if sid is None:
            return _unauthorized("login required")
        if request.method not in SAFE_METHODS and not auth.csrf_ok(
            sid, request.headers.get(CSRF_HEADER)
        ):
            return problem_response(403, "CSRF_INVALID", "missing or invalid CSRF token")
        return None
