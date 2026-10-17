"""Wire contracts shared by the API, scheduler and workers.

* ``DispatchMessage`` — the Redis Stream entry. It carries only identifiers: the payload stays in PostgreSQL, so
  Redis never holds tenant data and a stale message can never resurrect stale input.
* ``RealtimeEvent`` — published on Redis Pub/Sub after state changes commit; delivered to dashboards via SSE.
  Events are notifications (best effort); clients re-fetch authoritative state from the API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DispatchReason(StrEnum):
    SUBMIT = "submit"
    DELAY_ELAPSED = "delay_elapsed"
    RETRY = "retry"
    MANUAL_RETRY = "manual_retry"
    SCHEDULE = "schedule"
    WORKFLOW = "workflow"
    RECONCILE = "reconcile"


class DispatchMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: UUID
    project_id: UUID
    queue: str
    priority: int
    reason: DispatchReason
    enqueued_at: datetime
    traceparent: str | None = None

    def to_fields(self) -> dict[str, str]:
        fields = {
            "task_id": str(self.task_id),
            "project_id": str(self.project_id),
            "queue": self.queue,
            "priority": str(self.priority),
            "reason": self.reason.value,
            "enqueued_at": self.enqueued_at.isoformat(),
        }
        if self.traceparent:
            fields["traceparent"] = self.traceparent
        return fields

    @classmethod
    def from_fields(cls, fields: dict[bytes, bytes] | dict[str, str]) -> DispatchMessage:
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in fields.items()
        }
        return cls.model_validate(decoded)


class EventType(StrEnum):
    TASK_UPDATED = "task.updated"
    TASK_PROGRESS = "task.progress"
    TASK_LOG = "task.log"
    WORKER_UPDATED = "worker.updated"
    WORKFLOW_RUN_UPDATED = "workflow_run.updated"
    SCHEDULE_UPDATED = "schedule.updated"


class RealtimeEvent(BaseModel):
    type: EventType
    project_id: UUID | None = None  # None = fleet-wide event (e.g. worker heartbeat)
    data: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


def project_channel(project_id: UUID | str, prefix: str = "ff") -> str:
    return f"{prefix}:events:project:{project_id}"


def fleet_channel(prefix: str = "ff") -> str:
    return f"{prefix}:events:fleet"
