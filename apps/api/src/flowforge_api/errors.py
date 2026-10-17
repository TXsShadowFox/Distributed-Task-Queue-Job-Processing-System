"""Consistent error envelope: ``{"error": {"code", "message", "details", "request_id"}}``. Internal errors never leak
stack traces or SQL to clients; they are logged with the request id instead."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from flowforge_core.context import request_info
from flowforge_core.errors import FlowForgeError, RateLimited
from flowforge_core.logs import get_logger

log = get_logger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
}


def error_response(
    status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_info().request_id,
            }
        },
        headers=headers,
    )


def _jsonable_validation_errors(errors: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "loc": [str(p) for p in err.get("loc", ())],
            "msg": str(err.get("msg", ""))[:300],
            "type": err.get("type"),
        }
        for err in errors[:50]
    ]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FlowForgeError)
    async def _domain(_: Request, exc: FlowForgeError) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimited):
            headers = {"Retry-After": str(exc.retry_after)}
        if exc.status_code >= 500:
            log.error("domain_error", code=exc.code, error=exc.message)
        return error_response(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422,
            "validation_failed",
            "request validation failed",
            _jsonable_validation_errors(list(exc.errors())),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(
            exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(OperationalError)
    async def _db_unavailable(_: Request, exc: OperationalError) -> JSONResponse:
        log.error("database_unavailable", error=str(exc.orig)[:300] if exc.orig else str(exc)[:300])
        return error_response(
            503,
            "service_unavailable",
            "database temporarily unavailable; retry shortly",
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        # Handlers run outside the original `except` block, so the exception is passed explicitly.
        log.error("database_error", error=str(exc.orig)[:300] if exc.orig else None, exc_info=exc)
        return error_response(500, "internal_error", "internal server error")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, exc_info=exc)
        return error_response(500, "internal_error", "internal server error")
