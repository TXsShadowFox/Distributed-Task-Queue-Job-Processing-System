from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from flowforge_api.deps import (
    Principal,
    ProjectContext,
    StateDep,
    project_context,
    require_platform_admin,
)
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.platform import WorkerDetailOut, WorkerOut
from flowforge_api.schemas.tasks import TaskSummaryOut
from flowforge_core.services import workers as worker_service
from flowforge_shared.rbac import Permission

router = APIRouter(prefix="/workers", tags=["workers"])

WorkersRead = Annotated[ProjectContext, Depends(project_context(Permission.WORKERS_READ))]
PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]


@router.get(
    "", response_model=list[WorkerOut], summary="Worker fleet (live workers plus those seen in 24h)"
)
async def list_workers(
    _: WorkersRead, state: StateDep, include_stopped: bool = True
) -> list[WorkerOut]:
    since = datetime.now(UTC) - timedelta(hours=24) if include_stopped else datetime.now(UTC)
    async with state.db.session() as session:
        workers = await worker_service.list_workers(session, include_stopped_since=since)
    return [WorkerOut.build(w) for w in workers]


@router.get(
    "/{worker_id}", response_model=WorkerDetailOut, responses=error_responses(401, 403, 404)
)
async def get_worker(worker_id: uuid.UUID, ctx: WorkersRead, state: StateDep) -> WorkerDetailOut:
    async with state.db.session() as session:
        worker = await worker_service.get_worker(session, worker_id)
        tasks = (
            await worker_service.project_tasks_on_worker(
                session, worker_id=worker_id, project_id=ctx.project_id
            )
            if ctx.has(Permission.TASKS_READ)
            else []
        )
    return WorkerDetailOut(
        **WorkerOut.build(worker).model_dump(),
        project_tasks=[TaskSummaryOut.build(t, worker.name) for t in tasks],
    )


@router.post(
    "/{worker_id}/drain",
    response_model=WorkerOut,
    responses=error_responses(401, 403, 404, 409),
    summary="Stop the worker from taking new tasks; running tasks finish normally (platform admins only)",
)
async def drain_worker(
    worker_id: uuid.UUID, principal: PlatformAdmin, state: StateDep
) -> WorkerOut:
    async with state.db.transaction() as uow:
        worker = await worker_service.set_draining(
            uow, worker_id=worker_id, drain=True, actor=principal.actor
        )
    return WorkerOut.build(worker)


@router.post(
    "/{worker_id}/resume",
    response_model=WorkerOut,
    responses=error_responses(401, 403, 404, 409),
    summary="Let a draining worker take new tasks again (platform admins only)",
)
async def resume_worker(
    worker_id: uuid.UUID, principal: PlatformAdmin, state: StateDep
) -> WorkerOut:
    async with state.db.transaction() as uow:
        worker = await worker_service.set_draining(
            uow, worker_id=worker_id, drain=False, actor=principal.actor
        )
    return WorkerOut.build(worker)
