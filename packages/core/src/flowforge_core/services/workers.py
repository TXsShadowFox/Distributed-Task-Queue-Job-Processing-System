"""Worker registry: registration, heartbeats, drain/resume and liveness. Workers are a shared, platform-level fleet;
tenants see fleet status but never other tenants' task IDs, and only platform admins may drain or resume."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.config import get_settings
from flowforge_core.context import Actor
from flowforge_core.db.models import Task, Worker
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, NotFound
from flowforge_core.events import worker_control_channel
from flowforge_core.services import audit
from flowforge_shared.messages import EventType, RealtimeEvent
from flowforge_shared.states import LEASED_TASK_STATES, WorkerStatus

WS = WorkerStatus


def worker_event(worker: Any) -> RealtimeEvent:
    return RealtimeEvent(
        type=EventType.WORKER_UPDATED,
        project_id=None,
        data={
            "id": str(worker.id),
            "name": worker.name,
            "status": worker.status,
            "active_tasks": worker.active_tasks,
            "last_heartbeat_at": worker.last_heartbeat_at.isoformat()
            if worker.last_heartbeat_at
            else None,
            "cpu_percent": worker.cpu_percent,
            "memory_rss_bytes": worker.memory_rss_bytes,
        },
    )


async def register_worker(
    uow: UnitOfWork,
    *,
    worker_id: uuid.UUID,
    name: str,
    hostname: str,
    pid: int,
    version: str,
    queues: list[str],
    task_types: list[str],
    concurrency: int,
    metadata: dict[str, Any],
) -> None:
    values = {
        "id": worker_id,
        "name": name[:200],
        "hostname": hostname[:255],
        "pid": pid,
        "version": version,
        "queues": queues,
        "task_types": task_types,
        "concurrency": concurrency,
        "status": WS.ACTIVE,
        "meta": metadata,
    }
    insert_stmt = pg_insert(Worker).values(**values)
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[Worker.id],
        set_={
            "status": WS.ACTIVE,
            "last_heartbeat_at": func.now(),
            "stopped_at": None,
            "queues": insert_stmt.excluded.queues,
            "task_types": insert_stmt.excluded.task_types,
            "concurrency": insert_stmt.excluded.concurrency,
        },
    ).returning(Worker)
    worker = (await uow.session.scalars(stmt)).one()
    uow.emit(worker_event(worker))


@dataclass(frozen=True, slots=True)
class HeartbeatReply:
    status: WorkerStatus
    drain_requested: bool


async def heartbeat(
    uow: UnitOfWork,
    *,
    worker_id: uuid.UUID,
    active_tasks: int,
    cpu_percent: float | None,
    memory_rss_bytes: int | None,
    succeeded_delta: int,
    failed_delta: int,
) -> HeartbeatReply:
    stmt = (
        update(Worker)
        .where(Worker.id == worker_id)
        .values(
            last_heartbeat_at=func.now(),
            active_tasks=active_tasks,
            cpu_percent=cpu_percent,
            memory_rss_bytes=memory_rss_bytes,
            tasks_succeeded=Worker.tasks_succeeded + succeeded_delta,
            tasks_failed=Worker.tasks_failed + failed_delta,
            # A worker the reaper marked OFFLINE (e.g. after a long pause) rejoins when it heart-beats again.
            status=case(
                (
                    Worker.status == WS.OFFLINE,
                    case((Worker.drain_requested_at.is_(None), WS.ACTIVE), else_=WS.DRAINING),
                ),
                else_=Worker.status,
            ),
        )
        .returning(Worker)
        .execution_options(synchronize_session=False)
    )
    worker = (await uow.session.scalars(stmt)).one_or_none()
    if worker is None:
        raise NotFound("worker is not registered")
    uow.emit(worker_event(worker))
    return HeartbeatReply(WorkerStatus(worker.status), worker.drain_requested_at is not None)


async def mark_stopped(uow: UnitOfWork, *, worker_id: uuid.UUID) -> None:
    worker = (
        await uow.session.scalars(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(status=WS.STOPPED, stopped_at=func.now(), active_tasks=0)
            .returning(Worker)
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if worker is not None:
        uow.emit(worker_event(worker))


async def set_draining(
    uow: UnitOfWork, *, worker_id: uuid.UUID, drain: bool, actor: Actor
) -> Worker:
    session = uow.session
    worker = await session.scalar(select(Worker).where(Worker.id == worker_id).with_for_update())
    if worker is None:
        raise NotFound("worker not found")
    if worker.status in (WS.STOPPED, WS.OFFLINE):
        raise Conflict(f"worker is {worker.status}")
    worker.drain_requested_at = func.now() if drain else None
    worker.status = WS.DRAINING if drain else WS.ACTIVE
    await session.flush()
    await session.refresh(worker)
    uow.send_control(
        worker_control_channel(str(worker_id), get_settings().redis_prefix),
        {"type": "drain" if drain else "resume"},
    )
    await audit.record(
        session,
        actor=actor,
        action="worker.drain" if drain else "worker.resume",
        resource_type="worker",
        resource_id=worker_id,
        details={"name": worker.name},
    )
    uow.emit(worker_event(worker))
    return worker


async def mark_offline(uow: UnitOfWork, *, offline_after_seconds: float) -> list[uuid.UUID]:
    rows = (
        await uow.session.scalars(
            update(Worker)
            .where(
                Worker.status.in_([WS.ACTIVE, WS.DRAINING]),
                Worker.last_heartbeat_at < func.now() - timedelta(seconds=offline_after_seconds),
            )
            .values(status=WS.OFFLINE)
            .returning(Worker)
            .execution_options(synchronize_session=False)
        )
    ).all()
    for worker in rows:
        uow.emit(worker_event(worker))
    return [w.id for w in rows]


async def list_workers(
    session: AsyncSession, *, include_stopped_since: datetime | None = None
) -> list[Worker]:
    stmt = select(Worker).order_by(Worker.status, Worker.started_at.desc())
    if include_stopped_since is not None:
        stmt = stmt.where(
            (Worker.status.in_([WS.ACTIVE, WS.DRAINING]))
            | (Worker.last_heartbeat_at >= include_stopped_since)
        )
    return list((await session.scalars(stmt)).all())


async def get_worker(session: AsyncSession, worker_id: uuid.UUID) -> Worker:
    worker = await session.get(Worker, worker_id)
    if worker is None:
        raise NotFound("worker not found")
    return worker


async def project_tasks_on_worker(
    session: AsyncSession, *, worker_id: uuid.UUID, project_id: uuid.UUID, limit: int = 50
) -> list[Task]:
    """Tasks of *this* project currently leased by the worker (other tenants' tasks are never exposed)."""
    return list(
        (
            await session.scalars(
                select(Task)
                .where(
                    Task.lease_owner == worker_id,
                    Task.project_id == project_id,
                    Task.state.in_(LEASED_TASK_STATES),
                )
                .order_by(Task.started_at)
                .limit(limit)
            )
        ).all()
    )


async def counts_by_status(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(select(Worker.status, func.count()).group_by(Worker.status))
    ).all()
    return {r[0]: int(r[1]) for r in rows}
