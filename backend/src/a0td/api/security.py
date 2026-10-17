"""Security headers (SPEC 14): strict CSP, nosniff, no framing, no referrer. No CORS."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
# The interactive OpenAPI page (/api/docs) loads Swagger UI from a CDN; only that page is relaxed.
DOCS_CSP = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; frame-ancestors 'none'"
)
DOCS_PATHS = frozenset({"/api/docs", "/api/docs/oauth2-redirect"})


class SecurityHeadersMiddleware:
    """Pure ASGI middleware adding the headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if scope.get("path") in DOCS_PATHS else CSP

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = csp
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        await self.app(scope, receive, send_with_headers)
