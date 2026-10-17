"""Prometheus metric catalogue.

Label cardinality is bounded on purpose: task IDs, user IDs and raw URL paths are never labels. Latency is recorded in
histograms so p50/p95/p99 can be computed with ``histogram_quantile``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector

_EXEC_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    300,
    600,
    1800,
)
_WAIT_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    300,
    900,
    3600,
)
_HTTP_BUCKETS = (0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

# ---------------------------------------------------------------------------------------------- task lifecycle
TASKS_SUBMITTED = Counter(
    "flowforge_tasks_submitted_total", "Tasks accepted for execution", ["task_type", "queue"]
)
TASKS_FINISHED = Counter(
    "flowforge_task_attempts_finished_total",
    "Attempts that finished, by outcome (succeeded, failed, timed_out, lost, cancelled, interrupted)",
    ["task_type", "queue", "outcome"],
)
TASK_RETRIES = Counter(
    "flowforge_task_retries_total", "Automatic retries scheduled", ["task_type", "reason"]
)
TASKS_DEAD_LETTERED = Counter(
    "flowforge_tasks_dead_lettered_total", "Tasks moved to the dead-letter queue", ["task_type"]
)
TASK_EXECUTION_SECONDS = Histogram(
    "flowforge_task_execution_seconds",
    "Handler execution time per attempt",
    ["task_type", "queue", "outcome"],
    buckets=_EXEC_BUCKETS,
)
TASK_QUEUE_WAIT_SECONDS = Histogram(
    "flowforge_task_queue_wait_seconds",
    "Time between a task becoming QUEUED and a worker claiming it",
    ["queue"],
    buckets=_WAIT_BUCKETS,
)

# ---------------------------------------------------------------------------------------------- delivery
DUPLICATE_DELIVERIES = Counter(
    "flowforge_duplicate_deliveries_total",
    "Stream entries discarded because the task was no longer QUEUED",
    ["reason"],
)
FENCED_WRITES = Counter(
    "flowforge_fenced_writes_total",
    "Worker writes rejected because the lease token no longer matched (zombie attempt)",
    ["operation"],
)
MESSAGES_RECLAIMED = Counter(
    "flowforge_stream_messages_reclaimed_total", "Pending stream entries reclaimed via XAUTOCLAIM"
)
HANDLER_CRASHES = Counter(
    "flowforge_worker_handler_crashes_total",
    "Stream entries whose handling failed unexpectedly; the entry stays pending and is retried",
)
LEASES_EXPIRED = Counter(
    "flowforge_leases_expired_total", "Leases expired and recovered by the reaper"
)
TASKS_RECONCILED = Counter(
    "flowforge_tasks_reconciled_total",
    "QUEUED tasks re-dispatched because their stream entry was lost",
)
OUTBOX_PUBLISHED = Counter("flowforge_outbox_published_total", "Outbox events published to Redis")
OUTBOX_PUBLISH_FAILURES = Counter(
    "flowforge_outbox_publish_failures_total", "Failed outbox relay batches"
)
OUTBOX_BACKLOG = Gauge("flowforge_outbox_backlog", "Unpublished outbox events")
TASKS_PROMOTED = Counter(
    "flowforge_tasks_promoted_total", "Delayed/retrying tasks promoted to QUEUED", ["from_state"]
)
SCHEDULE_FIRES = Counter(
    "flowforge_schedule_fires_total", "Cron schedule fire decisions", ["outcome"]
)
EVENT_PUBLISH_FAILURES = Counter(
    "flowforge_realtime_publish_failures_total", "Realtime events that could not be published"
)

# ---------------------------------------------------------------------------------------------- queues & fleet
QUEUE_LENGTH = Gauge(
    "flowforge_queue_stream_length", "Stream entries not yet acknowledged", ["queue", "band"]
)
QUEUE_PENDING = Gauge(
    "flowforge_queue_pending_messages", "Delivered but unacknowledged entries", ["queue", "band"]
)
TASKS_BY_STATE = Gauge("flowforge_tasks_by_state", "Tasks per state (all projects)", ["state"])
WORKERS_BY_STATUS = Gauge("flowforge_workers", "Registered workers per status", ["status"])
WORKER_HEARTBEAT_AGE = Gauge(
    "flowforge_worker_heartbeat_age_seconds",
    "Seconds since each live worker's last heartbeat",
    ["worker"],
)
WORKER_ACTIVE_TASKS = Gauge(
    "flowforge_worker_active_tasks", "Tasks executing in this worker process"
)
WORKER_CONCURRENCY = Gauge("flowforge_worker_concurrency", "Configured concurrency of this worker")

# ---------------------------------------------------------------------------------------------- API
HTTP_REQUEST_SECONDS = Histogram(
    "flowforge_http_request_duration_seconds",
    "API request latency by route template",
    ["method", "route", "status_class"],
    buckets=_HTTP_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("flowforge_http_requests_in_flight", "In-flight API requests")
RATE_LIMITED = Counter(
    "flowforge_rate_limited_total", "Requests rejected by rate limiting", ["scope"]
)
AUTH_FAILURES = Counter("flowforge_auth_failures_total", "Failed authentication attempts", ["kind"])
SSE_CONNECTIONS = Gauge("flowforge_sse_connections", "Open realtime (SSE) connections")


# ---------------------------------------------------------------------------------------------- DB pool
class _PoolCollector(Collector):
    """Exposes SQLAlchemy connection pool statistics for every registered engine."""

    def __init__(self) -> None:
        self._pools: dict[str, Any] = {}

    def register(self, name: str, pool: Any) -> None:
        self._pools[name] = pool

    def collect(self) -> Iterator[GaugeMetricFamily]:
        size = GaugeMetricFamily("flowforge_db_pool_size", "Configured pool size", labels=["pool"])
        out = GaugeMetricFamily(
            "flowforge_db_pool_checked_out", "Connections currently in use", labels=["pool"]
        )
        idle = GaugeMetricFamily(
            "flowforge_db_pool_idle", "Idle pooled connections", labels=["pool"]
        )
        overflow = GaugeMetricFamily(
            "flowforge_db_pool_overflow", "Connections opened beyond pool_size", labels=["pool"]
        )
        for name, pool in self._pools.items():
            for family, getter in (
                (size, "size"),
                (out, "checkedout"),
                (idle, "checkedin"),
                (overflow, "overflow"),
            ):
                fn = getattr(pool, getter, None)
                if callable(fn):
                    family.add_metric([name], float(fn()))
        yield from (size, out, idle, overflow)


DB_POOLS = _PoolCollector()
REGISTRY.register(DB_POOLS)
