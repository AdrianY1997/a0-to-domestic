"""Authentication modes (ADM-03): none, password (cookie + CSRF + rate limit) and token."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from a0td.api import auth as auth_mod
from a0td.api.app import create_app
from a0td.config import AppConfig

PW = "s3cret-pass"


@pytest.fixture(scope="module")
def pw_hash() -> str:
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PW)


@pytest.fixture
def pw_client(make_config: Callable[..., AppConfig], pw_hash: str) -> Iterator[TestClient]:
    cfg = make_config(auth_mode="password", password_hash=pw_hash)
    with TestClient(create_app(cfg)) as c:
        yield c


def _login(c: TestClient, password: str = PW) -> Any:
    return c.post("/api/v1/auth/login", json={"password": password})


def test_none_mode_is_open(client: TestClient) -> None:
    assert client.get("/api/v1/presets").status_code == 200
    assert client.get("/api/v1/auth/session").json() == {
        "auth_mode": "none",
        "authenticated": True,
        "csrf_token": None,
    }
    assert client.post("/api/v1/auth/login", json={"password": "x"}).status_code == 404
    assert client.get("/api/v1/info").json()["auth_mode"] == "none"


def test_password_requires_login_but_health_and_info_open(pw_client: TestClient) -> None:
    r = pw_client.get("/api/v1/presets")
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_REQUIRED"
    assert pw_client.get("/healthz").status_code == 200
    assert pw_client.get("/readyz").status_code == 200
    info = pw_client.get("/api/v1/info")
    assert info.status_code == 200
    assert info.json()["auth_mode"] == "password"
    assert pw_client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_wrong_password(pw_client: TestClient) -> None:
    r = _login(pw_client, "nope")
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CREDENTIALS"
    assert pw_client.get("/api/v1/presets").status_code == 401


def test_login_cookie_flags_and_session(pw_client: TestClient) -> None:
    r = _login(pw_client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    csrf = r.json()["csrf_token"]
    assert csrf
    assert pw_client.get("/api/v1/presets").status_code == 200
    session = pw_client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True
    assert session["csrf_token"] == csrf


def test_csrf_required_for_state_changes(pw_client: TestClient) -> None:
    csrf = _login(pw_client).json()["csrf_token"]
    body = {"name": "mine", "settings": {}}
    missing = pw_client.post("/api/v1/presets", json=body)
    assert missing.status_code == 403
    assert missing.json()["code"] == "CSRF_INVALID"
    bad = pw_client.post("/api/v1/presets", json=body, headers={"X-CSRF-Token": "x" * 64})
    assert bad.status_code == 403
    ok = pw_client.post("/api/v1/presets", json=body, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201


def test_logout_clears_session(pw_client: TestClient) -> None:
    csrf = _login(pw_client).json()["csrf_token"]
    assert pw_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    pw_client.cookies.clear()
    assert pw_client.get("/api/v1/presets").status_code == 401


def test_tampered_and_expired_cookie(
    pw_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(pw_client)
    good = pw_client.cookies.get(auth_mod.COOKIE_NAME)
    assert good
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    pw_client.cookies.set(auth_mod.COOKIE_NAME, flipped)
    assert pw_client.get("/api/v1/presets").status_code == 401
    pw_client.cookies.set(auth_mod.COOKIE_NAME, good)
    assert pw_client.get("/api/v1/presets").status_code == 200
    real = auth_mod.time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real() + auth_mod.SESSION_TTL_S + 5)
    assert pw_client.get("/api/v1/presets").status_code == 401


def test_login_rate_limit(pw_client: TestClient) -> None:
    for _ in range(auth_mod.LOGIN_MAX_ATTEMPTS):
        assert _login(pw_client, "bad").status_code == 401
    r = _login(pw_client)  # even the right password is refused now
    assert r.status_code == 429
    assert r.json()["code"] == "RATE_LIMITED"


def test_secret_persisted_in_data_dir(make_config: Callable[..., AppConfig], pw_hash: str) -> None:
    cfg = make_config(auth_mode="password", password_hash=pw_hash)
    cookie = auth_mod.AuthService(cfg).new_session()
    assert (cfg.data_dir / "secret.key").is_file()
    assert auth_mod.AuthService(cfg).session_id(cookie) is not None  # same key after a restart
    other = make_config(
        auth_mode="password", password_hash=pw_hash, secret_key="explicit-key-" + "x" * 32
    )
    assert auth_mod.AuthService(other).session_id(cookie) is None


def test_misconfiguration_fails_fast(make_config: Callable[..., AppConfig]) -> None:
    with pytest.raises(auth_mod.AuthConfigError):
        create_app(make_config(auth_mode="password"))
    with pytest.raises(auth_mod.AuthConfigError):
        create_app(make_config(auth_mode="token"))


def test_token_mode(make_config: Callable[..., AppConfig]) -> None:
    cfg = make_config(auth_mode="token", token="tok-123")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/v1/presets").status_code == 401
        assert c.get("/api/v1/presets", headers={"Authorization": "Bearer no"}).status_code == 401
        assert (
            c.get("/api/v1/presets", headers={"Authorization": "Basic tok-123"}).status_code == 401
        )
        h = {"Authorization": "Bearer tok-123"}
        assert c.get("/api/v1/presets", headers=h).status_code == 200
        # no CSRF needed with a bearer token
        created = c.post("/api/v1/presets", json={"name": "a", "settings": {}}, headers=h)
        assert created.status_code == 201
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/v1/info").json()["auth_mode"] == "token"
        assert c.post("/api/v1/auth/login", json={"password": "x"}).status_code == 404


def test_logout_requires_csrf_with_a_session(pw_client: TestClient) -> None:
    csrf = _login(pw_client).json()["csrf_token"]
    missing = pw_client.post("/api/v1/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["code"] == "CSRF_INVALID"
    assert pw_client.get("/api/v1/presets").status_code == 200  # still logged in
    assert pw_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    # without a session there is nothing to protect: the cookie is simply cleared
    pw_client.cookies.clear()
    assert pw_client.post("/api/v1/auth/logout").status_code == 204


def test_csrf_required_on_document_upload(pw_client: TestClient) -> None:
    csrf = _login(pw_client).json()["csrf_token"]
    files = {"file": ("a.pdf", b"%PDF-1.4 x")}
    denied = pw_client.post("/api/v1/documents", files=files)
    assert denied.status_code == 403
    assert denied.json()["code"] == "CSRF_INVALID"
    ok = pw_client.post("/api/v1/documents", files=files, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 202


def test_api_docs_are_protected(pw_client: TestClient, client: TestClient) -> None:
    for path in ("/api/docs", "/api/openapi.json"):
        assert client.get(path).status_code == 200  # auth none stays open
        denied = pw_client.get(path)
        assert denied.status_code == 401
        assert denied.json()["code"] == "AUTH_REQUIRED"
    _login(pw_client)
    assert pw_client.get("/api/openapi.json").status_code == 200
    assert pw_client.get("/api/docs").status_code == 200


def test_token_mode_open_paths_and_docs(make_config: Callable[..., AppConfig]) -> None:
    cfg = make_config(auth_mode="token", token="tok-123")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/v1/info").status_code == 200
        session = c.get("/api/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["auth_mode"] == "token"
        assert session.json()["authenticated"] is False
        assert c.post("/api/v1/auth/logout").status_code == 204
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/openapi.json").status_code == 401
        h = {"Authorization": "Bearer tok-123"}
        assert c.get("/api/openapi.json", headers=h).status_code == 200
        assert c.get("/api/v1/auth/session", headers=h).json()["authenticated"] is True
        # no CSRF on uploads with a bearer token
        up = c.post("/api/v1/documents", files={"file": ("a.pdf", b"%PDF-1.4 x")}, headers=h)
        assert up.status_code == 202


def test_secure_cookie_and_client_ip_behind_trusted_proxy(
    make_config: Callable[..., AppConfig], pw_hash: str
) -> None:
    app = create_app(make_config(auth_mode="password", password_hash=pw_hash))
    fwd = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}
    # what uvicorn does with --proxy-headers --forwarded-allow-ips=<proxy>
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="testclient")) as c:  # type: ignore[arg-type]
        r = c.post("/api/v1/auth/login", json={"password": PW}, headers=fwd)
        assert "secure" in r.headers["set-cookie"].lower()
        # one forwarded client exhausting its attempts does not lock out another
        for _ in range(auth_mod.LOGIN_MAX_ATTEMPTS):
            bad = c.post("/api/v1/auth/login", json={"password": "x"}, headers=fwd)
            assert bad.status_code == 401
        assert c.post("/api/v1/auth/login", json={"password": PW}, headers=fwd).status_code == 429
        other = {**fwd, "X-Forwarded-For": "198.51.100.9"}
        assert c.post("/api/v1/auth/login", json={"password": PW}, headers=other).status_code == 200
    # proxy not trusted (default): the headers are ignored
    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="10.9.9.9")) as c:  # type: ignore[arg-type]
        r = c.post("/api/v1/auth/login", json={"password": PW}, headers=fwd)
        assert "secure" not in r.headers["set-cookie"].lower()


def test_only_failed_logins_consume_attempts(pw_client: TestClient) -> None:
    for _ in range(auth_mod.LOGIN_MAX_ATTEMPTS + 3):
        assert _login(pw_client).status_code == 200
    for _ in range(auth_mod.LOGIN_MAX_ATTEMPTS):
        assert _login(pw_client, "bad").status_code == 401
    assert _login(pw_client, "bad").status_code == 429


def test_short_secret_key_is_rejected(make_config: Callable[..., AppConfig], pw_hash: str) -> None:
    with pytest.raises(auth_mod.AuthConfigError, match="A0TD_SECRET_KEY is too short"):
        create_app(make_config(auth_mode="password", password_hash=pw_hash, secret_key="short"))
    ok = make_config(auth_mode="password", password_hash=pw_hash, secret_key="k" * 32)
    assert auth_mod.load_secret_key(ok) == b"k" * 32


@pytest.mark.parametrize("content", [b"", b"  \n", b"too-short"])
def test_short_secret_file_is_regenerated(
    make_config: Callable[..., AppConfig], pw_hash: str, content: bytes
) -> None:
    cfg = make_config(auth_mode="password", password_hash=pw_hash)
    path = cfg.data_dir / "secret.key"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    key = auth_mod.load_secret_key(cfg)
    assert len(key) >= auth_mod.MIN_SECRET_BYTES
    assert path.read_bytes() == key
    assert auth_mod.load_secret_key(cfg) == key  # stable afterwards
    assert not list(path.parent.glob(".secret.key.*"))  # no temp leftovers
