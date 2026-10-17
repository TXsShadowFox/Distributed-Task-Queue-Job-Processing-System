from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTERED"})


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True)
class Task:
    id: str
    task_type: str
    state: str
    queue: str
    priority: int
    attempt_count: int
    retry_count: int
    max_retries: int
    payload: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    progress: int = 0
    idempotency_key: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def succeeded(self) -> bool:
        return self.state == "SUCCEEDED"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data["id"]),
            task_type=data["task_type"],
            state=data["state"],
            queue=data["queue"],
            priority=int(data["priority"]),
            attempt_count=int(data.get("attempt_count", 0)),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 0)),
            payload=data.get("payload"),
            result=data.get("result"),
            error=data.get("error"),
            progress=int(data.get("progress", 0)),
            idempotency_key=data.get("idempotency_key"),
            created_at=_dt(data.get("created_at")),
            started_at=_dt(data.get("started_at")),
            finished_at=_dt(data.get("finished_at")),
            raw=data,
        )


@dataclass(frozen=True)
class TaskPage:
    items: list[Task]
    next_cursor: str | None
    has_more: bool
    total: int | None = None


@dataclass(frozen=True)
class BatchItem:
    index: int
    id: str
    state: str
    created: bool
