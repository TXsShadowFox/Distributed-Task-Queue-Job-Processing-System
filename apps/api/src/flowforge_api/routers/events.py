"""Server-Sent Events. Clients authenticate with the same Authorization header as every other request (the dashboard
uses a fetch-based EventSource), so no token ever appears in a URL. A stream ends when the access token expires; the
client refreshes and reconnects."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.realtime import Subscription
from flowforge_api.schemas.tasks import LogOut
from flowforge_api.state import AppState
from flowforge_core.observability import metrics
from flowforge_core.services import task_queries
from flowforge_shared.messages import fleet_channel, project_channel
from flowforge_shared.rbac import Permission

router = APIRouter(tags=["realtime"])

TasksRead = Annotated[ProjectContext, Depends(project_context(Permission.TASKS_READ))]
KEEPALIVE_SECONDS = 15
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def task_log_channel(task_id: uuid.UUID | str, prefix: str) -> str:
    return f"{prefix}:events:tasklog:{task_id}"


def _frame(data: Any, event: str | None = None) -> str:
    payload = data.decode() if isinstance(data, bytes) else orjson.dumps(data, default=str).decode()
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {payload}\n\n"


async def _pump(
    request: Request, state: AppState, sub: Subscription, ctx: ProjectContext, preamble: list[str]
) -> AsyncIterator[str]:
    expires_at = ctx.principal.token_expires_at
    metrics.SSE_CONNECTIONS.inc()
    try:
        yield "retry: 3000\n\n"
        for frame in preamble:
            yield frame
        while True:
            if await request.is_disconnected():
                return
            if expires_at is not None and datetime.now(UTC) >= expires_at:
                yield _frame({"type": "token_expired"}, "control")
                return
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if isinstance(item, dict):
                yield _frame(item, "control")
            else:
                yield _frame(item)
    finally:
        state.hub.unsubscribe(sub)
        metrics.SSE_CONNECTIONS.dec()


@router.get(
    "/events/stream",
    summary="Live task, workflow, schedule and worker events for the project (SSE)",
)
async def event_stream(request: Request, ctx: TasksRead, state: StateDep) -> StreamingResponse:
    prefix = state.settings.redis_prefix
    sub = state.hub.subscribe({project_channel(ctx.project_id, prefix), fleet_channel(prefix)})
    ready = _frame(
        {"type": "ready", "project_id": str(ctx.project_id), "realtime": state.hub.connected},
        "control",
    )
    return StreamingResponse(
        _pump(request, state, sub, ctx, [ready]),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/tasks/{task_id}/logs/stream", summary="Live log tail of one task (SSE, with backlog)")
async def task_log_stream(
    task_id: uuid.UUID,
    request: Request,
    ctx: TasksRead,
    state: StateDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    # Subscribe *before* reading the backlog so no line falls between the two; clients de-duplicate by log id.
    sub = state.hub.subscribe({task_log_channel(task_id, state.settings.redis_prefix)})
    try:
        async with state.db.session() as session:
            await task_queries.get_task(session, ctx.project_id, task_id)
            backlog = await task_queries.list_logs(session, task_id, after_id=after_id, limit=2000)
    except Exception:
        state.hub.unsubscribe(sub)
        raise
    preamble = (
        [
            _frame(
                {
                    "type": "task.log",
                    "task_id": str(task_id),
                    "lines": [LogOut.build(r).model_dump(mode="json") for r in backlog],
                }
            )
        ]
        if backlog
        else []
    )
    return StreamingResponse(
        _pump(request, state, sub, ctx, preamble),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
