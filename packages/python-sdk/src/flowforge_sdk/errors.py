from __future__ import annotations

from typing import Any


class FlowForgeError(Exception):
    """Base error. ``status`` is the HTTP status (0 for transport errors); ``request_id`` helps support find logs."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "error",
        details: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details
        self.request_id = request_id

    def __str__(self) -> str:
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"[{self.status} {self.code}] {self.message}{suffix}"


class AuthenticationError(FlowForgeError):
    """401: missing, invalid, expired or revoked credentials."""


class PermissionDeniedError(FlowForgeError):
    """403: the API key lacks the required scope."""


class NotFoundError(FlowForgeError):
    """404: the resource does not exist in this project."""


class ConflictError(FlowForgeError):
    """409: e.g. retrying a task that is not failed, or reusing an idempotency key with a different request."""


class ValidationError(FlowForgeError):
    """422: unknown task type, payload not matching the task type's schema, bad parameters."""


class PayloadTooLargeError(FlowForgeError):
    """413."""


class RateLimitError(FlowForgeError):
    """429. ``retry_after`` is the server's hint in seconds."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ServerError(FlowForgeError):
    """5xx after retries were exhausted."""


class TransportError(FlowForgeError):
    """Network failure (connection refused, DNS, TLS, read timeout) after retries were exhausted."""


class TaskWaitTimeout(FlowForgeError):
    """``wait()`` gave up before the task reached a terminal state (the task keeps running)."""


_BY_STATUS: dict[int, type[FlowForgeError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    413: PayloadTooLargeError,
    422: ValidationError,
    429: RateLimitError,
}


def error_for(status: int, body: Any, retry_after: float | None = None) -> FlowForgeError:
    err = body.get("error", {}) if isinstance(body, dict) else {}
    message = str(err.get("message") or f"HTTP {status}")
    kwargs: dict[str, Any] = {
        "status": status,
        "code": str(err.get("code") or "error"),
        "details": err.get("details"),
        "request_id": err.get("request_id"),
    }
    if status == 429:
        return RateLimitError(message, retry_after=retry_after, **kwargs)
    cls = _BY_STATUS.get(status, ServerError if status >= 500 else FlowForgeError)
    return cls(message, **kwargs)
