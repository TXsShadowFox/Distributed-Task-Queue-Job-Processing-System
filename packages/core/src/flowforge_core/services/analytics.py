"""Analytics computed directly from task, attempt and worker rows — every number shown in the dashboard is real.

Time series use ``date_bin`` buckets with ``generate_series`` gap-filling; latency percentiles use
``percentile_cont`` over attempt durations (execution) and ``queue_wait_ms`` (time from QUEUED to claimed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Window = Literal["1h", "6h", "24h", "7d", "30d"]


@dataclass(frozen=True, slots=True)
class WindowSpec:
    span: timedelta
    bucket: timedelta


WINDOWS: dict[str, WindowSpec] = {
    "1h": WindowSpec(timedelta(hours=1), timedelta(minutes=1)),
    "6h": WindowSpec(timedelta(hours=6), timedelta(minutes=5)),
    "24h": WindowSpec(timedelta(hours=24), timedelta(minutes=15)),
    "7d": WindowSpec(timedelta(days=7), timedelta(hours=2)),
    "30d": WindowSpec(timedelta(days=30), timedelta(hours=12)),
}


def _ms(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


async def overview(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    p = {"pid": project_id}
    by_state = {
        r.state: int(r.n)
        for r in (
            await session.execute(
                text(
                    "SELECT state, count(*) AS n FROM tasks WHERE project_id = :pid GROUP BY state"
                ),
                p,
            )
        ).all()
    }
    last24 = (
        await session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE state = 'SUCCEEDED') AS succeeded,
                  count(*) FILTER (WHERE state IN ('FAILED', 'DEAD_LETTERED')) AS failed,
                  count(*) FILTER (WHERE state = 'CANCELLED') AS cancelled
                FROM tasks
                WHERE project_id = :pid AND finished_at >= now() - interval '24 hours'
                """
            ),
            p,
        )
    ).one()
    attempts24 = (
        await session.execute(
            text(
                """
                SELECT
                  avg(a.duration_ms) FILTER (WHERE a.status = 'SUCCEEDED') AS avg_duration_ms,
                  count(*) FILTER (WHERE a.attempt_number > 1) AS retries
                FROM task_attempts a JOIN tasks t ON t.id = a.task_id
                WHERE t.project_id = :pid AND a.started_at >= now() - interval '24 hours'
                """
            ),
            p,
        )
    ).one()
    succeeded_1h = await session.scalar(
        text(
            "SELECT count(*) FROM tasks WHERE project_id = :pid AND state = 'SUCCEEDED' "
            "AND finished_at >= now() - interval '1 hour'"
        ),
        p,
    )
    queues = [
        {"queue": r.queue, "queued": int(r.n), "oldest_queued_at": r.oldest}
        for r in (
            await session.execute(
                text(
                    "SELECT queue, count(*) AS n, min(queued_at) AS oldest FROM tasks "
                    "WHERE project_id = :pid AND state = 'QUEUED' GROUP BY queue ORDER BY queue"
                ),
                p,
            )
        ).all()
    ]
    workers = (
        await session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE status = 'ACTIVE') AS active,
                  count(*) FILTER (WHERE status = 'DRAINING') AS draining,
                  count(*) FILTER (WHERE status = 'OFFLINE' AND last_heartbeat_at >= now() - interval '24 hours')
                    AS offline,
                  coalesce(sum(concurrency) FILTER (WHERE status = 'ACTIVE'), 0) AS capacity,
                  coalesce(sum(active_tasks) FILTER (WHERE status IN ('ACTIVE', 'DRAINING')), 0) AS busy
                FROM workers
                """
            )
        )
    ).one()
    dlq_open = await session.scalar(
        text(
            "SELECT count(*) FROM tasks WHERE project_id = :pid AND state = 'DEAD_LETTERED' "
            "AND archived_at IS NULL"
        ),
        p,
    )
    finished = int(last24.succeeded) + int(last24.failed)
    return {
        "tasks_by_state": by_state,
        "total_tasks": sum(by_state.values()),
        "running": by_state.get("RUNNING", 0) + by_state.get("CANCEL_REQUESTED", 0),
        "queued": by_state.get("QUEUED", 0),
        "scheduled": by_state.get("SCHEDULED", 0) + by_state.get("RETRY_SCHEDULED", 0),
        "succeeded_24h": int(last24.succeeded),
        "failed_24h": int(last24.failed),
        "cancelled_24h": int(last24.cancelled),
        "success_rate_24h": round(int(last24.succeeded) / finished, 4) if finished else None,
        "retries_24h": int(attempts24.retries or 0),
        "avg_duration_ms_24h": _ms(attempts24.avg_duration_ms),
        "throughput_per_min_1h": round(int(succeeded_1h or 0) / 60, 3),
        "queue_depth": queues,
        "dead_letter_open": int(dlq_open or 0),
        "workers": {
            "active": int(workers.active),
            "draining": int(workers.draining),
            "offline": int(workers.offline),
            "capacity": int(workers.capacity),
            "busy_slots": int(workers.busy),
        },
    }


async def throughput(session: AsyncSession, project_id: uuid.UUID, window: str) -> dict[str, Any]:
    spec = WINDOWS[window]
    rows = (
        await session.execute(
            text(
                """
                WITH bounds AS (
                  SELECT date_bin(CAST(:bucket AS interval), now() - CAST(:span AS interval), timestamptz '2000-01-01') AS start_at
                ),
                buckets AS (
                  SELECT generate_series((SELECT start_at FROM bounds), now(), CAST(:bucket AS interval)) AS ts
                ),
                submitted AS (
                  SELECT date_bin(CAST(:bucket AS interval), created_at, timestamptz '2000-01-01') AS ts, count(*) AS n
                  FROM tasks WHERE project_id = :pid AND created_at >= (SELECT start_at FROM bounds)
                  GROUP BY 1
                ),
                finished AS (
                  SELECT date_bin(CAST(:bucket AS interval), finished_at, timestamptz '2000-01-01') AS ts,
                         count(*) FILTER (WHERE state = 'SUCCEEDED') AS succeeded,
                         count(*) FILTER (WHERE state IN ('FAILED', 'DEAD_LETTERED')) AS failed,
                         count(*) FILTER (WHERE state = 'CANCELLED') AS cancelled
                  FROM tasks WHERE project_id = :pid AND finished_at >= (SELECT start_at FROM bounds)
                  GROUP BY 1
                ),
                retried AS (
                  SELECT date_bin(CAST(:bucket AS interval), a.started_at, timestamptz '2000-01-01') AS ts, count(*) AS n
                  FROM task_attempts a JOIN tasks t ON t.id = a.task_id
                  WHERE t.project_id = :pid AND a.attempt_number > 1
                    AND a.started_at >= (SELECT start_at FROM bounds)
                  GROUP BY 1
                )
                SELECT b.ts,
                       coalesce(s.n, 0) AS submitted,
                       coalesce(f.succeeded, 0) AS succeeded,
                       coalesce(f.failed, 0) AS failed,
                       coalesce(f.cancelled, 0) AS cancelled,
                       coalesce(r.n, 0) AS retried
                FROM buckets b
                LEFT JOIN submitted s ON s.ts = b.ts
                LEFT JOIN finished f ON f.ts = b.ts
                LEFT JOIN retried r ON r.ts = b.ts
                ORDER BY b.ts
                """
            ),
            {"pid": project_id, "bucket": spec.bucket, "span": spec.span},
        )
    ).all()
    return {
        "window": window,
        "bucket_seconds": int(spec.bucket.total_seconds()),
        "series": [
            {
                "ts": r.ts,
                "submitted": int(r.submitted),
                "succeeded": int(r.succeeded),
                "failed": int(r.failed),
                "cancelled": int(r.cancelled),
                "retried": int(r.retried),
            }
            for r in rows
        ],
    }


async def failures(session: AsyncSession, project_id: uuid.UUID, window: str) -> dict[str, Any]:
    spec = WINDOWS[window]
    params = {"pid": project_id, "span": spec.span}
    by_type = (
        await session.execute(
            text(
                """
                SELECT task_type,
                       count(*) AS total,
                       count(*) FILTER (WHERE state IN ('FAILED', 'DEAD_LETTERED')) AS failed
                FROM tasks
                WHERE project_id = :pid AND finished_at >= now() - CAST(:span AS interval)
                GROUP BY task_type ORDER BY failed DESC, total DESC LIMIT 25
                """
            ),
            params,
        )
    ).all()
    by_error = (
        await session.execute(
            text(
                """
                SELECT coalesce(a.error_type, 'unknown') AS error_type, a.status, count(*) AS n
                FROM task_attempts a JOIN tasks t ON t.id = a.task_id
                WHERE t.project_id = :pid AND a.started_at >= now() - CAST(:span AS interval)
                  AND a.status IN ('FAILED', 'TIMED_OUT', 'LOST')
                GROUP BY 1, 2 ORDER BY n DESC LIMIT 25
                """
            ),
            params,
        )
    ).all()
    recent = (
        await session.execute(
            text(
                """
                SELECT id, task_type, state, error->>'type' AS error_type, error->>'message' AS message,
                       finished_at
                FROM tasks
                WHERE project_id = :pid AND state IN ('FAILED', 'DEAD_LETTERED')
                  AND finished_at >= now() - CAST(:span AS interval)
                ORDER BY finished_at DESC LIMIT 20
                """
            ),
            params,
        )
    ).all()
    return {
        "window": window,
        "by_task_type": [
            {
                "task_type": r.task_type,
                "total": int(r.total),
                "failed": int(r.failed),
                "failure_rate": round(int(r.failed) / int(r.total), 4) if r.total else 0.0,
            }
            for r in by_type
        ],
        "by_error_type": [
            {"error_type": r.error_type, "attempt_status": r.status, "count": int(r.n)}
            for r in by_error
        ],
        "recent": [
            {
                "task_id": str(r.id),
                "task_type": r.task_type,
                "state": r.state,
                "error_type": r.error_type,
                "message": (r.message or "")[:300],
                "finished_at": r.finished_at,
            }
            for r in recent
        ],
    }


_PCT = """
  percentile_cont(0.5) WITHIN GROUP (ORDER BY {col}) AS p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY {col}) AS p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY {col}) AS p99,
  avg({col}) AS avg,
  max({col}) AS max,
  count({col}) AS n
"""


_END_TO_END_SQL = """
SELECT
  percentile_cont(0.5) WITHIN GROUP (ORDER BY e2e) AS p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY e2e) AS p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY e2e) AS p99,
  avg(e2e) AS avg,
  max(e2e) AS max,
  count(e2e) AS n
FROM (
  SELECT extract(epoch FROM (finished_at - created_at)) * 1000 AS e2e
  FROM tasks WHERE project_id = :pid AND state = 'SUCCEEDED' AND finished_at >= now() - CAST(:span AS interval)
) s
"""


def _pct(row: Any) -> dict[str, Any]:
    return {
        "p50_ms": _ms(row.p50),
        "p95_ms": _ms(row.p95),
        "p99_ms": _ms(row.p99),
        "avg_ms": _ms(row.avg),
        "max_ms": _ms(row.max),
        "count": int(row.n or 0),
    }


async def latency(session: AsyncSession, project_id: uuid.UUID, window: str) -> dict[str, Any]:
    spec = WINDOWS[window]
    params = {"pid": project_id, "span": spec.span, "bucket": spec.bucket}
    base = (
        "FROM task_attempts a JOIN tasks t ON t.id = a.task_id "
        "WHERE t.project_id = :pid AND a.started_at >= now() - CAST(:span AS interval)"
    )
    execution = (
        await session.execute(
            text(f"SELECT {_PCT.format(col='a.duration_ms')} {base} AND a.status = 'SUCCEEDED'"),
            params,
        )
    ).one()
    wait = (
        await session.execute(text(f"SELECT {_PCT.format(col='a.queue_wait_ms')} {base}"), params)
    ).one()
    end_to_end = (
        await session.execute(
            text(_END_TO_END_SQL),
            params,
        )
    ).one()
    by_type = (
        await session.execute(
            text(
                f"""
                SELECT t.task_type, {_PCT.format(col="a.duration_ms")}
                {base} AND a.status = 'SUCCEEDED'
                GROUP BY t.task_type ORDER BY n DESC LIMIT 20
                """
            ),
            params,
        )
    ).all()
    # Gap-filled like throughput: every bucket of the window is present, with nulls where nothing ran.
    series = (
        await session.execute(
            text(
                """
                WITH bounds AS (
                  SELECT date_bin(CAST(:bucket AS interval), now() - CAST(:span AS interval), timestamptz '2000-01-01') AS start_at
                ),
                buckets AS (
                  SELECT generate_series((SELECT start_at FROM bounds), now(), CAST(:bucket AS interval)) AS ts
                ),
                stats AS (
                  SELECT date_bin(CAST(:bucket AS interval), a.started_at, timestamptz '2000-01-01') AS ts,
                         percentile_cont(0.95) WITHIN GROUP (ORDER BY a.duration_ms)
                           FILTER (WHERE a.status = 'SUCCEEDED') AS exec_p95,
                         percentile_cont(0.5) WITHIN GROUP (ORDER BY a.duration_ms)
                           FILTER (WHERE a.status = 'SUCCEEDED') AS exec_p50,
                         percentile_cont(0.95) WITHIN GROUP (ORDER BY a.queue_wait_ms) AS wait_p95
                  FROM task_attempts a JOIN tasks t ON t.id = a.task_id
                  WHERE t.project_id = :pid AND a.started_at >= (SELECT start_at FROM bounds)
                  GROUP BY 1
                )
                SELECT b.ts, s.exec_p95, s.exec_p50, s.wait_p95
                FROM buckets b LEFT JOIN stats s ON s.ts = b.ts
                ORDER BY b.ts
                """
            ),
            params,
        )
    ).all()
    return {
        "window": window,
        "execution": _pct(execution),
        "queue_wait": _pct(wait),
        "end_to_end": _pct(end_to_end),
        "by_task_type": [{"task_type": r.task_type, **_pct(r)} for r in by_type],
        "bucket_seconds": int(spec.bucket.total_seconds()),
        "series": [
            {
                "ts": r.ts,
                "exec_p50_ms": _ms(r.exec_p50),
                "exec_p95_ms": _ms(r.exec_p95),
                "wait_p95_ms": _ms(r.wait_p95),
            }
            for r in series
        ],
    }


async def worker_utilization(
    session: AsyncSession, project_id: uuid.UUID, window: str
) -> dict[str, Any]:
    """Busy share of each worker's slots while it was online within the window:
    sum of (clipped) attempt durations / (concurrency * online seconds in the window).

    Dividing by the online time rather than the whole window keeps a worker that started an hour ago from looking
    idle in the 24-hour view. Includes attempts of all projects, because slots are shared; only aggregate numbers
    are exposed."""
    spec = WINDOWS[window]
    rows = (
        await session.execute(
            text(
                """
                SELECT w.id, w.name, w.status, w.concurrency,
                       coalesce(sum(
                         extract(epoch FROM (least(coalesce(a.finished_at, now()), now())
                                             - greatest(a.started_at, now() - CAST(:span AS interval))))
                       ), 0) AS busy_seconds,
                       count(a.id) AS attempts,
                       greatest(extract(epoch FROM (
                         least(now(), coalesce(w.stopped_at,
                                               CASE WHEN w.status = 'OFFLINE' THEN w.last_heartbeat_at ELSE now() END))
                         - greatest(w.started_at, now() - CAST(:span AS interval))
                       )), 0) AS online_seconds
                FROM workers w
                LEFT JOIN task_attempts a
                  ON a.worker_id = w.id AND coalesce(a.finished_at, now()) >= now() - CAST(:span AS interval)
                WHERE w.last_heartbeat_at >= now() - CAST(:span AS interval)
                GROUP BY w.id ORDER BY w.name
                """
            ),
            {"span": spec.span},
        )
    ).all()
    return {
        "window": window,
        "workers": [
            {
                "worker_id": str(r.id),
                "name": r.name,
                "status": r.status,
                "concurrency": int(r.concurrency),
                "attempts": int(r.attempts),
                "busy_seconds": round(float(r.busy_seconds), 2),
                "online_seconds": round(float(r.online_seconds), 2),
                "utilization": round(
                    min(
                        1.0, float(r.busy_seconds) / (int(r.concurrency) * float(r.online_seconds))
                    ),
                    6,
                )
                if r.concurrency and r.online_seconds
                else 0.0,
            }
            for r in rows
        ],
    }
