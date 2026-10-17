"""Read-side queries for tasks with keyset (cursor) pagination.

Keyset pagination keeps every page O(page size) — ``WHERE (sort_col, id) < (:last_value, :last_id)`` walks an index
instead of skipping OFFSET rows — and stays stable while new tasks arrive.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import orjson
from sqlalchemy import Select, String, cast, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.db.models import Task, TaskAttempt, TaskEvent, TaskLog, Worker
from flowforge_core.errors import NotFound, ValidationFailed
from flowforge_shared.states import TaskState

SortField = Literal["created_at", "updated_at", "priority"]
_SORT_COLUMNS = {
    "created_at": Task.created_at,
    "updated_at": Task.updated_at,
    "priority": Task.priority,
}
_HEX_PREFIX = re.compile(r"^[0-9a-f-]{4,36}$")
MAX_PAGE = 200


@dataclass(slots=True)
class TaskFilters:
    states: list[TaskState] = field(default_factory=list)
    task_type: str | None = None
    queue: str | None = None
    search: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    workflow_run_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    include_archived: bool = True


@dataclass(slots=True)
class Page:
    items: list[Any]
    next_cursor: str | None
    has_more: bool
    total: int | None = None


def encode_cursor(sort: str, value: Any, row_id: uuid.UUID) -> str:
    raw = orjson.dumps(
        [sort, value.isoformat() if isinstance(value, datetime) else value, str(row_id)]
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, sort: str) -> tuple[Any, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        cursor_sort, value, row_id = orjson.loads(base64.urlsafe_b64decode(padded))
        if cursor_sort != sort:
            raise ValueError("cursor belongs to a different sort order")
        if sort in ("created_at", "updated_at"):
            value = datetime.fromisoformat(value)
        elif not isinstance(value, int):
            raise ValueError("bad cursor value")
        return value, uuid.UUID(row_id)
    except (ValueError, TypeError, orjson.JSONDecodeError):
        raise ValidationFailed("invalid pagination cursor") from None


def _apply_filters[S: Select[*tuple[Any, ...]]](
    stmt: S, project_id: uuid.UUID, f: TaskFilters
) -> S:
    stmt = stmt.where(Task.project_id == project_id)
    if f.states:
        stmt = stmt.where(Task.state.in_([s.value for s in f.states]))
    if f.task_type:
        stmt = stmt.where(Task.task_type == f.task_type)
    if f.queue:
        stmt = stmt.where(Task.queue == f.queue)
    if f.created_after:
        stmt = stmt.where(Task.created_at >= f.created_after)
    if f.created_before:
        stmt = stmt.where(Task.created_at < f.created_before)
    if f.workflow_run_id:
        stmt = stmt.where(Task.workflow_run_id == f.workflow_run_id)
    if f.schedule_id:
        stmt = stmt.where(Task.schedule_id == f.schedule_id)
    if not f.include_archived:
        stmt = stmt.where(Task.archived_at.is_(None))
    if f.search:
        q = f.search.strip()[:200]
        try:
            stmt = stmt.where(Task.id == uuid.UUID(q))
        except ValueError:
            clauses = [
                Task.idempotency_key == q,
                Task.task_type.ilike(f"%{_escape_like(q)}%", escape="\\"),
            ]
            if _HEX_PREFIX.fullmatch(q.lower()):
                clauses.append(cast(Task.id, String).like(f"{q.lower()}%"))
            stmt = stmt.where(or_(*clauses))
    return stmt


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_tasks(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    filters: TaskFilters,
    sort: SortField = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 25,
    cursor: str | None = None,
    include_total: bool = False,
) -> Page:
    limit = max(1, min(limit, MAX_PAGE))
    col = _SORT_COLUMNS[sort]
    stmt = _apply_filters(
        select(Task, Worker.name.label("worker_name")).outerjoin(
            Worker, Worker.id == Task.last_worker_id
        ),
        project_id,
        filters,
    )
    if cursor:
        value, last_id = decode_cursor(cursor, sort)
        key = tuple_(col, Task.id)
        stmt = stmt.where(
            key < tuple_(value, last_id) if order == "desc" else key > tuple_(value, last_id)
        )
    ordering = (col.desc(), Task.id.desc()) if order == "desc" else (col.asc(), Task.id.asc())
    rows = (await session.execute(stmt.order_by(*ordering).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [(r[0], r[1]) for r in rows]
    next_cursor = (
        encode_cursor(sort, getattr(rows[-1][0], sort), rows[-1][0].id)
        if has_more and rows
        else None
    )
    total = None
    if include_total:
        total = int(
            await session.scalar(_apply_filters(select(func.count(Task.id)), project_id, filters))
            or 0
        )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more, total=total)


async def get_task(
    session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID
) -> tuple[Task, str | None]:
    row = (
        await session.execute(
            select(Task, Worker.name)
            .outerjoin(Worker, Worker.id == Task.last_worker_id)
            .where(Task.id == task_id, Task.project_id == project_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("task not found")
    return row[0], row[1]


async def list_attempts(
    session: AsyncSession, task_id: uuid.UUID
) -> list[tuple[TaskAttempt, str | None]]:
    rows = (
        await session.execute(
            select(TaskAttempt, Worker.name)
            .outerjoin(Worker, Worker.id == TaskAttempt.worker_id)
            .where(TaskAttempt.task_id == task_id)
            .order_by(TaskAttempt.attempt_number)
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


async def list_events(
    session: AsyncSession, task_id: uuid.UUID, limit: int = 500
) -> list[TaskEvent]:
    return list(
        (
            await session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.id)
                .limit(limit)
            )
        ).all()
    )


async def list_logs(
    session: AsyncSession, task_id: uuid.UUID, *, after_id: int = 0, limit: int = 500
) -> list[TaskLog]:
    return list(
        (
            await session.scalars(
                select(TaskLog)
                .where(TaskLog.task_id == task_id, TaskLog.id > after_id)
                .order_by(TaskLog.id)
                .limit(max(1, min(limit, 2000)))
            )
        ).all()
    )


async def distinct_task_types(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                select(Task.task_type)
                .where(Task.project_id == project_id)
                .distinct()
                .order_by(Task.task_type)
            )
        ).all()
    )
