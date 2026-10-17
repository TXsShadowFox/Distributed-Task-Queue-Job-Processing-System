"""Transactional outbox. Rows are inserted in the same transaction as the state change they announce; the relay in the
scheduler publishes them to Redis Streams. A crash between XADD and marking the row published causes a duplicate
stream entry, which workers discard because the task is no longer QUEUED (at-least-once, idempotent claim)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.config import get_settings
from flowforge_core.db.models import OutboxEvent
from flowforge_shared.messages import DispatchMessage, DispatchReason
from flowforge_shared.priority import band_for_priority, stream_key

DISPATCH_EVENT = "task.dispatch"


def dispatch_row(
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    queue: str,
    priority: int,
    reason: DispatchReason,
    enqueued_at: datetime,
    traceparent: str | None,
) -> dict[str, Any]:
    msg = DispatchMessage(
        task_id=task_id,
        project_id=project_id,
        queue=queue,
        priority=priority,
        reason=reason,
        enqueued_at=enqueued_at,
        traceparent=traceparent,
    )
    return {
        "event_type": DISPATCH_EVENT,
        "aggregate_type": "task",
        "aggregate_id": task_id,
        "stream": stream_key(queue, band_for_priority(priority), get_settings().redis_prefix),
        "payload": msg.to_fields(),
    }


async def claim_batch(session: AsyncSession, limit: int) -> list[OutboxEvent]:
    """Lock a batch of unpublished events. SKIP LOCKED lets several relays run concurrently without blocking."""
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def mark_published(session: AsyncSession, ids: list[int]) -> None:
    if ids:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(ids))
            .values(published_at=func.now(), publish_attempts=OutboxEvent.publish_attempts + 1)
            .execution_options(synchronize_session=False)
        )


async def backlog(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
        )
        or 0
    )


async def purge_published(session: AsyncSession, older_than_hours: int, limit: int = 10_000) -> int:
    ids = (
        select(OutboxEvent.id)
        .where(
            OutboxEvent.published_at.is_not(None),
            OutboxEvent.published_at
            < func.now() - func.make_interval(0, 0, 0, 0, older_than_hours),
        )
        .limit(limit)
        .scalar_subquery()
    )
    result = await session.execute(
        delete(OutboxEvent)
        .where(OutboxEvent.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
