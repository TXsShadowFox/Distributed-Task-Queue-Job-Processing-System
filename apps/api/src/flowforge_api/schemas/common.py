from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StrictInput(BaseModel):
    """Request bodies reject unknown fields (typos surface as 422 instead of being silently ignored)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "Bad request",
        401: "Missing or invalid credentials",
        403: "Authenticated but not permitted",
        404: "Not found (also returned for resources in other tenants)",
        409: "Conflict with the current state",
        413: "Payload too large",
        422: "Validation failed",
        429: "Rate limited (see Retry-After)",
        503: "Dependency temporarily unavailable",
    }
    return {
        code: {"model": ErrorResponse, "description": descriptions.get(code, "Error")}
        for code in codes
    }


class OkOut(BaseModel):
    ok: bool = True
