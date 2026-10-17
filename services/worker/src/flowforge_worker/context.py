"""What a handler sees while it runs: identity of the attempt, a structured task logger, progress reporting and
cooperative cancellation."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from flowforge_worker.errors import TaskCancelled

MAX_LOG_LINES = 1000
MAX_LOG_MESSAGE = 4000
PROGRESS_MIN_INTERVAL = 0.5


class LogBuffer:
    """Thread-safe buffer of log rows awaiting a batched write to PostgreSQL (and live publication)."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(row)

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            rows, self._rows = self._rows, []
        return rows

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)


class TaskLogger:
    def __init__(self, task_id: uuid.UUID, attempt: int, buffer: LogBuffer) -> None:
        self._task_id = task_id
        self._attempt = attempt
        self._buffer = buffer
        self._count = 0
        self._dropped = 0
        self._lock = threading.Lock()

    def _emit(self, level: str, message: str, fields: dict[str, Any]) -> None:
        with self._lock:
            if self._count >= MAX_LOG_LINES:
                self._dropped += 1
                return
            self._count += 1
        self._buffer.append(
            {
                "task_id": self._task_id,
                "attempt_number": self._attempt,
                "level": level,
                "message": message[:MAX_LOG_MESSAGE],
                "fields": fields or None,
                "logged_at": datetime.now(UTC),
            }
        )

    def debug(self, message: str, **fields: Any) -> None:
        self._emit("DEBUG", message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit("INFO", message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit("WARNING", message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit("ERROR", message, fields)

    def finalize(self) -> None:
        with self._lock:
            dropped, self._dropped = self._dropped, 0
        if dropped:
            self._buffer.append(
                {
                    "task_id": self._task_id,
                    "attempt_number": self._attempt,
                    "level": "WARNING",
                    "message": f"{dropped} log lines dropped (limit {MAX_LOG_LINES} per attempt)",
                    "fields": None,
                    "logged_at": datetime.now(UTC),
                }
            )


ProgressSink = Callable[[uuid.UUID, uuid.UUID, int, str | None], Awaitable[None]]


class TaskContext:
    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        task_type: str,
        queue: str,
        priority: int,
        attempt: int,
        retry_count: int,
        max_retries: int,
        lease_token: uuid.UUID,
        idempotency_key: str | None,
        metadata: dict[str, Any],
        logger: TaskLogger,
        progress_sink: ProgressSink,
        workflow: dict[str, Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.project_id = project_id
        self.task_type = task_type
        self.queue = queue
        self.priority = priority
        self.attempt = attempt
        self.retry_count = retry_count
        self.max_retries = max_retries
        self.lease_token = lease_token
        self.idempotency_key = idempotency_key
        self.metadata = metadata
        self.log = logger
        self.workflow = workflow
        self._progress_sink = progress_sink
        self._last_progress = 0.0
        self._cancelled = threading.Event()

    @property
    def is_last_attempt(self) -> bool:
        return self.retry_count >= self.max_retries

    @property
    def upstream(self) -> dict[str, Any]:
        """Results of the directly upstream workflow nodes, keyed by node key (empty outside workflows)."""
        return dict((self.workflow or {}).get("upstream") or {})

    @property
    def workflow_input(self) -> dict[str, Any]:
        return dict((self.workflow or {}).get("input") or {})

    def mark_cancelled(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise TaskCancelled("cancellation requested")

    async def progress(
        self, percent: int, message: str | None = None, *, force: bool = False
    ) -> None:
        """Report progress (0-100). Throttled to two writes per second unless ``force``."""
        now = time.monotonic()
        if not force and percent < 100 and now - self._last_progress < PROGRESS_MIN_INTERVAL:
            return
        self._last_progress = now
        await self._progress_sink(
            self.task_id, self.lease_token, max(0, min(100, int(percent))), message
        )

    def sync(self, loop: asyncio.AbstractEventLoop) -> SyncTaskContext:
        return SyncTaskContext(self, loop)


class SyncTaskContext:
    """Thread-safe view of a TaskContext for ``executor='thread'`` handlers."""

    def __init__(self, ctx: TaskContext, loop: asyncio.AbstractEventLoop) -> None:
        self._ctx = ctx
        self._loop = loop
        self.task_id = ctx.task_id
        self.attempt = ctx.attempt
        self.idempotency_key = ctx.idempotency_key
        self.metadata = ctx.metadata
        self.log = ctx.log
        self.upstream = ctx.upstream
        self.workflow_input = ctx.workflow_input

    def progress(self, percent: int, message: str | None = None) -> None:
        asyncio.run_coroutine_threadsafe(self._ctx.progress(percent, message), self._loop)

    def is_cancelled(self) -> bool:
        return self._ctx.is_cancelled()

    def check_cancelled(self) -> None:
        self._ctx.check_cancelled()
