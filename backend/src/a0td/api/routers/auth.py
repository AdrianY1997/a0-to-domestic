"""Login, logout and session status (SPEC ADM-03). Only meaningful with password auth."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from a0td.api.auth import COOKIE_NAME, CSRF_HEADER, SESSION_TTL_S, AuthService
from a0td.api.errors import ProblemError
from a0td.domain.errors import DomainError, ErrorCode

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=1024)


class SessionOut(BaseModel):
    auth_mode: str
    authenticated: bool
    csrf_token: str | None = None


def _auth(request: Request) -> AuthService:
    auth: AuthService = request.app.state.auth
    return auth


def _client_ip(request: Request) -> str:
    """Peer address; uvicorn rewrites it from X-Forwarded-For only for A0TD_TRUSTED_PROXIES."""
    return request.client.host if request.client else "unknown"


def _secure(request: Request) -> bool:
    """Scheme as seen by the server; uvicorn applies X-Forwarded-Proto from trusted proxies only."""
    return request.url.scheme == "https"


@router.post("/login", response_model=SessionOut)
def login(body: LoginBody, request: Request, response: Response) -> SessionOut:
    auth = _auth(request)
    if auth.mode != "password":
        raise DomainError(ErrorCode.NOT_FOUND, "password login is not enabled")
    if not auth.login_allowed(_client_ip(request)):
        raise ProblemError(429, "RATE_LIMITED", "too many login attempts, try again in a minute")
    if not auth.check_password(body.password):
        auth.record_failure(_client_ip(request))
        raise ProblemError(401, "INVALID_CREDENTIALS", "wrong password")
    cookie = auth.new_session()
    sid = auth.session_id(cookie)
    assert sid is not None
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="strict",
        secure=_secure(request),
        path="/",
    )
    return SessionOut(auth_mode=auth.mode, authenticated=True, csrf_token=auth.csrf_token(sid))


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    """Clear the cookie. With a valid session the CSRF header is required (no logout CSRF)."""
    auth = _auth(request)
    if auth.mode == "password":
        sid = auth.session_id(request.cookies.get(COOKIE_NAME))
        if sid is not None and not auth.csrf_ok(sid, request.headers.get(CSRF_HEADER)):
            raise ProblemError(403, "CSRF_INVALID", "missing or invalid CSRF token")
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


@router.get("/session", response_model=SessionOut)
def session(request: Request) -> SessionOut:
    """Session status: the SPA calls this on load to pick between the app and the login page."""
    auth = _auth(request)
    if auth.mode == "none":
        return SessionOut(auth_mode="none", authenticated=True)
    if auth.mode == "token":
        ok = auth.bearer_ok(request.headers.get("authorization"))
        return SessionOut(auth_mode="token", authenticated=ok)
    sid = auth.session_id(request.cookies.get(COOKIE_NAME))
    if sid is None:
        return SessionOut(auth_mode="password", authenticated=False)
    return SessionOut(auth_mode="password", authenticated=True, csrf_token=auth.csrf_token(sid))
