"""Recurring (cron) schedules.

Exactly-once firing per fire time is enforced by the database, not by process coordination: each fire submits a task
with idempotency key ``schedule:<id>:<fire time>`` (and a unique ``(schedule_id, schedule_fire_time)`` index), and
schedule rows are claimed with ``FOR UPDATE SKIP LOCKED``. Restarting the scheduler, or running several instances,
cannot double-fire.

* Misfire policy (fire times missed while the scheduler was down): ``RUN_ONCE`` fires once for the most recent missed
  time; ``SKIP`` drops fire times that are more than ``MISFIRE_GRACE`` late.
* Overlap policy: ``SKIP`` does not fire while the previous run's task is still active.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.context import SCHEDULER, Actor
from flowforge_core.db.models import Schedule, Task
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, NotFound, ValidationFailed
from flowforge_core.observability import metrics
from flowforge_core.services import audit
from flowforge_core.services import tasks as task_service
from flowforge_core.services.task_types import TaskTypeCatalog
from flowforge_shared.cron import (
    CronError,
    next_fire_time,
    next_fire_times,
    validate_cron,
    validate_timezone,
)
from flowforge_shared.messages import DispatchReason, EventType, RealtimeEvent
from flowforge_shared.priority import MAX_PRIORITY, MIN_PRIORITY, validate_queue_name
from flowforge_shared.states import (
    TERMINAL_TASK_STATES,
    MisfirePolicy,
    OverlapPolicy,
    ScheduleStatus,
    TaskState,
)

MISFIRE_GRACE = timedelta(seconds=60)
MAX_MISSED_SCAN = 10_000


@dataclass(slots=True)
class ScheduleInput:
    name: str
    cron_expression: str
    timezone: str
    task_type: str
    payload: dict[str, Any]
    description: str | None = None
    queue: str | None = None
    priority: int | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None
    misfire_policy: MisfirePolicy = MisfirePolicy.RUN_ONCE
    overlap_policy: OverlapPolicy = OverlapPolicy.ALLOW
    paused: bool = False


def schedule_event(schedule: Schedule) -> RealtimeEvent:
    return RealtimeEvent(
        type=EventType.SCHEDULE_UPDATED,
        project_id=schedule.project_id,
        data={
            "id": str(schedule.id),
            "status": schedule.status,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        },
    )


def _validate_common(data: ScheduleInput) -> tuple[str, str]:
    try:
        expr = validate_cron(data.cron_expression)
        validate_timezone(data.timezone)
    except CronError as exc:
        raise ValidationFailed(str(exc)) from None
    if data.queue is not None:
        try:
            validate_queue_name(data.queue)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from None
    if data.priority is not None and not MIN_PRIORITY <= data.priority <= MAX_PRIORITY:
        raise ValidationFailed("priority must be between 0 and 9")
    return expr, data.timezone


async def create_schedule(
    uow: UnitOfWork,
    *,
    catalog: TaskTypeCatalog,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    data: ScheduleInput,
    actor: Actor,
) -> Schedule:
    expr, tz = _validate_common(data)
    entry = await catalog.require(data.task_type)
    entry.validate_payload(data.payload)
    now = await task_service.db_now(uow.session)
    schedule = Schedule(
        project_id=project_id,
        name=data.name.strip(),
        description=data.description,
        cron_expression=expr,
        timezone=tz,
        task_type=data.task_type,
        payload=data.payload,
        queue=data.queue or entry.queue,
        priority=data.priority if data.priority is not None else entry.default_priority,
        max_retries=data.max_retries if data.max_retries is not None else entry.default_max_retries,
        timeout_seconds=data.timeout_seconds or entry.default_timeout_seconds,
        status=ScheduleStatus.PAUSED if data.paused else ScheduleStatus.ACTIVE,
        misfire_policy=data.misfire_policy,
        overlap_policy=data.overlap_policy,
        next_run_at=next_fire_time(expr, tz, now),
        created_by_user_id=actor.user_id,
    )
    uow.session.add(schedule)
    try:
        await uow.session.flush()
    except IntegrityError:
        raise Conflict(f"a schedule named {data.name!r} already exists") from None
    await audit.record(
        uow.session,
        actor=actor,
        action="schedule.create",
        resource_type="schedule",
        resource_id=schedule.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"name": schedule.name, "cron": expr, "timezone": tz},
    )
    uow.emit(schedule_event(schedule))
    return schedule


async def get_schedule(
    session: AsyncSession, project_id: uuid.UUID, schedule_id: uuid.UUID
) -> Schedule:
    schedule = await session.scalar(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.project_id == project_id)
    )
    if schedule is None:
        raise NotFound("schedule not found")
    return schedule


async def list_schedules(session: AsyncSession, project_id: uuid.UUID) -> list[Schedule]:
    return list(
        (
            await session.scalars(
                select(Schedule).where(Schedule.project_id == project_id).order_by(Schedule.name)
            )
        ).all()
    )


async def update_schedule(
    uow: UnitOfWork,
    *,
    catalog: TaskTypeCatalog,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    changes: dict[str, Any],
    expected_version: int | None,
    actor: Actor,
) -> Schedule:
    """Partial update with optimistic concurrency (``expected_version``) for concurrent dashboard edits."""
    session = uow.session
    schedule = await session.scalar(
        select(Schedule)
        .where(Schedule.id == schedule_id, Schedule.project_id == project_id)
        .with_for_update()
    )
    if schedule is None:
        raise NotFound("schedule not found")
    if expected_version is not None and expected_version != schedule.version:
        raise Conflict(
            "schedule was modified by someone else; reload and try again",
            details={"current_version": schedule.version},
        )
    merged = ScheduleInput(
        name=changes.get("name", schedule.name),
        cron_expression=changes.get("cron_expression", schedule.cron_expression),
        timezone=changes.get("timezone", schedule.timezone),
        task_type=changes.get("task_type", schedule.task_type),
        payload=changes.get("payload", schedule.payload),
        description=changes.get("description", schedule.description),
        queue=changes.get("queue", schedule.queue),
        priority=changes.get("priority", schedule.priority),
        max_retries=changes.get("max_retries", schedule.max_retries),
        timeout_seconds=changes.get("timeout_seconds", schedule.timeout_seconds),
        misfire_policy=MisfirePolicy(changes.get("misfire_policy", schedule.misfire_policy)),
        overlap_policy=OverlapPolicy(changes.get("overlap_policy", schedule.overlap_policy)),
        paused=changes.get("paused", schedule.status == ScheduleStatus.PAUSED),
    )
    expr, tz = _validate_common(merged)
    if "task_type" in changes or "payload" in changes:
        entry = await catalog.require(merged.task_type)
        entry.validate_payload(merged.payload)
    timing_changed = expr != schedule.cron_expression or tz != schedule.timezone
    resuming = schedule.status == ScheduleStatus.PAUSED and not merged.paused
    schedule.name = merged.name.strip()
    schedule.description = merged.description
    schedule.cron_expression = expr
    schedule.timezone = tz
    schedule.task_type = merged.task_type
    schedule.payload = merged.payload
    schedule.queue = merged.queue or schedule.queue
    schedule.priority = merged.priority if merged.priority is not None else schedule.priority
    schedule.max_retries = (
        merged.max_retries if merged.max_retries is not None else schedule.max_retries
    )
    schedule.timeout_seconds = merged.timeout_seconds or schedule.timeout_seconds
    schedule.misfire_policy = merged.misfire_policy
    schedule.overlap_policy = merged.overlap_policy
    schedule.status = ScheduleStatus.PAUSED if merged.paused else ScheduleStatus.ACTIVE
    if timing_changed or resuming:
        # Resuming does not replay fire times missed while paused.
        schedule.next_run_at = next_fire_time(expr, tz, await task_service.db_now(session))
    schedule.version += 1
    try:
        await session.flush()
    except IntegrityError:
        raise Conflict("a schedule with this name already exists") from None
    await session.refresh(schedule)
    await audit.record(
        session,
        actor=actor,
        action="schedule.update",
        resource_type="schedule",
        resource_id=schedule.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"changes": sorted(changes)},
    )
    uow.emit(schedule_event(schedule))
    return schedule


async def delete_schedule(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    actor: Actor,
) -> None:
    schedule = await get_schedule(uow.session, project_id, schedule_id)
    await uow.session.delete(schedule)
    await audit.record(
        uow.session,
        actor=actor,
        action="schedule.delete",
        resource_type="schedule",
        resource_id=schedule_id,
        organization_id=organization_id,
        project_id=project_id,
        details={"name": schedule.name},
    )


def _fire_counter(outcome: str) -> Callable[[], None]:
    def inc() -> None:
        metrics.SCHEDULE_FIRES.labels(outcome).inc()

    return inc


def preview(expression: str, timezone: str, after: datetime, count: int = 5) -> list[datetime]:
    try:
        return next_fire_times(expression, timezone, after, count)
    except CronError as exc:
        raise ValidationFailed(str(exc)) from None


def _latest_missed(expr: str, tz: str, first_due: datetime, now: datetime) -> tuple[datetime, int]:
    """Most recent fire time <= now, starting from the first due fire time; plus how many fire times were due."""
    latest = first_due
    count = 1
    cursor = first_due
    for _ in range(MAX_MISSED_SCAN):
        nxt = next_fire_time(expr, tz, cursor)
        if nxt > now:
            break
        latest, cursor, count = nxt, nxt, count + 1
    return latest, count


async def fire_due_schedules(uow: UnitOfWork, *, limit: int) -> int:
    """Called by the scheduler loop. Returns the number of tasks created."""
    session = uow.session
    due = (
        await session.scalars(
            select(Schedule)
            .where(Schedule.status == ScheduleStatus.ACTIVE, Schedule.next_run_at <= func.now())
            .order_by(Schedule.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not due:
        return 0
    now = await task_service.db_now(session)
    created = 0
    for schedule in due:
        if schedule.next_run_at is None:  # excluded by the query; defensive for mypy
            continue
        fire_time, due_count = _latest_missed(
            schedule.cron_expression, schedule.timezone, schedule.next_run_at, now
        )
        outcome = "fired"
        if schedule.misfire_policy == MisfirePolicy.SKIP and now - fire_time > MISFIRE_GRACE:
            outcome = "misfire_skipped"
        elif schedule.overlap_policy == OverlapPolicy.SKIP and schedule.last_task_id is not None:
            previous_state = await session.scalar(
                select(Task.state).where(Task.id == schedule.last_task_id)
            )
            if previous_state is not None and TaskState(previous_state) not in TERMINAL_TASK_STATES:
                outcome = "overlap_skipped"
        if outcome == "fired":
            spec = task_service.TaskSpec(
                task_type=schedule.task_type,
                payload=schedule.payload,
                queue=schedule.queue,
                priority=schedule.priority,
                max_retries=schedule.max_retries,
                timeout_seconds=schedule.timeout_seconds,
                idempotency_key=f"schedule:{schedule.id}:{fire_time.isoformat()}",
                metadata={"schedule_id": str(schedule.id), "schedule_name": schedule.name},
            )
            [result] = await task_service.submit_tasks(
                uow,
                project_id=schedule.project_id,
                specs=[spec],
                actor=SCHEDULER,
                reason=DispatchReason.SCHEDULE,
                origins=[
                    task_service.SubmitOrigin(schedule_id=schedule.id, schedule_fire_time=fire_time)
                ],
            )
            if result.created:
                created += 1
                schedule.total_runs += 1
            schedule.last_task_id = result.task.id
            schedule.last_run_at = fire_time
        else:
            schedule.skipped_runs += 1
        if due_count > 1:  # older missed fire times collapse into this one
            schedule.skipped_runs += due_count - 1
        schedule.next_run_at = next_fire_time(schedule.cron_expression, schedule.timezone, now)
        schedule.version += 1
        uow.after_commit(_fire_counter(outcome))
        uow.emit(schedule_event(schedule))
    await session.flush()
    return created
