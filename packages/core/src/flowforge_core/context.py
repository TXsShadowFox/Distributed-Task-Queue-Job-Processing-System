"""Who is acting (for audit trails and attribution) and request metadata carried via contextvars."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str  # user | api_key | worker | scheduler | system
    id: str | None = None
    label: str | None = None  # email or API-key prefix (never a secret)
    user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None

    @classmethod
    def for_user(cls, user_id: uuid.UUID, email: str) -> Actor:
        return cls("user", str(user_id), email, user_id=user_id)

    @classmethod
    def for_api_key(cls, key_id: uuid.UUID, prefix: str) -> Actor:
        return cls("api_key", str(key_id), prefix, api_key_id=key_id)

    @classmethod
    def for_worker(cls, worker_id: uuid.UUID | str) -> Actor:
        return cls("worker", str(worker_id))


SYSTEM = Actor("system")
SCHEDULER = Actor("scheduler")


@dataclass(frozen=True, slots=True)
class RequestInfo:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None


current_request: ContextVar[RequestInfo | None] = ContextVar(
    "flowforge_current_request", default=None
)


def request_info() -> RequestInfo:
    return current_request.get() or RequestInfo()
