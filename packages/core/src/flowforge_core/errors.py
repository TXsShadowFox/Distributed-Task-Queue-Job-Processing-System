"""Domain errors. The API maps each to an HTTP status and a stable machine-readable ``code``."""

from __future__ import annotations

from typing import Any


class FlowForgeError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ValidationFailed(FlowForgeError):
    status_code = 422
    code = "validation_failed"


class NotFound(FlowForgeError):
    """Also used for resources in another tenant, so their existence is not revealed."""

    status_code = 404
    code = "not_found"


class Conflict(FlowForgeError):
    status_code = 409
    code = "conflict"


class InvalidStateTransition(Conflict):
    code = "invalid_state_transition"


class IdempotencyConflict(Conflict):
    code = "idempotency_key_reused"


class AuthenticationFailed(FlowForgeError):
    status_code = 401
    code = "authentication_failed"


class PermissionDenied(FlowForgeError):
    status_code = 403
    code = "permission_denied"


class RateLimited(FlowForgeError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retry_after = retry_after


class PayloadTooLarge(FlowForgeError):
    status_code = 413
    code = "payload_too_large"


class ServiceUnavailable(FlowForgeError):
    status_code = 503
    code = "service_unavailable"
