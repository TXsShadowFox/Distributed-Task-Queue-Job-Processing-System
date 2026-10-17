"""Task lifecycle operations.

Concurrency model (see docs/architecture/reliability.md):

* Every transition is a guarded ``UPDATE … WHERE id = :id AND state = :expected`` (plus a PostgreSQL trigger that
  rejects illegal transitions), so two writers can never both apply a transition from the same state.
* Read-decide-write paths (fail, cancel, retry) first take a row lock (``SELECT … FOR UPDATE``).
* Worker-originated writes are **fenced**: they must present the attempt's ``lease_token``. After a lease expires and
  the task is retried elsewhere, a slow "zombie" attempt can no longer change the task; its write is discarded and
  recorded as a ``FENCED`` event.
* Every transition into QUEUED writes an outbox row in the same transaction.
* ``version`` increments on every transition (optimistic-concurrency token exposed to API clients).
* Lock ordering is always workflow run -> task. Every path that may touch both locks the parent run first
  (``_lock_parent_run``), which rules out run/task deadlocks between workers, the API and the reaper.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, StrEnum
from typing import Any

import orjson
from sqlalchemy import Integer, cast, extract, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from flowforge_core.config import get_settings
from flowforge_core.context import SCHEDULER, Actor
from flowforge_core.db.models import OutboxEvent, Task, TaskAttempt, TaskEvent, TaskLog, WorkflowRun
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, IdempotencyConflict, InvalidStateTransition, NotFound
from flowforge_core.events import worker_control_channel
from flowforge_core.observability import metrics
from flowforge_core.services import audit, outbox
from flowforge_shared.ids import uuid7
from flowforge_shared.lifecycle import FailureDecision, FailureKind, decide_failure
from flowforge_shared.messages import DispatchReason, EventType, RealtimeEvent
from flowforge_shared.priority import PriorityBand
from flowforge_shared.states import (
    IMMEDIATELY_CANCELLABLE_STATES,
    LEASED_TASK_STATES,
    MANUALLY_RETRYABLE_STATES,
    TERMINAL_TASK_STATES,
    TIME_WAITING_STATES,
    AttemptStatus,
    TaskState,
)

TS = TaskState
MAX_ERROR_MESSAGE = 4000
MAX_TRACEBACK = 16_000
BULK_EVENT_THRESHOLD = 50


class TaskEventType(StrEnum):
    CREATED = "CREATED"
    STATE_CHANGED = "STATE_CHANGED"
    ARCHIVED = "ARCHIVED"
    FENCED = "FENCED"
    RECONCILED = "RECONCILED"


# ------------------------------------------------------------------------------------------------ data types
@dataclass(slots=True)
class TaskSpec:
    """Fully resolved submission parameters (defaults already applied from the task-type catalog)."""

    task_type: str
    payload: dict[str, Any]
    queue: str
    priority: int
    max_retries: int
    timeout_seconds: int
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 300.0
    dead_letter: bool = True
    run_at: datetime | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def request_hash(self) -> str:
        """Fingerprint of the request, used to tell a safe idempotent replay from a conflicting key reuse."""
        canonical = orjson.dumps(
            {
                "task_type": self.task_type,
                "payload": self.payload,
                "queue": self.queue,
                "priority": self.priority,
                "max_retries": self.max_retries,
                "timeout_seconds": self.timeout_seconds,
                "backoff": [self.backoff_base_seconds, self.backoff_max_seconds],
                "dead_letter": self.dead_letter,
                "run_at": self.run_at.isoformat() if self.run_at else None,
                "metadata": self.metadata,
            },
            option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS,
        )
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class SubmitOrigin:
    workflow_run_id: uuid.UUID | None = None
    workflow_node_run_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    schedule_fire_time: datetime | None = None


_NO_ORIGIN = SubmitOrigin()


@dataclass(frozen=True, slots=True)
class SubmitResult:
    task: Task
    created: bool


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    id: uuid.UUID
    project_id: uuid.UUID
    task_type: str
    queue: str
    priority: int
    payload: dict[str, Any]
    metadata: dict[str, Any]
    timeout_seconds: int
    attempt_number: int
    retry_count: int
    max_retries: int
    lease_token: uuid.UUID
    queue_wait_seconds: float | None
    traceparent: str | None
    workflow_run_id: uuid.UUID | None
    idempotency_key: str | None


@dataclass(frozen=True, slots=True)
class TaskFailure:
    kind: FailureKind
    error_type: str
    message: str
    traceback: str | None = None
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class FinishResult:
    applied: bool  # False = fenced (stale lease token) — the write was discarded
    state: TaskState | None = None
    decision: FailureDecision | None = None


# Columns returned by transition statements: enough for events, metrics and workflow progression.
_ROW = (
    Task.id,
    Task.project_id,
    Task.task_type,
    Task.queue,
    Task.priority,
    Task.state,
    Task.attempt_count,
    Task.retry_count,
    Task.max_retries,
    Task.progress,
    Task.result,
    Task.workflow_run_id,
    Task.workflow_node_run_id,
    Task.traceparent,
    Task.version,
)


# ------------------------------------------------------------------------------------------------ helpers
async def db_now(session: AsyncSession) -> datetime:
    """The transaction timestamp — identical to what ``now()`` returns in every later statement of the transaction."""
    value: datetime | None = await session.scalar(select(func.now()))
    if value is None:  # pragma: no cover - now() always returns a value
        raise RuntimeError("database did not return now()")
    return value


def _event(
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    event_type: TaskEventType,
    actor: Actor,
    *,
    from_state: str | None = None,
    to_state: str | None = None,
    attempt: int | None = None,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "project_id": project_id,
        "event_type": event_type.value,
        "from_state": from_state,
        "to_state": to_state,
        "attempt_number": attempt,
        "message": message,
        "data": data,
        "actor_type": actor.kind,
        "actor_id": actor.id,
    }


async def _core(session: AsyncSession) -> AsyncConnection:
    """The session's connection, inside the same transaction. Hot-path statements that need no ORM processing
    (no entities loaded, no identity map) run on it directly: identical SQL at a fraction of the CPU cost."""
    return await session.connection()


async def _add_events(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if rows:
        await (await _core(session)).execute(insert(TaskEvent), rows)


def task_realtime_event(row: Any) -> RealtimeEvent:
    return RealtimeEvent(
        type=EventType.TASK_UPDATED,
        project_id=row.project_id,
        data={
            "id": str(row.id),
            "state": row.state,
            "task_type": row.task_type,
            "queue": row.queue,
            "priority": row.priority,
            "attempt_count": row.attempt_count,
            "retry_count": row.retry_count,
            "progress": row.progress,
            "workflow_run_id": str(row.workflow_run_id) if row.workflow_run_id else None,
        },
    )


def _error_doc(failure: TaskFailure, attempt: int) -> dict[str, Any]:
    return {
        "type": failure.error_type[:200],
        "message": failure.message[:MAX_ERROR_MESSAGE],
        "kind": failure.kind.value,
        "retryable": failure.retryable,
        "attempt": attempt,
    }


def _duration_ms() -> Any:
    return cast(extract("epoch", func.now() - TaskAttempt.started_at) * 1000, Integer)


async def _finish_attempt(
    session: AsyncSession,
    task_id: uuid.UUID,
    attempt_number: int,
    lease_token: uuid.UUID,
    status: AttemptStatus,
    failure: TaskFailure | None = None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "finished_at": func.now(),
        "duration_ms": _duration_ms(),
    }
    if failure is not None:
        values.update(
            error_type=failure.error_type[:200],
            error_message=failure.message[:MAX_ERROR_MESSAGE],
            traceback=(failure.traceback or None) and failure.traceback[-MAX_TRACEBACK:],
        )
    await (await _core(session)).execute(
        update(TaskAttempt)
        .where(
            TaskAttempt.task_id == task_id,
            TaskAttempt.attempt_number == attempt_number,
            TaskAttempt.lease_token == lease_token,
        )
        .values(**values)
    )


async def _lock_for_api(session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.project_id == project_id).with_for_update()
    )
    if task is None:
        raise NotFound("task not found")
    return task


class _Unknown(Enum):
    RUN = "unknown"


UNKNOWN_RUN = _Unknown.RUN


async def _lock_parent_run(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    skip_locked: bool = False,
    run_id: uuid.UUID | _Unknown | None = UNKNOWN_RUN,
) -> bool:
    """Lock the task's workflow run (if any) *before* the task row. Returns False if ``skip_locked`` and the run is
    locked elsewhere. ``workflow_run_id`` never changes after creation, so reading it unlocked is safe, and a caller
    that already knows it (a worker learns it when claiming) can pass it to save a round trip."""
    if isinstance(run_id, _Unknown):
        run_id = await session.scalar(select(Task.workflow_run_id).where(Task.id == task_id))
    if run_id is None:
        return True
    locked = await session.scalar(
        select(WorkflowRun.id)
        .where(WorkflowRun.id == run_id)
        .with_for_update(skip_locked=skip_locked)
    )
    return locked is not None


async def _update_task_entity(
    session: AsyncSession, task_id: uuid.UUID, expected: TaskState, values: dict[str, Any]
) -> Task:
    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.state == expected)
        .values(version=Task.version + 1, **values)
        .returning(Task)
        .execution_options(populate_existing=True)
    )
    task = (await session.scalars(stmt)).one_or_none()
    if task is None:  # impossible while the row lock is held; kept as a loud safety net
        raise Conflict("task changed concurrently; retry the request")
    return task


# ------------------------------------------------------------------------------------------------ submission
async def submit_tasks(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    specs: Sequence[TaskSpec],
    actor: Actor,
    reason: DispatchReason = DispatchReason.SUBMIT,
    origins: Sequence[SubmitOrigin] | None = None,
    traceparent: str | None = None,
) -> list[SubmitResult]:
    """Insert tasks (QUEUED, or SCHEDULED when ``run_at`` is in the future) with their events and outbox rows.

    Idempotency: a repeated ``idempotency_key`` with an identical request returns the existing task
    (``created=False``); the same key with a different request raises ``IdempotencyConflict``.
    """
    if not specs:
        return []
    if origins is not None and len(origins) != len(specs):
        raise ValueError("origins must align with specs")
    session = uow.session
    now = await db_now(session)

    rows: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        origin = origins[i] if origins is not None else _NO_ORIGIN
        delayed = spec.run_at is not None and spec.run_at > now
        rows.append(
            {
                "id": uuid7(),
                "project_id": project_id,
                "task_type": spec.task_type,
                "queue": spec.queue,
                "priority": spec.priority,
                "state": TS.SCHEDULED if delayed else TS.QUEUED,
                "payload": spec.payload,
                "meta": spec.metadata,
                "idempotency_key": spec.idempotency_key,
                "request_hash": spec.request_hash() if spec.idempotency_key else None,
                "max_retries": spec.max_retries,
                "timeout_seconds": spec.timeout_seconds,
                "backoff_base_seconds": spec.backoff_base_seconds,
                "backoff_max_seconds": spec.backoff_max_seconds,
                "dead_letter": spec.dead_letter,
                "available_at": spec.run_at if delayed else None,
                "queued_at": None if delayed else now,
                "created_by_user_id": actor.user_id,
                "created_by_api_key_id": actor.api_key_id,
                "workflow_run_id": origin.workflow_run_id,
                "workflow_node_run_id": origin.workflow_node_run_id,
                "schedule_id": origin.schedule_id,
                "schedule_fire_time": origin.schedule_fire_time,
                "traceparent": traceparent,
            }
        )

    stmt = (
        pg_insert(Task)
        .on_conflict_do_nothing(
            index_elements=[Task.project_id, Task.idempotency_key],
            index_where=Task.idempotency_key.is_not(None),
        )
        .returning(Task)
    )
    inserted = list((await session.scalars(stmt, rows)).all())
    inserted_by_id = {t.id: t for t in inserted}

    existing_by_key: dict[str, Task] = {}
    conflicting = [r for r in rows if r["id"] not in inserted_by_id]
    if conflicting:
        keys = [r["idempotency_key"] for r in conflicting]
        existing = await session.scalars(
            select(Task).where(Task.project_id == project_id, Task.idempotency_key.in_(keys))
        )
        existing_by_key = {t.idempotency_key: t for t in existing if t.idempotency_key}

    results: list[SubmitResult] = []
    for r in rows:
        created = inserted_by_id.get(r["id"])
        if created is not None:
            results.append(SubmitResult(created, True))
            continue
        prior = existing_by_key.get(r["idempotency_key"])
        if prior is None:
            raise Conflict("idempotency key is being used by a concurrent request; retry")
        if prior.request_hash != r["request_hash"]:
            raise IdempotencyConflict(
                "idempotency key was already used with a different request",
                details={"idempotency_key": r["idempotency_key"], "task_id": str(prior.id)},
            )
        results.append(SubmitResult(prior, False))

    event_rows: list[dict[str, Any]] = []
    outbox_rows: list[dict[str, Any]] = []
    for t in inserted:
        event_rows.append(
            _event(
                t.id,
                project_id,
                TaskEventType.CREATED,
                actor,
                to_state=TS.CREATED,
                message="Task created",
            )
        )
        if t.state == TS.QUEUED:
            event_rows.append(
                _event(
                    t.id,
                    project_id,
                    TaskEventType.STATE_CHANGED,
                    actor,
                    from_state=TS.CREATED,
                    to_state=TS.QUEUED,
                    message="Queued for execution",
                )
            )
            outbox_rows.append(
                outbox.dispatch_row(
                    task_id=t.id,
                    project_id=project_id,
                    queue=t.queue,
                    priority=t.priority,
                    reason=reason,
                    enqueued_at=now,
                    traceparent=t.traceparent,
                )
            )
        else:
            event_rows.append(
                _event(
                    t.id,
                    project_id,
                    TaskEventType.STATE_CHANGED,
                    actor,
                    from_state=TS.CREATED,
                    to_state=TS.SCHEDULED,
                    message=f"Delayed until {t.available_at.isoformat() if t.available_at else '?'}",
                )
            )
    await _add_events(session, event_rows)
    if outbox_rows:
        await session.execute(insert(OutboxEvent), outbox_rows)

    if len(inserted) <= BULK_EVENT_THRESHOLD:
        for t in inserted:
            uow.emit(task_realtime_event(t))
    elif inserted:
        uow.emit(
            RealtimeEvent(
                type=EventType.TASK_UPDATED,
                project_id=project_id,
                data={"bulk": True, "count": len(inserted)},
            )
        )
    counts = Counter((t.task_type, t.queue) for t in inserted)

    def _count() -> None:
        for (task_type, queue), n in counts.items():
            metrics.TASKS_SUBMITTED.labels(task_type, queue).inc(n)

    uow.after_commit(_count)
    return results


# ------------------------------------------------------------------------------------------------ worker path
async def claim_task(
    uow: UnitOfWork, *, task_id: uuid.UUID, worker_id: uuid.UUID, lease_ttl_seconds: float
) -> ClaimedTask | None:
    """QUEUED -> RUNNING with a fresh lease. Returns None if the task is not QUEUED (duplicate delivery,
    cancelled while queued, or already claimed by another worker)."""
    token = uuid.uuid4()
    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.state == TS.QUEUED)
        .values(
            state=TS.RUNNING,
            lease_owner=worker_id,
            last_worker_id=worker_id,
            lease_token=token,
            lease_expires_at=func.now() + timedelta(seconds=lease_ttl_seconds),
            attempt_count=Task.attempt_count + 1,
            started_at=func.now(),
            finished_at=None,
            progress=0,
            progress_message=None,
            version=Task.version + 1,
        )
        .returning(
            *_ROW,
            Task.payload,
            Task.meta.label("meta"),  # Core rows are keyed by column name ("metadata")
            Task.timeout_seconds,
            Task.queued_at,
            Task.started_at,
            Task.idempotency_key,
        )
        .execution_options(synchronize_session=False)
    )
    conn = await _core(uow.session)
    row = (await conn.execute(stmt)).one_or_none()
    if row is None:
        return None
    wait = (row.started_at - row.queued_at).total_seconds() if row.queued_at else None
    # A Core INSERT rather than session.add(): this is the hottest path, and an ORM flush costs far more CPU.
    await conn.execute(
        insert(TaskAttempt).values(
            task_id=row.id,
            attempt_number=row.attempt_count,
            worker_id=worker_id,
            lease_token=token,
            status=AttemptStatus.RUNNING,
            queue_wait_ms=int(wait * 1000) if wait is not None else None,
        )
    )
    await _add_events(
        uow.session,
        [
            _event(
                row.id,
                row.project_id,
                TaskEventType.STATE_CHANGED,
                Actor.for_worker(worker_id),
                from_state=TS.QUEUED,
                to_state=TS.RUNNING,
                attempt=row.attempt_count,
                message=f"Attempt {row.attempt_count} started",
            )
        ],
    )
    uow.emit(task_realtime_event(row))
    return ClaimedTask(
        id=row.id,
        project_id=row.project_id,
        task_type=row.task_type,
        queue=row.queue,
        priority=row.priority,
        payload=row.payload,
        metadata=row.meta,
        timeout_seconds=row.timeout_seconds,
        attempt_number=row.attempt_count,
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        lease_token=token,
        queue_wait_seconds=wait,
        traceparent=row.traceparent,
        workflow_run_id=row.workflow_run_id,
        idempotency_key=row.idempotency_key,
    )


async def task_state(session: AsyncSession, task_id: uuid.UUID) -> TaskState | None:
    value = await session.scalar(select(Task.state).where(Task.id == task_id))
    return TaskState(value) if value else None


async def _lock_leased(session: AsyncSession, task_id: uuid.UUID, lease_token: uuid.UUID) -> Any:
    stmt = (
        select(
            Task.id,
            Task.project_id,
            Task.task_type,
            Task.queue,
            Task.state,
            Task.attempt_count,
            Task.retry_count,
            Task.max_retries,
            Task.dead_letter,
            Task.backoff_base_seconds,
            Task.backoff_max_seconds,
            Task.lease_token,
            Task.lease_owner,
        )
        .where(
            Task.id == task_id, Task.lease_token == lease_token, Task.state.in_(LEASED_TASK_STATES)
        )
        .with_for_update()
    )
    return (await (await _core(session)).execute(stmt)).one_or_none()


async def _record_fenced(
    uow: UnitOfWork, task_id: uuid.UUID, worker_id: uuid.UUID, operation: str
) -> None:
    project_id = await uow.session.scalar(select(Task.project_id).where(Task.id == task_id))
    if project_id is not None:
        await _add_events(
            uow.session,
            [
                _event(
                    task_id,
                    project_id,
                    TaskEventType.FENCED,
                    Actor.for_worker(worker_id),
                    message=f"Discarded '{operation}' from a stale attempt (lease no longer held)",
                )
            ],
        )
    uow.after_commit(lambda: metrics.FENCED_WRITES.labels(operation).inc())


async def complete_task(
    uow: UnitOfWork,
    *,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    worker_id: uuid.UUID,
    result: Any,
    workflow_run_id: uuid.UUID | _Unknown | None = UNKNOWN_RUN,
) -> FinishResult:
    session = uow.session
    await _lock_parent_run(session, task_id, run_id=workflow_run_id)
    locked = await _lock_leased(session, task_id, lease_token)
    if locked is None:
        await _record_fenced(uow, task_id, worker_id, "complete")
        return FinishResult(applied=False)
    stmt = (
        update(Task)
        .where(Task.id == task_id, Task.lease_token == lease_token, Task.state == locked.state)
        .values(
            state=TS.SUCCEEDED,
            result=result,
            error=None,
            progress=100,
            finished_at=func.now(),
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            version=Task.version + 1,
        )
        .returning(*_ROW)
    )
    row = (await (await _core(session)).execute(stmt)).one()
    await _finish_attempt(session, task_id, row.attempt_count, lease_token, AttemptStatus.SUCCEEDED)
    message = (
        "Completed successfully"
        if locked.state == TS.RUNNING
        else "Completed before the cancellation request was observed"
    )
    await _add_events(
        session,
        [
            _event(
                row.id,
                row.project_id,
                TaskEventType.STATE_CHANGED,
                Actor.for_worker(worker_id),
                from_state=locked.state,
                to_state=TS.SUCCEEDED,
                attempt=row.attempt_count,
                message=message,
            )
        ],
    )
    if row.workflow_node_run_id is not None:
        from flowforge_core.services import workflow_engine

        await workflow_engine.on_task_finished(uow, row, TS.SUCCEEDED)
    uow.emit(task_realtime_event(row))
    return FinishResult(applied=True, state=TS.SUCCEEDED)


async def fail_task(
    uow: UnitOfWork,
    *,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    worker_id: uuid.UUID,
    failure: TaskFailure,
    workflow_run_id: uuid.UUID | _Unknown | None = UNKNOWN_RUN,
) -> FinishResult:
    await _lock_parent_run(uow.session, task_id, run_id=workflow_run_id)
    locked = await _lock_leased(uow.session, task_id, lease_token)
    if locked is None:
        await _record_fenced(uow, task_id, worker_id, "fail")
        return FinishResult(applied=False)
    return await _apply_failure(uow, locked, failure, Actor.for_worker(worker_id))


async def _apply_failure(
    uow: UnitOfWork, locked: Any, failure: TaskFailure, actor: Actor
) -> FinishResult:
    session = uow.session
    current = TaskState(locked.state)
    decision = decide_failure(
        current_state=current,
        kind=failure.kind,
        retryable=failure.retryable,
        retry_count=locked.retry_count,
        max_retries=locked.max_retries,
        dead_letter=locked.dead_letter,
        backoff_base_seconds=locked.backoff_base_seconds,
        backoff_max_seconds=locked.backoff_max_seconds,
    )
    values: dict[str, Any] = {
        "state": decision.next_state,
        "error": _error_doc(failure, locked.attempt_count),
        "lease_owner": None,
        "lease_token": None,  # nosec B105 - clears the lease, not a password
        "lease_expires_at": None,
        "version": Task.version + 1,
    }
    if decision.next_state is TS.RETRY_SCHEDULED:
        values["available_at"] = func.now() + timedelta(seconds=decision.retry_delay_seconds or 0.0)
        if decision.consumes_retry:
            values["retry_count"] = Task.retry_count + 1
    else:
        values["finished_at"] = func.now()
        if decision.next_state is TS.DEAD_LETTERED:
            values["dead_lettered_at"] = func.now()
    stmt = (
        update(Task)
        .where(Task.id == locked.id, Task.lease_token == locked.lease_token, Task.state == current)
        .values(**values)
        .returning(*_ROW)
        .execution_options(synchronize_session=False)
    )
    row = (await session.execute(stmt)).one()
    await _finish_attempt(
        session,
        locked.id,
        locked.attempt_count,
        locked.lease_token,
        decision.attempt_status,
        failure,
    )
    await _add_events(
        session,
        [
            _event(
                row.id,
                row.project_id,
                TaskEventType.STATE_CHANGED,
                actor,
                from_state=current,
                to_state=decision.next_state,
                attempt=locked.attempt_count,
                message=f"{failure.error_type}: {failure.message[:300]} — {decision.reason}",
                data={
                    "error_type": failure.error_type,
                    "kind": failure.kind.value,
                    "retry_delay_seconds": decision.retry_delay_seconds,
                },
            )
        ],
    )
    if decision.next_state in TERMINAL_TASK_STATES and row.workflow_node_run_id is not None:
        from flowforge_core.services import workflow_engine

        await workflow_engine.on_task_finished(uow, row, decision.next_state)
    uow.emit(task_realtime_event(row))

    task_type = row.task_type

    def _count() -> None:
        if decision.next_state is TS.RETRY_SCHEDULED and decision.consumes_retry:
            metrics.TASK_RETRIES.labels(task_type, failure.kind.value).inc()
        elif decision.next_state is TS.DEAD_LETTERED:
            metrics.TASKS_DEAD_LETTERED.labels(task_type).inc()

    uow.after_commit(_count)
    return FinishResult(applied=True, state=decision.next_state, decision=decision)


async def renew_leases(
    session: AsyncSession,
    *,
    worker_id: uuid.UUID,
    lease_tokens: Sequence[uuid.UUID],
    ttl_seconds: float,
) -> dict[uuid.UUID, TaskState]:
    """Extend this worker's leases. Returns ``{lease_token: state}`` for leases still held; a missing token means the
    lease was lost (expired and reassigned) and the local execution must be abandoned."""
    if not lease_tokens:
        return {}
    stmt = (
        update(Task)
        .where(
            Task.lease_owner == worker_id,
            Task.lease_token.in_(list(lease_tokens)),
            Task.state.in_(LEASED_TASK_STATES),
        )
        .values(lease_expires_at=func.now() + timedelta(seconds=ttl_seconds))
        .returning(Task.lease_token, Task.state)
        .execution_options(synchronize_session=False)
    )
    rows = (await session.execute(stmt)).all()
    return {r.lease_token: TaskState(r.state) for r in rows}


async def update_progress(
    uow: UnitOfWork,
    *,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    progress: int,
    message: str | None,
) -> bool:
    stmt = (
        update(Task)
        .where(
            Task.id == task_id, Task.lease_token == lease_token, Task.state.in_(LEASED_TASK_STATES)
        )
        .values(
            progress=max(0, min(100, progress)),
            progress_message=(message or None) and message[:500],
        )
        .returning(Task.project_id)
        .execution_options(synchronize_session=False)
    )
    project_id = (await uow.session.execute(stmt)).scalar_one_or_none()
    if project_id is None:
        return False
    uow.emit(
        RealtimeEvent(
            type=EventType.TASK_PROGRESS,
            project_id=project_id,
            data={"id": str(task_id), "progress": progress, "message": message},
        )
    )
    return True


async def append_logs(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    if rows:
        await session.execute(insert(TaskLog), list(rows))


# ------------------------------------------------------------------------------------------------ operator actions
async def cancel_task(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    actor: Actor,
    organization_id: uuid.UUID | None = None,
) -> Task:
    """Immediate for tasks not yet running; cooperative (CANCEL_REQUESTED) for running tasks."""
    session = uow.session
    await _lock_parent_run(session, task_id)
    task = await _lock_for_api(session, project_id, task_id)
    state = TaskState(task.state)
    if state in IMMEDIATELY_CANCELLABLE_STATES:
        task = await _update_task_entity(
            session,
            task_id,
            state,
            {
                "state": TS.CANCELLED,
                "finished_at": func.now(),
                "available_at": None,
                "cancel_requested_at": func.now(),
            },
        )
        message = f"Cancelled by {actor.label or actor.kind}"
        if task.workflow_node_run_id is not None:
            from flowforge_core.services import workflow_engine

            await workflow_engine.on_task_finished(uow, task, TS.CANCELLED)
    elif state is TS.RUNNING:
        lease_owner = task.lease_owner
        task = await _update_task_entity(
            session,
            task_id,
            state,
            {"state": TS.CANCEL_REQUESTED, "cancel_requested_at": func.now()},
        )
        message = f"Cancellation requested by {actor.label or actor.kind}; waiting for the worker"
        if lease_owner is not None:
            uow.send_control(
                worker_control_channel(str(lease_owner), get_settings().redis_prefix),
                {"type": "cancel", "task_id": str(task_id)},
            )
    elif state is TS.CANCEL_REQUESTED:
        return task
    else:
        raise InvalidStateTransition(f"task is {state} and can no longer be cancelled")
    await _add_events(
        session,
        [
            _event(
                task.id,
                project_id,
                TaskEventType.STATE_CHANGED,
                actor,
                from_state=state,
                to_state=task.state,
                attempt=task.attempt_count or None,
                message=message,
            )
        ],
    )
    await audit.record(
        session,
        actor=actor,
        action="task.cancel",
        resource_type="task",
        resource_id=task_id,
        organization_id=organization_id,
        project_id=project_id,
        details={"from_state": state.value},
    )
    uow.emit(task_realtime_event(task))
    return task


async def retry_task(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    actor: Actor,
    organization_id: uuid.UUID | None = None,
) -> Task:
    """Manually re-queue a FAILED, DEAD_LETTERED or CANCELLED task with a fresh retry budget."""
    session = uow.session
    await _lock_parent_run(session, task_id)
    task = await _lock_for_api(session, project_id, task_id)
    state = TaskState(task.state)
    if state not in MANUALLY_RETRYABLE_STATES:
        raise InvalidStateTransition(
            f"only failed, dead-lettered or cancelled tasks can be retried (task is {state})"
        )
    if task.workflow_node_run_id is not None:
        from flowforge_core.services import workflow_engine

        await workflow_engine.before_manual_retry(uow, task)
    now = await db_now(session)
    task = await _update_task_entity(
        session,
        task_id,
        state,
        {
            "state": TS.QUEUED,
            "retry_count": 0,
            "queued_at": func.now(),
            "finished_at": None,
            "dead_lettered_at": None,
            "archived_at": None,
            "cancel_requested_at": None,
            "error": None,
            "result": None,
            "progress": 0,
            "progress_message": None,
        },
    )
    await session.execute(
        insert(OutboxEvent),
        [
            outbox.dispatch_row(
                task_id=task.id,
                project_id=project_id,
                queue=task.queue,
                priority=task.priority,
                reason=DispatchReason.MANUAL_RETRY,
                enqueued_at=now,
                traceparent=task.traceparent,
            )
        ],
    )
    await _add_events(
        session,
        [
            _event(
                task.id,
                project_id,
                TaskEventType.STATE_CHANGED,
                actor,
                from_state=state,
                to_state=TS.QUEUED,
                message=f"Manually retried by {actor.label or actor.kind} (retry budget reset)",
            )
        ],
    )
    await audit.record(
        session,
        actor=actor,
        action="task.retry",
        resource_type="task",
        resource_id=task_id,
        organization_id=organization_id,
        project_id=project_id,
        details={"from_state": state.value},
    )
    uow.emit(task_realtime_event(task))
    return task


async def archive_task(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    actor: Actor,
    organization_id: uuid.UUID | None = None,
) -> Task:
    """Remove a dead-lettered task from the active DLQ view (the task and its history are kept)."""
    session = uow.session
    task = await _lock_for_api(session, project_id, task_id)
    if task.state != TS.DEAD_LETTERED:
        raise InvalidStateTransition("only dead-lettered tasks can be archived")
    if task.archived_at is not None:
        return task
    stmt = (
        update(Task)
        .where(Task.id == task_id)
        .values(archived_at=func.now(), version=Task.version + 1)
        .returning(Task)
        .execution_options(populate_existing=True)
    )
    task = (await session.scalars(stmt)).one()
    await _add_events(
        session,
        [
            _event(
                task.id,
                project_id,
                TaskEventType.ARCHIVED,
                actor,
                message="Archived from the dead-letter queue",
            )
        ],
    )
    await audit.record(
        session,
        actor=actor,
        action="task.archive",
        resource_type="task",
        resource_id=task_id,
        organization_id=organization_id,
        project_id=project_id,
    )
    uow.emit(task_realtime_event(task))
    return task


# ------------------------------------------------------------------------------------------------ control plane
async def promote_due_tasks(uow: UnitOfWork, *, limit: int) -> int:
    """SCHEDULED / RETRY_SCHEDULED tasks whose time has come -> QUEUED (+ outbox), skipping rows locked elsewhere."""
    session = uow.session
    due = (
        select(Task.id, Task.state)
        .where(Task.state.in_(TIME_WAITING_STATES), Task.available_at <= func.now())
        .order_by(Task.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("due")
    )
    stmt = (
        update(Task)
        .where(Task.id == due.c.id)
        .values(state=TS.QUEUED, available_at=None, queued_at=func.now(), version=Task.version + 1)
        .returning(*_ROW, due.c.state.label("from_state"))
        .execution_options(synchronize_session=False)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return 0
    now = await db_now(session)
    events: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for r in rows:
        retry = r.from_state == TS.RETRY_SCHEDULED
        events.append(
            _event(
                r.id,
                r.project_id,
                TaskEventType.STATE_CHANGED,
                SCHEDULER,
                from_state=r.from_state,
                to_state=TS.QUEUED,
                message="Retry backoff elapsed; re-queued"
                if retry
                else "Scheduled time reached; queued",
            )
        )
        out.append(
            outbox.dispatch_row(
                task_id=r.id,
                project_id=r.project_id,
                queue=r.queue,
                priority=r.priority,
                reason=DispatchReason.RETRY if retry else DispatchReason.DELAY_ELAPSED,
                enqueued_at=now,
                traceparent=r.traceparent,
            )
        )
    await _add_events(session, events)
    await session.execute(insert(OutboxEvent), out)
    if len(rows) <= BULK_EVENT_THRESHOLD:
        for r in rows:
            uow.emit(task_realtime_event(r))
    by_state = Counter(r.from_state for r in rows)

    def _count() -> None:
        for from_state, n in by_state.items():
            metrics.TASKS_PROMOTED.labels(from_state).inc(n)

    uow.after_commit(_count)
    return len(rows)


async def expire_leases(uow: UnitOfWork, *, limit: int) -> int:
    """Recover tasks whose worker stopped renewing its lease (crash, hang, network partition).

    Locks are taken in the global order (workflow run, then task) and always with SKIP LOCKED, so the reaper never
    waits on — or deadlocks with — workers, API requests or another reaper; skipped rows are retried next cycle.
    """
    session = uow.session
    candidates = (
        (
            await session.execute(
                select(Task.id)
                .where(Task.state.in_(LEASED_TASK_STATES), Task.lease_expires_at < func.now())
                .order_by(Task.lease_expires_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    for task_id in candidates:
        if not await _lock_parent_run(session, task_id, skip_locked=True):
            continue
        locked = (
            await session.execute(
                select(
                    Task.id,
                    Task.project_id,
                    Task.task_type,
                    Task.queue,
                    Task.state,
                    Task.attempt_count,
                    Task.retry_count,
                    Task.max_retries,
                    Task.dead_letter,
                    Task.backoff_base_seconds,
                    Task.backoff_max_seconds,
                    Task.lease_token,
                    Task.lease_owner,
                )
                .where(
                    Task.id == task_id,
                    Task.state.in_(LEASED_TASK_STATES),
                    Task.lease_expires_at < func.now(),
                )
                .with_for_update(skip_locked=True)
            )
        ).one_or_none()
        if locked is None:
            continue
        await _apply_failure(
            uow,
            locked,
            TaskFailure(
                kind=FailureKind.LOST,
                error_type="LeaseExpired",
                message=(
                    f"lease expired: worker {locked.lease_owner} stopped renewing it "
                    "(crashed, hung or partitioned)"
                ),
                retryable=True,
            ),
            SCHEDULER,
        )
        recovered += 1
    if recovered:
        uow.after_commit(lambda: metrics.LEASES_EXPIRED.inc(recovered))
    return recovered


_BAND_RANGES: dict[PriorityBand, tuple[int, int]] = {
    PriorityBand.HIGH: (7, 9),
    PriorityBand.NORMAL: (4, 6),
    PriorityBand.LOW: (0, 3),
}


async def queued_queues(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(Task.queue).where(Task.state == TS.QUEUED).distinct())
    return list(rows)


async def reconcile_lost_dispatches(
    uow: UnitOfWork, *, queue: str, band: PriorityBand, grace_seconds: float, limit: int
) -> int:
    """Re-dispatch QUEUED tasks of a (queue, band) whose stream is known to be empty.

    Precondition (checked by the caller): the stream has no entries, so no message for these tasks exists in Redis.
    Tasks with an unpublished outbox row are excluded (the relay will publish them).
    """
    lo, hi = _BAND_RANGES[band]
    pending_outbox = (
        select(OutboxEvent.id)
        .where(OutboxEvent.aggregate_id == Task.id, OutboxEvent.published_at.is_(None))
        .exists()
    )
    stmt = (
        select(Task.id, Task.project_id, Task.queue, Task.priority, Task.traceparent)
        .where(
            Task.state == TS.QUEUED,
            Task.queue == queue,
            Task.priority.between(lo, hi),
            Task.queued_at < func.now() - timedelta(seconds=grace_seconds),
            ~pending_outbox,
        )
        .order_by(Task.queued_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = (await uow.session.execute(stmt)).all()
    if not rows:
        return 0
    now = await db_now(uow.session)
    await uow.session.execute(
        insert(OutboxEvent),
        [
            outbox.dispatch_row(
                task_id=r.id,
                project_id=r.project_id,
                queue=r.queue,
                priority=r.priority,
                reason=DispatchReason.RECONCILE,
                enqueued_at=now,
                traceparent=r.traceparent,
            )
            for r in rows
        ],
    )
    await _add_events(
        uow.session,
        [
            _event(
                r.id,
                r.project_id,
                TaskEventType.RECONCILED,
                SCHEDULER,
                message="Stream entry missing (e.g. Redis data loss); task re-dispatched",
            )
            for r in rows
        ],
    )
    count = len(rows)
    uow.after_commit(lambda: metrics.TASKS_RECONCILED.inc(count))
    return count
