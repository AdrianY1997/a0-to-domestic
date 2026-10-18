"""RFC 9457 ``application/problem+json`` error handling."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from a0td.domain.errors import HTTP_STATUS_BY_CODE, DomainError, ErrorCode

PROBLEM_JSON = "application/problem+json"

STATUS_BY_CODE = HTTP_STATUS_BY_CODE


def problem_response(
    status: int,
    code: ErrorCode | str,
    detail: str,
    params: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    code_str = code.value if isinstance(code, ErrorCode) else code
    body: dict[str, Any] = {
        "type": f"urn:a0td:error:{code_str}",
        "title": code_str.replace("_", " ").capitalize(),
        "status": status,
        "detail": detail,
        "code": code_str,
        "params": params or {},
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(jsonable_encoder(body), status_code=status, media_type=PROBLEM_JSON)


class ProblemError(Exception):
    """An error with an explicit HTTP status and a code outside the domain enum (e.g. 501)."""

    def __init__(
        self, status: int, code: str, detail: str, params: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.params = params or {}


async def _problem_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)
    return problem_response(exc.status, exc.code, exc.detail, exc.params)


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    status = STATUS_BY_CODE.get(exc.code, 400)
    return problem_response(status, exc.code, exc.detail, exc.params, exc.errors)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {
            "field": ".".join(str(p) for p in e["loc"] if p != "body"),
            "message": e["msg"],
            "type": e["type"],
        }
        for e in exc.errors()
    ]
    return problem_response(
        422, ErrorCode.INVALID_SETTINGS, "Request validation failed", errors=errors
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_SETTINGS
    return problem_response(exc.status_code, code, str(exc.detail))


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # Never echo the exception text (it can carry paths); the server logs the traceback.
    return problem_response(500, "INTERNAL_ERROR", "internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, _unhandled_error_handler)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(ProblemError, _problem_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
