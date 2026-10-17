from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from flowforge_api.schemas.common import APIModel, StrictInput
from flowforge_core.db.models import Task, TaskAttempt, TaskEvent, TaskLog
from flowforge_shared.priority import QUEUE_NAME_PATTERN

MAX_METADATA_KEYS = 50


class TaskSubmit(StrictInput):
    task_type: str = Field(min_length=1, max_length=100, examples=["generate_report"])
    payload: dict[str, Any] = Field(default_factory=dict, examples=[{"report_id": 42}])
    queue: str | None = Field(default=None, pattern=QUEUE_NAME_PATTERN)
    priority: int | None = Field(default=None, ge=0, le=9, description="0 (lowest) … 9 (highest)")
    max_retries: int | None = Field(default=None, ge=0, le=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    retry_backoff_seconds: float | None = Field(default=None, ge=0, le=3600)
    retry_backoff_max_seconds: float | None = Field(default=None, ge=0, le=86_400)
    dead_letter: bool = Field(
        default=True, description="Move to the dead-letter queue after permanent failure"
    )
    delay_seconds: float | None = Field(default=None, ge=0, le=365 * 86_400)
    run_at: datetime | None = Field(
        default=None, description="Absolute UTC time to run (timezone-aware)"
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> TaskSubmit:
        if self.delay_seconds is not None and self.run_at is not None:
            raise ValueError("use either delay_seconds or run_at, not both")
        if self.run_at is not None and self.run_at.tzinfo is None:
            raise ValueError("run_at must include a timezone offset")
        if len(self.metadata) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata may contain at most {MAX_METADATA_KEYS} keys")
        return self


class TaskBatchSubmit(StrictInput):
    tasks: list[TaskSubmit] = Field(min_length=1, max_length=1000)


class BatchItemOut(BaseModel):
    index: int
    id: uuid.UUID
    state: str
    created: bool


class BatchOut(BaseModel):
    items: list[BatchItemOut]
    created: int
    replayed: int


def _duration_ms(task: Task) -> int | None:
    if task.started_at and task.finished_at:
        return int((task.finished_at - task.started_at).total_seconds() * 1000)
    return None


class TaskSummaryOut(APIModel):
    id: uuid.UUID
    task_type: str
    queue: str
    priority: int
    state: str
    progress: int
    attempt_count: int
    retry_count: int
    max_retries: int
    idempotency_key: str | None
    error_type: str | None
    worker_name: str | None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    available_at: datetime | None
    duration_ms: int | None
    workflow_run_id: uuid.UUID | None
    schedule_id: uuid.UUID | None
    archived_at: datetime | None
    version: int

    @classmethod
    def build(cls, task: Task, worker_name: str | None) -> TaskSummaryOut:
        return cls(
            id=task.id,
            task_type=task.task_type,
            queue=task.queue,
            priority=task.priority,
            state=task.state,
            progress=task.progress,
            attempt_count=task.attempt_count,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            idempotency_key=task.idempotency_key,
            error_type=(task.error or {}).get("type"),
            worker_name=worker_name,
            created_at=task.created_at,
            updated_at=task.updated_at,
            queued_at=task.queued_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            available_at=task.available_at,
            duration_ms=_duration_ms(task),
            workflow_run_id=task.workflow_run_id,
            schedule_id=task.schedule_id,
            archived_at=task.archived_at,
            version=task.version,
        )


class TaskOut(TaskSummaryOut):
    project_id: uuid.UUID
    payload: dict[str, Any]
    metadata: dict[str, Any]
    result: Any = None
    error: dict[str, Any] | None
    progress_message: str | None
    timeout_seconds: int
    retry_backoff_seconds: float
    retry_backoff_max_seconds: float
    dead_letter: bool
    cancel_requested_at: datetime | None
    dead_lettered_at: datetime | None
    schedule_fire_time: datetime | None
    trace_id: str | None

    @classmethod
    def from_task(cls, task: Task, worker_name: str | None = None) -> TaskOut:
        summary = TaskSummaryOut.build(task, worker_name).model_dump()
        trace_id = (
            task.traceparent.split("-")[1]
            if task.traceparent and task.traceparent.count("-") == 3
            else None
        )
        return cls(
            **summary,
            project_id=task.project_id,
            payload=task.payload,
            metadata=task.meta,
            result=task.result,
            error=task.error,
            progress_message=task.progress_message,
            timeout_seconds=task.timeout_seconds,
            retry_backoff_seconds=task.backoff_base_seconds,
            retry_backoff_max_seconds=task.backoff_max_seconds,
            dead_letter=task.dead_letter,
            cancel_requested_at=task.cancel_requested_at,
            dead_lettered_at=task.dead_lettered_at,
            schedule_fire_time=task.schedule_fire_time,
            trace_id=trace_id,
        )


class TaskListOut(BaseModel):
    items: list[TaskSummaryOut]
    next_cursor: str | None
    has_more: bool
    total: int | None = None


class AttemptOut(APIModel):
    id: uuid.UUID
    attempt_number: int
    status: str
    worker_id: uuid.UUID | None
    worker_name: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    queue_wait_ms: int | None
    error_type: str | None
    error_message: str | None
    traceback: str | None

    @classmethod
    def build(cls, attempt: TaskAttempt, worker_name: str | None) -> AttemptOut:
        return cls(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            worker_id=attempt.worker_id,
            worker_name=worker_name,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            duration_ms=attempt.duration_ms,
            queue_wait_ms=attempt.queue_wait_ms,
            error_type=attempt.error_type,
            error_message=attempt.error_message,
            traceback=attempt.traceback,
        )


class EventOut(APIModel):
    id: int
    event_type: str
    from_state: str | None
    to_state: str | None
    attempt_number: int | None
    message: str | None
    data: dict[str, Any] | None
    actor_type: str
    actor_id: str | None
    created_at: datetime

    @classmethod
    def build(cls, event: TaskEvent) -> EventOut:
        return cls.model_validate(event)


class LogOut(APIModel):
    id: int
    attempt_number: int
    level: str
    message: str
    fields: dict[str, Any] | None
    logged_at: datetime

    @classmethod
    def build(cls, row: TaskLog) -> LogOut:
        return cls.model_validate(row)


class LogListOut(BaseModel):
    items: list[LogOut]
    next_after_id: int


class TaskTypeOut(BaseModel):
    name: str
    description: str
    queue: str
    payload_schema: dict[str, Any]
    example_payload: dict[str, Any]
    default_priority: int
    default_max_retries: int
    default_timeout_seconds: int
    executor: str


class BulkIdsIn(StrictInput):
    task_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class BulkResultItem(BaseModel):
    id: uuid.UUID
    ok: bool
    error: str | None = None


class BulkResultOut(BaseModel):
    results: list[BulkResultItem]
    succeeded: int
    failed: int
