"""FlowForge control plane.

Every loop is safe to run in several scheduler instances at once (active-active): rows are claimed with
``FOR UPDATE SKIP LOCKED`` and every effect is idempotent, so there is no leader election and no failover gap.

Loops:
* outbox relay   — publishes outbox rows to Redis Streams; woken instantly by PostgreSQL LISTEN/NOTIFY, polls as a
                   fallback;
* promoter       — SCHEDULED / RETRY_SCHEDULED tasks whose time has come -> QUEUED;
* cron           — fires due schedules (exactly once per fire time);
* reaper         — recovers tasks whose lease expired; marks silent workers OFFLINE;
* reconciler     — re-dispatches QUEUED tasks whose stream entry was lost (e.g. Redis restarted without its data);
* retention      — deletes expired history in small batches; prunes idle stream consumers;
* metrics        — queue depth, task states, worker liveness, outbox backlog gauges.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import asyncpg
from prometheus_client import Gauge
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select, text

from flowforge_core.broker import CONSUMER_GROUP, StreamBroker
from flowforge_core.config import Settings
from flowforge_core.db.models import RefreshToken, Worker
from flowforge_core.db.session import create_engine, create_sessionmaker
from flowforge_core.db.uow import Database
from flowforge_core.events import EventPublisher
from flowforge_core.logs import get_logger
from flowforge_core.observability import metrics
from flowforge_core.observability.tracing import instrument_engine
from flowforge_core.redis_client import create_redis
from flowforge_core.services import outbox, schedules
from flowforge_core.services import tasks as task_service
from flowforge_core.services import workers as worker_service
from flowforge_shared.priority import PriorityBand
from flowforge_shared.states import TaskState

log = get_logger("flowforge_scheduler")

LOOP_LAST_SUCCESS = Gauge(
    "flowforge_scheduler_loop_last_success_timestamp",
    "Unix time of each loop's last successful cycle",
    ["loop"],
)
LOOP_FAILURES = Gauge(
    "flowforge_scheduler_loop_consecutive_failures", "Consecutive failures per loop", ["loop"]
)

OUTBOX_CHANNEL = "ff_outbox"
CONSUMER_IDLE_PRUNE_MS = 3_600_000


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class ControlPlane:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stop = asyncio.Event()
        self.started = asyncio.Event()
        self._outbox_signal = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._listen_conn: asyncpg.Connection | None = None
        self.db: Database
        self.broker: StreamBroker
        self.known_queues: set[str] = set()

    async def start(self) -> None:
        engine = create_engine(self.settings, application_name="flowforge-scheduler")
        instrument_engine(engine)
        metrics.DB_POOLS.register("scheduler", engine.pool)
        self.redis = create_redis(self.settings, client_name="flowforge-scheduler")
        self.db = Database(
            engine,
            create_sessionmaker(engine),
            EventPublisher(self.redis, self.settings.redis_prefix),
        )
        self.broker = StreamBroker(self.redis, self.settings.redis_prefix)
        self.started.set()
        log.info("scheduler_started")

    async def run(self) -> None:
        await self.start()
        s = self.settings
        loops: dict[str, tuple[Callable[[], Awaitable[Any]], float]] = {
            "promoter": (self.promote_once, s.scheduler_poll_interval_seconds),
            "cron": (self.cron_once, 1.0),
            "reaper": (self.reap_once, s.reaper_interval_seconds),
            "reconciler": (self.reconcile_once, s.reconcile_interval_seconds),
            "retention": (self.retention_once, s.retention_interval_seconds),
            "metrics": (self.metrics_once, 10.0),
        }
        self._tasks = [
            asyncio.create_task(self._supervise(name, fn, every), name=name)
            for name, (fn, every) in loops.items()
        ]
        self._tasks.append(asyncio.create_task(self._relay_loop(), name="outbox-relay"))
        self._tasks.append(asyncio.create_task(self._listen_loop(), name="outbox-listen"))
        await self._stop.wait()
        await self.shutdown()

    def request_stop(self) -> None:
        self._stop.set()

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._listen_conn is not None and not self._listen_conn.is_closed():
            await self._listen_conn.close()
        await self.redis.aclose()
        await self.db.dispose()
        log.info("scheduler_stopped")

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _supervise(self, name: str, fn: Callable[[], Awaitable[Any]], every: float) -> None:
        """Run ``fn`` every ``every`` seconds; failures are logged with a traceback and retried with backoff."""
        failures = 0
        while not self._stop.is_set():
            try:
                await fn()
                failures = 0
                LOOP_LAST_SUCCESS.labels(name).set(time.time())
                LOOP_FAILURES.labels(name).set(0)
                await self._sleep(every)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                LOOP_FAILURES.labels(name).set(failures)
                backoff = min(every * 2**failures, 30.0)
                log.exception(
                    "scheduler_loop_failed",
                    loop=name,
                    consecutive_failures=failures,
                    retry_in=backoff,
                )
                await self._sleep(backoff)

    # ------------------------------------------------------------------------------------------------ outbox relay
    async def relay_once(self) -> int:
        limit = self.settings.scheduler_batch_size
        async with self.db.transaction() as uow:
            events = await outbox.claim_batch(uow.session, limit)
            if not events:
                return 0
            # Published while the rows are locked; if Redis fails the transaction rolls back and the rows stay
            # unpublished. A crash after XADD but before COMMIT re-publishes them: duplicates are discarded by workers.
            await self.broker.publish([(e.stream, e.payload) for e in events])
            await outbox.mark_published(uow.session, [e.id for e in events])
        metrics.OUTBOX_PUBLISHED.inc(len(events))
        return len(events)

    async def _relay_loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                published = await self.relay_once()
                failures = 0
                LOOP_LAST_SUCCESS.labels("outbox-relay").set(time.time())
                if published >= self.settings.scheduler_batch_size:
                    continue  # more waiting: drain without sleeping
                self._outbox_signal.clear()
                with contextlib.suppress(TimeoutError):
                    # NOTIFY wakes us within milliseconds; the timeout is the polling fallback.
                    await asyncio.wait_for(self._outbox_signal.wait(), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                metrics.OUTBOX_PUBLISH_FAILURES.inc()
                backoff = min(0.25 * 2**failures, 10.0)
                log.warning(
                    "outbox_relay_failed",
                    error=str(exc)[:300],
                    retry_in=backoff,
                    exc_info=failures == 1,
                )
                await self._sleep(backoff)

    async def _listen_loop(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                conn = await asyncpg.connect(asyncpg_dsn(self.settings.database_url), timeout=10)
                self._listen_conn = conn
                await conn.add_listener(OUTBOX_CHANNEL, lambda *_: self._outbox_signal.set())
                backoff = 0.5
                self._outbox_signal.set()  # catch up on anything inserted while we were not listening
                while not self._stop.is_set() and not conn.is_closed():
                    await self._sleep(15)
                    await conn.execute("SELECT 1")  # liveness probe of the LISTEN connection
            except asyncio.CancelledError:
                raise
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
                log.warning("outbox_listen_failed", error=str(exc)[:200], retry_in=backoff)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 15.0)
            finally:
                if self._listen_conn is not None and not self._listen_conn.is_closed():
                    with contextlib.suppress(Exception):
                        await self._listen_conn.close()

    # ------------------------------------------------------------------------------------------------ promotion, cron
    async def promote_once(self) -> int:
        total = 0
        while True:
            async with self.db.transaction() as uow:
                n = await task_service.promote_due_tasks(
                    uow, limit=self.settings.scheduler_batch_size
                )
            total += n
            if n < self.settings.scheduler_batch_size:
                return total

    async def cron_once(self) -> int:
        async with self.db.transaction() as uow:
            return await schedules.fire_due_schedules(uow, limit=100)

    # ------------------------------------------------------------------------------------------------ reaper
    async def reap_once(self) -> tuple[int, int]:
        recovered = 0
        while True:
            async with self.db.transaction() as uow:
                n = await task_service.expire_leases(uow, limit=200)
            recovered += n
            if n < 200:
                break
        async with self.db.transaction() as uow:
            offline = await worker_service.mark_offline(
                uow, offline_after_seconds=self.settings.worker_offline_after_seconds
            )
        if recovered or offline:
            log.warning("reaper_recovered", leases=recovered, workers_offline=len(offline))
        return recovered, len(offline)

    # ------------------------------------------------------------------------------------------------ reconciler
    async def reconcile_once(self) -> int:
        async with self.db.session() as session:
            queues = await task_service.queued_queues(session)
        self.known_queues.update(queues)
        total = 0
        for queue in queues:
            streams = {band: self.broker.stream_for(queue, band) for band in PriorityBand}
            try:
                stats = {s.stream: s for s in await self.broker.stats(streams.values())}
            except (RedisError, OSError) as exc:
                log.warning("reconcile_skipped_redis_unavailable", error=str(exc)[:200])
                return total
            for band, stream in streams.items():
                if stats[stream].length > 0:
                    continue  # entries still present: the backlog is simply not consumed yet
                async with self.db.transaction() as uow:
                    n = await task_service.reconcile_lost_dispatches(
                        uow,
                        queue=queue,
                        band=band,
                        grace_seconds=self.settings.reconcile_grace_seconds,
                        limit=self.settings.scheduler_batch_size,
                    )
                if n:
                    log.warning("lost_dispatches_reconciled", queue=queue, band=band.name, tasks=n)
                total += n
        return total

    # ------------------------------------------------------------------------------------------------ retention
    async def retention_once(self) -> dict[str, int]:
        removed: dict[str, int] = {}
        removed["tasks"] = await self._batched_delete(
            """
            DELETE FROM tasks WHERE id IN (
              SELECT t.id FROM tasks t JOIN projects p ON p.id = t.project_id
              WHERE t.state IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'DEAD_LETTERED')
                AND t.finished_at < now() - make_interval(
                      days => coalesce((p.settings->>'retention_days')::int, :days))
              LIMIT 1000)
            """
        )
        removed["workflow_runs"] = await self._batched_delete(
            """
            DELETE FROM workflow_runs WHERE id IN (
              SELECT id FROM workflow_runs
              WHERE state IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
                AND finished_at < now() - make_interval(days => :days)
              LIMIT 1000)
            """
        )
        async with self.db.transaction() as uow:
            removed["outbox"] = await outbox.purge_published(
                uow.session, self.settings.outbox_retention_hours
            )
            result = await uow.session.execute(
                delete(RefreshToken).where(RefreshToken.expires_at < func.now() - timedelta(days=1))
            )
            # Not "refresh_tokens": log redaction masks any key containing "token", which would hide the count.
            removed["expired_sessions"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
            result = await uow.session.execute(
                delete(Worker).where(
                    Worker.status.in_(["STOPPED", "OFFLINE"]),
                    Worker.last_heartbeat_at < func.now() - timedelta(days=7),
                )
            )
            removed["workers"] = int(result.rowcount or 0)  # type: ignore[attr-defined]
        removed["consumers"] = await self._prune_consumers()
        if any(removed.values()):
            log.info("retention_cycle", **removed)
        return removed

    async def _batched_delete(self, sql: str) -> int:
        total = 0
        while True:
            async with self.db.transaction() as uow:
                result = await uow.session.execute(
                    text(sql), {"days": self.settings.retention_days}
                )
            n = int(result.rowcount or 0)  # type: ignore[attr-defined]
            total += n
            if n < 1000:
                return total

    async def _prune_consumers(self) -> int:
        """Remove consumer-group members that own no pending entries and have been idle for an hour."""
        pruned = 0
        async with self.db.session() as session:
            live = {
                (m or {}).get("consumer")
                for m in (
                    await session.scalars(
                        select(Worker.meta).where(Worker.status.in_(["ACTIVE", "DRAINING"]))
                    )
                )
            }
        # Snapshot: other loops add queues to the set while this coroutine awaits.
        for queue in sorted(self.known_queues):
            for stream in self.broker.streams_for_queues([queue]):
                try:
                    consumers = await self.redis.xinfo_consumers(stream, CONSUMER_GROUP)
                except RedisError:
                    continue
                for c in consumers:
                    name = c["name"].decode() if isinstance(c["name"], bytes) else str(c["name"])
                    if (
                        name in live
                        or int(c.get("pending", 0)) > 0
                        or int(c.get("idle", 0)) < CONSUMER_IDLE_PRUNE_MS
                    ):
                        continue
                    pruned += await self.broker.remove_consumer(stream, name)
        return pruned

    # ------------------------------------------------------------------------------------------------ metrics
    async def metrics_once(self) -> None:
        async with self.db.session() as session:
            states: dict[str, int] = dict(
                (
                    await session.execute(text("SELECT state, count(*) FROM tasks GROUP BY state"))
                ).all()
            )
            for state in TaskState:
                metrics.TASKS_BY_STATE.labels(state.value).set(states.get(state.value, 0))
            by_status = await worker_service.counts_by_status(session)
            for status in ("ACTIVE", "DRAINING", "STOPPED", "OFFLINE"):
                metrics.WORKERS_BY_STATUS.labels(status).set(by_status.get(status, 0))
            metrics.WORKER_HEARTBEAT_AGE.clear()
            rows = (
                await session.execute(
                    select(
                        Worker.name, func.extract("epoch", func.now() - Worker.last_heartbeat_at)
                    ).where(Worker.status.in_(["ACTIVE", "DRAINING"]))
                )
            ).all()
            for name, age in rows:
                metrics.WORKER_HEARTBEAT_AGE.labels(name).set(float(age))
            metrics.OUTBOX_BACKLOG.set(await outbox.backlog(session))
            self.known_queues.update(
                await session.scalars(
                    select(func.unnest(Worker.queues)).where(Worker.status == "ACTIVE").distinct()
                )
            )
        if not self.known_queues:
            self.known_queues.add("default")
        try:
            for queue in sorted(self.known_queues):
                for stat in await self.broker.stats(self.broker.streams_for_queues([queue])):
                    band = stat.stream.rsplit(":", 1)[-1]
                    metrics.QUEUE_LENGTH.labels(queue, band).set(stat.length)
                    metrics.QUEUE_PENDING.labels(queue, band).set(stat.pending)
        except (RedisError, OSError) as exc:
            log.warning("queue_metrics_unavailable", error=str(exc)[:200])
