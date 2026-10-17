from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import orjson
from fastapi import APIRouter, Depends, Header, Query, Response

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.tasks import (
    AttemptOut,
    BatchItemOut,
    BatchOut,
    BulkIdsIn,
    BulkResultItem,
    BulkResultOut,
    EventOut,
    LogListOut,
    LogOut,
    TaskBatchSubmit,
    TaskListOut,
    TaskOut,
    TaskSubmit,
    TaskSummaryOut,
    TaskTypeOut,
)
from flowforge_api.state import AppState
from flowforge_core.errors import FlowForgeError, PayloadTooLarge, ValidationFailed
from flowforge_core.observability.tracing import current_traceparent
from flowforge_core.services import task_queries
from flowforge_core.services import tasks as task_service
from flowforge_core.services.tasks import TaskSpec
from flowforge_shared.rbac import Permission
from flowforge_shared.states import TaskState

router = APIRouter(prefix="/tasks", tags=["tasks"])
types_router = APIRouter(prefix="/task-types", tags=["tasks"])
dlq_router = APIRouter(prefix="/dead-letter", tags=["dead-letter queue"])

TasksRead = Annotated[ProjectContext, Depends(project_context(Permission.TASKS_READ))]
TasksWrite = Annotated[ProjectContext, Depends(project_context(Permission.TASKS_WRITE))]


async def build_spec(
    state: AppState, body: TaskSubmit, header_key: str | None, *, index: int | None = None
) -> TaskSpec:
    loc = f"tasks[{index}]." if index is not None else ""
    entry = await state.catalog.require(body.task_type)
    if len(orjson.dumps(body.payload)) > state.settings.max_payload_bytes:
        raise PayloadTooLarge(f"{loc}payload exceeds {state.settings.max_payload_bytes} bytes")
    entry.validate_payload(body.payload, location=f"{loc}payload")
    run_at = body.run_at
    if body.delay_seconds:
        run_at = datetime.now(UTC) + timedelta(seconds=body.delay_seconds)
    base = body.retry_backoff_seconds if body.retry_backoff_seconds is not None else 2.0
    cap = body.retry_backoff_max_seconds if body.retry_backoff_max_seconds is not None else 300.0
    if cap < base:
        raise ValidationFailed(f"{loc}retry_backoff_max_seconds must be >= retry_backoff_seconds")
    return TaskSpec(
        task_type=body.task_type,
        payload=body.payload,
        queue=body.queue or entry.queue,
        priority=body.priority if body.priority is not None else entry.default_priority,
        max_retries=body.max_retries if body.max_retries is not None else entry.default_max_retries,
        timeout_seconds=body.timeout_seconds or entry.default_timeout_seconds,
        backoff_base_seconds=base,
        backoff_max_seconds=cap,
        dead_letter=body.dead_letter,
        run_at=run_at,
        idempotency_key=body.idempotency_key or header_key,
        metadata=body.metadata,
    )


@router.post(
    "",
    status_code=201,
    response_model=TaskOut,
    responses={
        200: {"model": TaskOut, "description": "Idempotent replay: the task already existed"},
        **error_responses(401, 403, 409, 413, 422, 429),
    },
    summary="Submit a task",
)
async def submit_task(
    body: TaskSubmit,
    response: Response,
    ctx: TasksWrite,
    state: StateDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> TaskOut:
    spec = await build_spec(state, body, idempotency_key)
    async with state.db.transaction() as uow:
        [result] = await task_service.submit_tasks(
            uow,
            project_id=ctx.project_id,
            specs=[spec],
            actor=ctx.actor,
            traceparent=current_traceparent(),
        )
    if not result.created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    response.headers["Location"] = f"/api/v1/tasks/{result.task.id}"
    return TaskOut.from_task(result.task)


@router.post(
    "/batch",
    status_code=201,
    response_model=BatchOut,
    responses=error_responses(401, 403, 409, 413, 422, 429),
    summary="Submit up to 1,000 tasks atomically",
)
async def submit_batch(body: TaskBatchSubmit, ctx: TasksWrite, state: StateDep) -> BatchOut:
    specs = []
    errors = []
    for i, item in enumerate(body.tasks):
        try:
            specs.append(await build_spec(state, item, None, index=i))
        except FlowForgeError as exc:
            errors.append(
                {"index": i, "code": exc.code, "message": exc.message, "details": exc.details}
            )
    if errors:
        raise ValidationFailed(
            "one or more tasks are invalid; nothing was submitted", details=errors[:50]
        )
    async with state.db.transaction() as uow:
        results = await task_service.submit_tasks(
            uow,
            project_id=ctx.project_id,
            specs=specs,
            actor=ctx.actor,
            traceparent=current_traceparent(),
        )
    items = [
        BatchItemOut(index=i, id=r.task.id, state=r.task.state, created=r.created)
        for i, r in enumerate(results)
    ]
    created = sum(1 for r in results if r.created)
    return BatchOut(items=items, created=created, replayed=len(results) - created)


@router.get("", response_model=TaskListOut, responses=error_responses(401, 403, 422))
async def list_tasks(
    ctx: TasksRead,
    state: StateDep,
    state_filter: Annotated[list[TaskState] | None, Query(alias="state")] = None,
    task_type: Annotated[str | None, Query(max_length=100)] = None,
    queue: Annotated[str | None, Query(max_length=63)] = None,
    q: Annotated[
        str | None, Query(max_length=200, description="Task ID, ID prefix, idempotency key or type")
    ] = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    workflow_run_id: uuid.UUID | None = None,
    schedule_id: uuid.UUID | None = None,
    sort: Literal["created_at", "updated_at", "priority"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    include_total: bool = False,
) -> TaskListOut:
    filters = task_queries.TaskFilters(
        states=list(state_filter or []),
        task_type=task_type,
        queue=queue,
        search=q,
        created_after=created_after,
        created_before=created_before,
        workflow_run_id=workflow_run_id,
        schedule_id=schedule_id,
    )
    async with state.db.session() as session:
        page = await task_queries.list_tasks(
            session,
            project_id=ctx.project_id,
            filters=filters,
            sort=sort,
            order=order,
            limit=limit,
            cursor=cursor,
            include_total=include_total,
        )
    return TaskListOut(
        items=[TaskSummaryOut.build(t, w) for t, w in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        total=page.total,
    )


@router.get("/{task_id}", response_model=TaskOut, responses=error_responses(401, 403, 404))
async def get_task(task_id: uuid.UUID, ctx: TasksRead, state: StateDep) -> TaskOut:
    async with state.db.session() as session:
        task, worker_name = await task_queries.get_task(session, ctx.project_id, task_id)
    return TaskOut.from_task(task, worker_name)


@router.post(
    "/{task_id}/cancel", response_model=TaskOut, responses=error_responses(401, 403, 404, 409)
)
async def cancel_task(task_id: uuid.UUID, ctx: TasksWrite, state: StateDep) -> TaskOut:
    async with state.db.transaction() as uow:
        task = await task_service.cancel_task(
            uow,
            project_id=ctx.project_id,
            task_id=task_id,
            actor=ctx.actor,
            organization_id=ctx.organization_id,
        )
    return TaskOut.from_task(task)


@router.post(
    "/{task_id}/retry", response_model=TaskOut, responses=error_responses(401, 403, 404, 409)
)
async def retry_task(task_id: uuid.UUID, ctx: TasksWrite, state: StateDep) -> TaskOut:
    async with state.db.transaction() as uow:
        task = await task_service.retry_task(
            uow,
            project_id=ctx.project_id,
            task_id=task_id,
            actor=ctx.actor,
            organization_id=ctx.organization_id,
        )
    return TaskOut.from_task(task)


@router.get(
    "/{task_id}/attempts", response_model=list[AttemptOut], responses=error_responses(401, 403, 404)
)
async def list_attempts(task_id: uuid.UUID, ctx: TasksRead, state: StateDep) -> list[AttemptOut]:
    async with state.db.session() as session:
        await task_queries.get_task(session, ctx.project_id, task_id)
        attempts = await task_queries.list_attempts(session, task_id)
    return [AttemptOut.build(a, w) for a, w in attempts]


@router.get(
    "/{task_id}/events", response_model=list[EventOut], responses=error_responses(401, 403, 404)
)
async def list_events(task_id: uuid.UUID, ctx: TasksRead, state: StateDep) -> list[EventOut]:
    async with state.db.session() as session:
        await task_queries.get_task(session, ctx.project_id, task_id)
        events = await task_queries.list_events(session, task_id)
    return [EventOut.build(e) for e in events]


@router.get("/{task_id}/logs", response_model=LogListOut, responses=error_responses(401, 403, 404))
async def list_logs(
    task_id: uuid.UUID,
    ctx: TasksRead,
    state: StateDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> LogListOut:
    async with state.db.session() as session:
        await task_queries.get_task(session, ctx.project_id, task_id)
        logs = await task_queries.list_logs(session, task_id, after_id=after_id, limit=limit)
    return LogListOut(
        items=[LogOut.build(r) for r in logs], next_after_id=logs[-1].id if logs else after_id
    )


@types_router.get("", response_model=list[TaskTypeOut], summary="Task types registered by workers")
async def list_task_types(_: TasksRead, state: StateDep) -> list[TaskTypeOut]:
    entries = await state.catalog.all()
    return [
        TaskTypeOut(
            name=e.name,
            description=e.description,
            queue=e.queue,
            payload_schema=e.payload_schema,
            example_payload=e.example_payload,
            default_priority=e.default_priority,
            default_max_retries=e.default_max_retries,
            default_timeout_seconds=e.default_timeout_seconds,
            executor=e.executor,
        )
        for e in entries
    ]


# ------------------------------------------------------------------------------------------------ dead letter queue
@dlq_router.get("", response_model=TaskListOut, summary="Dead-lettered tasks")
async def list_dead_letter(
    ctx: TasksRead,
    state: StateDep,
    task_type: Annotated[str | None, Query(max_length=100)] = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> TaskListOut:
    filters = task_queries.TaskFilters(
        states=[TaskState.DEAD_LETTERED], task_type=task_type, include_archived=include_archived
    )
    async with state.db.session() as session:
        page = await task_queries.list_tasks(
            session,
            project_id=ctx.project_id,
            filters=filters,
            sort="updated_at",
            limit=limit,
            cursor=cursor,
            include_total=True,
        )
    return TaskListOut(
        items=[TaskSummaryOut.build(t, w) for t, w in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        total=page.total,
    )


@dlq_router.post(
    "/{task_id}/retry", response_model=TaskOut, responses=error_responses(401, 403, 404, 409)
)
async def retry_dead_letter(task_id: uuid.UUID, ctx: TasksWrite, state: StateDep) -> TaskOut:
    return await retry_task(task_id, ctx, state)


@dlq_router.post(
    "/{task_id}/archive", response_model=TaskOut, responses=error_responses(401, 403, 404, 409)
)
async def archive_dead_letter(task_id: uuid.UUID, ctx: TasksWrite, state: StateDep) -> TaskOut:
    async with state.db.transaction() as uow:
        task = await task_service.archive_task(
            uow,
            project_id=ctx.project_id,
            task_id=task_id,
            actor=ctx.actor,
            organization_id=ctx.organization_id,
        )
    return TaskOut.from_task(task)


async def _bulk(
    ctx: ProjectContext, state: StateDep, body: BulkIdsIn, action: str
) -> BulkResultOut:
    """Each task in its own transaction: one bad task does not block the others; results report per-task errors."""
    results: list[BulkResultItem] = []
    for task_id in body.task_ids:
        try:
            async with state.db.transaction() as uow:
                if action == "retry":
                    await task_service.retry_task(
                        uow,
                        project_id=ctx.project_id,
                        task_id=task_id,
                        actor=ctx.actor,
                        organization_id=ctx.organization_id,
                    )
                else:
                    await task_service.archive_task(
                        uow,
                        project_id=ctx.project_id,
                        task_id=task_id,
                        actor=ctx.actor,
                        organization_id=ctx.organization_id,
                    )
            results.append(BulkResultItem(id=task_id, ok=True))
        except FlowForgeError as exc:
            results.append(BulkResultItem(id=task_id, ok=False, error=exc.message))
    ok = sum(1 for r in results if r.ok)
    return BulkResultOut(results=results, succeeded=ok, failed=len(results) - ok)


@dlq_router.post(
    "/retry", response_model=BulkResultOut, summary="Retry several dead-lettered tasks"
)
async def bulk_retry(body: BulkIdsIn, ctx: TasksWrite, state: StateDep) -> BulkResultOut:
    return await _bulk(ctx, state, body, "retry")


@dlq_router.post(
    "/archive", response_model=BulkResultOut, summary="Archive several dead-lettered tasks"
)
async def bulk_archive(body: BulkIdsIn, ctx: TasksWrite, state: StateDep) -> BulkResultOut:
    return await _bulk(ctx, state, body, "archive")
