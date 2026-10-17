"""Notification feed derived from real events (no separate notification store to drift out of sync):
dead-lettered tasks, failed workflow runs and workers that went offline. Read state is a per-user, per-project marker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.db.models import NotificationRead, Task, Worker, Workflow, WorkflowRun
from flowforge_core.db.uow import UnitOfWork

LOOKBACK = timedelta(days=7)
CATEGORIES = ("dead_letter", "workflow_failed", "worker_offline")


def enabled_categories(preferences: dict[str, Any]) -> set[str]:
    prefs = preferences.get("notifications") or {}
    return {c for c in CATEGORIES if prefs.get(c, True)}


async def feed(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    preferences: dict[str, Any],
    limit: int = 30,
) -> dict[str, Any]:
    since = datetime.now(UTC) - LOOKBACK
    categories = enabled_categories(preferences)
    items: list[dict[str, Any]] = []
    if "dead_letter" in categories:
        rows = await session.execute(
            select(Task.id, Task.task_type, Task.dead_lettered_at, Task.error)
            .where(Task.project_id == project_id, Task.dead_lettered_at >= since)
            .order_by(Task.dead_lettered_at.desc())
            .limit(limit)
        )
        items += [
            {
                "id": f"dlq:{r.id}",
                "category": "dead_letter",
                "severity": "error",
                "title": f"{r.task_type} moved to the dead-letter queue",
                "detail": ((r.error or {}).get("message") or "")[:200],
                "link": f"/tasks/{r.id}",
                "ts": r.dead_lettered_at,
            }
            for r in rows
        ]
    if "workflow_failed" in categories:
        rows = await session.execute(
            select(WorkflowRun.id, WorkflowRun.finished_at, WorkflowRun.error, Workflow.name)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.state == "FAILED",
                WorkflowRun.finished_at >= since,
            )
            .order_by(WorkflowRun.finished_at.desc())
            .limit(limit)
        )
        items += [
            {
                "id": f"wf:{r.id}",
                "category": "workflow_failed",
                "severity": "error",
                "title": f"Workflow '{r.name}' failed",
                "detail": (r.error or "")[:200],
                "link": f"/workflows/runs/{r.id}",
                "ts": r.finished_at,
            }
            for r in rows
        ]
    if "worker_offline" in categories:
        rows = await session.execute(
            select(Worker.id, Worker.name, Worker.last_heartbeat_at)
            .where(Worker.status == "OFFLINE", Worker.last_heartbeat_at >= since)
            .order_by(Worker.last_heartbeat_at.desc())
            .limit(limit)
        )
        items += [
            {
                "id": f"worker:{r.id}",
                "category": "worker_offline",
                "severity": "warning",
                "title": f"Worker {r.name} went offline",
                "detail": "Heartbeats stopped; its leased tasks were recovered by the reaper.",
                "link": "/workers",
                "ts": r.last_heartbeat_at,
            }
            for r in rows
        ]
    items.sort(key=lambda i: i["ts"], reverse=True)
    items = items[:limit]
    last_read = await session.scalar(
        select(NotificationRead.last_read_at).where(
            NotificationRead.user_id == user_id, NotificationRead.project_id == project_id
        )
    )
    for item in items:
        item["unread"] = last_read is None or item["ts"] > last_read
    return {"items": items, "unread_count": sum(1 for i in items if i["unread"])}


async def mark_all_read(uow: UnitOfWork, *, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
    stmt = pg_insert(NotificationRead).values(
        user_id=user_id, project_id=project_id, last_read_at=func.now()
    )
    await uow.session.execute(
        stmt.on_conflict_do_update(
            index_elements=[NotificationRead.user_id, NotificationRead.project_id],
            set_={"last_read_at": func.now()},
        )
    )
