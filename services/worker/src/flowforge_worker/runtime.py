"""Worker runtime.

One task on one worker:

    XREADGROUP (priority-ordered streams) -> claim in PostgreSQL (QUEUED -> RUNNING, fresh lease token)
    -> XACK + XDEL -> run the handler in a child task with a timeout -> complete / fail, fenced by the lease token.

Background loops:
* heartbeat   — renews leases of in-flight tasks, publishes CPU/RSS, learns about cancel requests and drains;
* reclaim     — XAUTOCLAIM entries idle in the PEL (their consumer died between read and claim);
* log flush   — batches handler logs into PostgreSQL and publishes them for live tailing;
* control     — Redis Pub/Sub channel for instant cancel / drain notifications (the heartbeat is the fallback).

The supervising coroutine is never cancelled to stop a handler: timeouts, cancel requests, lease loss and shutdown all
cancel the *handler's child task*, so the supervisor always gets to record the outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

import orjson
import psutil
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import insert
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import QueuePool

from flowforge_core.broker import StreamBroker, StreamMessage
from flowforge_core.config import Settings
from flowforge_core.db.models import TaskLog
from flowforge_core.db.session import create_engine, create_sessionmaker
from flowforge_core.db.uow import Database
from flowforge_core.errors import NotFound
from flowforge_core.events import EventPublisher, worker_control_channel
from flowforge_core.logs import bound_context, get_logger
from flowforge_core.observability import metrics
from flowforge_core.observability.tracing import (
    context_from_traceparent,
    get_tracer,
    instrument_engine,
)
from flowforge_core.redis_client import create_redis
from flowforge_core.services import task_queries, workflow_engine
from flowforge_core.services import tasks as task_service
from flowforge_core.services import workers as worker_service
from flowforge_core.services.task_types import register_task_types
from flowforge_core.services.tasks import ClaimedTask, FinishResult, TaskFailure
from flowforge_shared.lifecycle import FailureKind
from flowforge_shared.messages import DispatchMessage
from flowforge_shared.priority import SmoothWeightedRoundRobin
from flowforge_shared.states import TaskState
from flowforge_worker import __version__
from flowforge_worker.context import LogBuffer, TaskContext, TaskLogger
from flowforge_worker.errors import TaskCancelled
from flowforge_worker.registry import TaskDefinition, TaskRegistry

log = get_logger("flowforge_worker")
tracer = get_tracer("flowforge.worker")
T = TypeVar("T")

MAX_RESULT_BYTES = 256 * 1024
MAX_PENDING_LOG_ROWS = 20_000
# Database connections a worker needs beyond one per execution slot: the heartbeat and the log flush.
POOL_HEADROOM = 2


def is_transient_db_error(exc: BaseException) -> bool:
    if isinstance(
        exc, OperationalError | InterfaceError | OSError | ConnectionError | TimeoutError
    ):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


class ResultTooLarge(Exception):
    retryable = False


@dataclass(slots=True)
class Running:
    claimed: ClaimedTask
    ctx: TaskContext
    started: float
    handler_task: asyncio.Task[Any] | None = None
    cancel_reason: str | None = None  # cancel_requested | lease_lost | shutdown


@dataclass(slots=True)
class WorkerStats:
    succeeded: int = 0
    failed: int = 0
    claimed: int = 0
    duplicates: int = 0
    by_outcome: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class Worker:
    def __init__(
        self,
        settings: Settings,
        registry: TaskRegistry,
        *,
        queues: list[str] | None = None,
        concurrency: int | None = None,
        name: str | None = None,
        worker_id: uuid.UUID | None = None,
        install_signal_handlers: bool = True,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.queues = list(queues or settings.worker_queues)
        self.concurrency = concurrency or settings.worker_concurrency
        self.id = worker_id or uuid.uuid4()
        self.name = f"{name or settings.worker_name}-{os.getpid()}"
        self.consumer = f"{self.name}:{self.id.hex[:8]}"
        self.install_signal_handlers = install_signal_handlers
        self.inflight: dict[uuid.UUID, Running] = {}
        self.draining = False
        self.stats = WorkerStats()
        self._busy = 0
        self._slot_freed = asyncio.Event()
        self._stop = asyncio.Event()
        self._heartbeat_now = asyncio.Event()
        self._force = False
        self._started = asyncio.Event()
        self._succeeded_delta = 0
        self._failed_delta = 0
        self._logs = LogBuffer()
        self._handling: set[asyncio.Task[None]] = set()
        self._loops: dict[str, asyncio.Task[None]] = {}
        self._reclaim_cursors: dict[str, str] = {}
        self._proc = psutil.Process()
        self._children: dict[int, psutil.Process] = {}
        self._cpu_count = psutil.cpu_count() or 1
        self._process_pool: ProcessPoolExecutor | None = None
        self._thread_pool: ThreadPoolExecutor | None = None
        self.db: Database
        self.broker: StreamBroker
        self.publisher: EventPublisher

    # ------------------------------------------------------------------------------------------------ lifecycle
    @property
    def streams(self) -> list[str]:
        return self.broker.streams_for_queues(self.queues)

    async def start(self) -> None:
        engine = create_engine(self.settings, application_name=f"flowforge-worker:{self.name}")
        instrument_engine(engine)
        metrics.DB_POOLS.register("worker", engine.pool)
        self.redis = create_redis(self.settings, client_name=f"flowforge-worker:{self.name}")
        self.publisher = EventPublisher(self.redis, self.settings.redis_prefix)
        self.db = Database(engine, create_sessionmaker(engine), self.publisher)
        self.broker = StreamBroker(self.redis, self.settings.redis_prefix)
        await self.broker.ensure_groups(self.streams)
        await self._warm_pool(engine)
        definitions = self.registry.definitions()
        async with self.db.transaction() as uow:
            await register_task_types(uow.session, [d.catalog_entry() for d in definitions])
            await worker_service.register_worker(
                uow,
                worker_id=self.id,
                name=self.name,
                hostname=socket.gethostname(),
                pid=os.getpid(),
                version=__version__,
                queues=self.queues,
                task_types=self.registry.names(),
                concurrency=self.concurrency,
                metadata={"consumer": self.consumer, "cpu_count": self._cpu_count},
            )
        self._thread_pool = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="ff-handler"
        )
        if (
            any(d.executor == "process" for d in definitions)
            and self.settings.worker_cpu_processes > 0
        ):
            self._process_pool = ProcessPoolExecutor(max_workers=self.settings.worker_cpu_processes)
        metrics.WORKER_CONCURRENCY.set(self.concurrency)
        self._proc.cpu_percent(None)
        log.info(
            "worker_started",
            worker_id=str(self.id),
            name=self.name,
            queues=self.queues,
            concurrency=self.concurrency,
            task_types=self.registry.names(),
        )
        self._started.set()

    async def _warm_pool(self, engine: AsyncEngine) -> None:
        """Open the connections the execution slots will use before taking any work.

        On a cold pool the first burst of claims waits for connection set-up, which PostgreSQL performs one connection
        at a time (about 0.1 s each on Windows): eight fresh workers needed six seconds for their first 100 tasks.
        """
        pool = engine.pool
        if not isinstance(pool, QueuePool):
            return
        wanted = self.concurrency + POOL_HEADROOM
        if pool.size() < wanted:
            log.warning(
                "database_pool_smaller_than_concurrency",
                pool_size=pool.size(),
                recommended=wanted,
                detail="raise FLOWFORGE_DATABASE_POOL_SIZE; otherwise connections are opened and closed under load",
            )
        results = await asyncio.gather(
            *(engine.connect().start() for _ in range(min(wanted, pool.size()))),
            return_exceptions=True,
        )
        opened = [r for r in results if isinstance(r, AsyncConnection)]
        await asyncio.gather(*(conn.close() for conn in opened))
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            # Not fatal: the pool opens the remaining connections on demand, as it would without warming.
            log.warning(
                "database_pool_warmup_incomplete",
                opened=len(opened),
                wanted=len(results),
                error=str(failures[0])[:200],
            )

    async def run(self) -> None:
        await self.start()
        if self.install_signal_handlers:
            self._install_signal_handlers()
        self._loops = {
            "consume": asyncio.create_task(self._consume_loop(), name="consume"),
            "heartbeat": asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            "reclaim": asyncio.create_task(self._reclaim_loop(), name="reclaim"),
            "logs": asyncio.create_task(self._log_flush_loop(), name="log-flush"),
            "control": asyncio.create_task(self._control_loop(), name="control"),
        }
        for loop_task in self._loops.values():
            loop_task.add_done_callback(self._loop_died)
        await self._stop.wait()
        await self._shutdown()

    async def wait_started(self) -> None:
        await self._started.wait()

    @property
    def stopping(self) -> bool:
        """True once shutdown has begun: requested, or forced by a crashed core loop."""
        return self._stop.is_set()

    def request_stop(self, *, force: bool = False) -> None:
        if self._stop.is_set() or force:
            self._force = True
            for running in list(self.inflight.values()):
                self._cancel_running(running.claimed.id, "shutdown")
        self._stop.set()

    def _loop_died(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._stop.is_set():
            return
        exc = task.exception()
        log.error("worker_loop_crashed", loop=task.get_name(), exc_info=exc)
        # A dead core loop would leave the worker half-alive; stop so the supervisor restarts it.
        self.request_stop()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def on_signal(*_: object) -> None:
            loop.call_soon_threadsafe(self.request_stop)

        if os.name == "nt":
            signal.signal(signal.SIGINT, on_signal)
            signal.signal(signal.SIGBREAK, on_signal)
        else:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.request_stop)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _shutdown(self) -> None:
        log.info("worker_stopping", inflight=len(self.inflight), force=self._force)
        for name in ("consume", "reclaim"):
            self._loops[name].cancel()
        grace = 0.0 if self._force else self.settings.worker_shutdown_grace_seconds
        if self._handling and grace > 0:
            await asyncio.wait(set(self._handling), timeout=grace)
        for running in list(self.inflight.values()):
            self._cancel_running(running.claimed.id, "shutdown")
        if self._handling:
            await asyncio.wait(set(self._handling), timeout=15)
        await self._flush_logs()
        try:
            async with self.db.transaction() as uow:
                await worker_service.mark_stopped(uow, worker_id=self.id)
        except Exception as exc:  # noqa: BLE001 - best effort during shutdown; reaper marks us OFFLINE anyway
            log.warning("mark_stopped_failed", error=str(exc)[:200])
        for loop_task in self._loops.values():
            loop_task.cancel()
        await asyncio.gather(*self._loops.values(), return_exceptions=True)
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=False, cancel_futures=True)
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=False, cancel_futures=True)
        await self.redis.aclose()
        await self.db.dispose()
        log.info(
            "worker_stopped",
            worker_id=str(self.id),
            succeeded=self.stats.succeeded,
            failed=self.stats.failed,
        )

    # ------------------------------------------------------------------------------------------------ consuming
    async def _consume_loop(self) -> None:
        rr = SmoothWeightedRoundRobin()
        backoff = 0.5
        while not self._stop.is_set():
            if self.draining:
                await self._sleep_or_stop(0.5)
                continue
            free = self.concurrency - self._busy
            if free <= 0:
                self._slot_freed.clear()
                stop_wait = asyncio.create_task(self._stop.wait())
                slot_wait = asyncio.create_task(self._slot_freed.wait())
                _, pending = await asyncio.wait(
                    {stop_wait, slot_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                for p in pending:
                    p.cancel()
                continue
            order = rr.read_order()
            streams = [self.broker.stream_for(q, band) for band in order for q in self.queues]
            try:
                messages = await self.broker.read(
                    self.consumer, streams, free, self.settings.worker_poll_block_ms
                )
                backoff = 0.5
            except (RedisError, OSError) as exc:
                log.warning("stream_read_failed", error=str(exc)[:200], retry_in=backoff)
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            if messages and (self.draining or self._stop.is_set()):
                # The blocking read started before the drain/stop was observed: give the entries back.
                await self._requeue(messages)
                continue
            for message in messages:
                self._start_handling(message)

    async def _requeue(self, messages: list[StreamMessage]) -> None:
        try:
            await self.broker.requeue(messages)
            log.info("entries_requeued", count=len(messages), draining=self.draining)
        except (RedisError, OSError) as exc:
            # Still safe: the entries stay in this consumer's PEL and are reclaimed by another worker.
            log.warning("requeue_failed", count=len(messages), error=str(exc)[:200])

    def _start_handling(self, message: StreamMessage) -> None:
        self._busy += 1
        task = asyncio.create_task(self._handle(message))
        self._handling.add(task)
        task.add_done_callback(self._handling_done)

    def _handling_done(self, task: asyncio.Task[None]) -> None:
        self._handling.discard(task)
        self._busy -= 1
        self._slot_freed.set()
        if not task.cancelled() and task.exception() is not None:
            # Deliberately fail-safe: the entry is not acknowledged, so it is reclaimed and retried until the cause
            # (typically a bug in a new release) is fixed, instead of failing or parking tasks. Alerted on.
            metrics.HANDLER_CRASHES.inc()
            log.error("task_supervisor_crashed", exc_info=task.exception())

    async def _ack(self, message: StreamMessage) -> None:
        try:
            await self.broker.ack(message.stream, message.message_id)
        except (RedisError, OSError) as exc:
            # Harmless: the entry stays pending, is reclaimed later and discarded as a duplicate.
            log.warning("ack_failed", stream=message.stream, error=str(exc)[:200])

    async def _handle(self, message: StreamMessage) -> None:
        try:
            dispatch = DispatchMessage.from_fields(message.fields)
        except ValidationError as exc:
            log.error("malformed_stream_entry", stream=message.stream, error=str(exc)[:300])
            await self._ack(message)
            return
        try:
            claimed = await self._db_retry(lambda: self._claim(dispatch), op="claim", budget=5.0)
        except Exception as exc:
            if not is_transient_db_error(exc):
                raise
            # Not acknowledged: the entry stays in the PEL and is reclaimed once PostgreSQL is reachable again.
            log.warning(
                "claim_failed_left_pending", task_id=str(dispatch.task_id), error=str(exc)[:200]
            )
            return
        if claimed is None:
            await self._record_duplicate(dispatch)
            await self._ack(message)
            return
        self.stats.claimed += 1
        await self._ack(message)
        if claimed.queue_wait_seconds is not None:
            metrics.TASK_QUEUE_WAIT_SECONDS.labels(claimed.queue).observe(
                claimed.queue_wait_seconds
            )
        await self._execute(claimed)

    async def _claim(self, dispatch: DispatchMessage) -> ClaimedTask | None:
        async with self.db.transaction() as uow:
            return await task_service.claim_task(
                uow,
                task_id=dispatch.task_id,
                worker_id=self.id,
                lease_ttl_seconds=self.settings.lease_ttl_seconds,
            )

    async def _record_duplicate(self, dispatch: DispatchMessage) -> None:
        self.stats.duplicates += 1
        try:
            async with self.db.session() as session:
                state = await task_service.task_state(session, dispatch.task_id)
            reason = state.value.lower() if state else "not_found"
        except Exception as exc:  # noqa: BLE001 - only used for a metric label
            reason = f"unknown:{type(exc).__name__}"
        metrics.DUPLICATE_DELIVERIES.labels(reason).inc()
        log.debug("duplicate_delivery_discarded", task_id=str(dispatch.task_id), reason=reason)

    # ------------------------------------------------------------------------------------------------ execution
    async def _execute(self, claimed: ClaimedTask) -> None:
        definition = self.registry.get(claimed.task_type)
        logger = TaskLogger(claimed.id, claimed.attempt_number, self._logs)
        ctx = TaskContext(
            task_id=claimed.id,
            project_id=claimed.project_id,
            task_type=claimed.task_type,
            queue=claimed.queue,
            priority=claimed.priority,
            attempt=claimed.attempt_number,
            retry_count=claimed.retry_count,
            max_retries=claimed.max_retries,
            lease_token=claimed.lease_token,
            idempotency_key=claimed.idempotency_key,
            metadata=claimed.metadata,
            logger=logger,
            progress_sink=self._progress,
        )
        running = Running(claimed=claimed, ctx=ctx, started=time.perf_counter())
        self.inflight[claimed.id] = running
        metrics.WORKER_ACTIVE_TASKS.set(len(self.inflight))
        outcome = "succeeded"
        with (
            bound_context(
                task_id=str(claimed.id), task_type=claimed.task_type, attempt=claimed.attempt_number
            ),
            tracer.start_as_current_span(
                "task.execute",
                context=context_from_traceparent(claimed.traceparent),
                attributes={
                    "flowforge.task_id": str(claimed.id),
                    "flowforge.task_type": claimed.task_type,
                    "flowforge.attempt": claimed.attempt_number,
                    "flowforge.queue": claimed.queue,
                },
            ),
        ):
            try:
                result, failure, outcome = await self._run_handler(definition, running)
                logger.finalize()
                await self._flush_logs()
                if outcome == "lost":
                    log.warning("lease_lost_result_discarded", task_id=str(claimed.id))
                    return
                finish = await self._finish(claimed, result, failure)
                if not finish.applied:
                    outcome = "fenced"
                    log.warning("write_fenced", task_id=str(claimed.id))
            except Exception as exc:
                if not is_transient_db_error(exc):
                    raise
                # PostgreSQL stayed unreachable for the whole retry budget. The lease will expire and the reaper
                # will retry the task elsewhere: at-least-once, never silently lost.
                outcome = "unrecorded"
                log.error("finish_not_recorded", task_id=str(claimed.id), error=str(exc)[:200])
            finally:
                self.inflight.pop(claimed.id, None)
                metrics.WORKER_ACTIVE_TASKS.set(len(self.inflight))
                elapsed = time.perf_counter() - running.started
                metrics.TASKS_FINISHED.labels(claimed.task_type, claimed.queue, outcome).inc()
                metrics.TASK_EXECUTION_SECONDS.labels(
                    claimed.task_type, claimed.queue, outcome
                ).observe(elapsed)
                self.stats.by_outcome[outcome] += 1
                if outcome == "succeeded":
                    self.stats.succeeded += 1
                    self._succeeded_delta += 1
                elif outcome in ("failed", "timed_out"):
                    self.stats.failed += 1
                    self._failed_delta += 1

    async def _run_handler(
        self, definition: TaskDefinition | None, running: Running
    ) -> tuple[Any, TaskFailure | None, str]:
        claimed, ctx = running.claimed, running.ctx
        try:
            if definition is None:
                return (
                    None,
                    TaskFailure(
                        FailureKind.ERROR,
                        "UnknownTaskType",
                        f"no handler for task type {claimed.task_type!r} on worker {self.name}",
                        retryable=True,
                    ),
                    "failed",
                )
            if claimed.workflow_run_id is not None:
                ctx.workflow = await self._db_retry(
                    lambda: self._workflow_context(claimed.id), op="workflow_context", budget=10.0
                )
            try:
                payload = definition.payload_model.model_validate(claimed.payload)
            except ValidationError as exc:
                return (
                    None,
                    TaskFailure(
                        FailureKind.ERROR,
                        "PayloadValidationError",
                        f"payload no longer matches the handler schema: {exc.errors()[:3]}",
                        retryable=False,
                    ),
                    "failed",
                )
            ctx.log.info("attempt started", attempt=claimed.attempt_number, worker=self.name)
            running.handler_task = asyncio.create_task(self._invoke(definition, ctx, payload))
            done, _ = await asyncio.wait({running.handler_task}, timeout=claimed.timeout_seconds)
            if not done:
                running.cancel_reason = running.cancel_reason or "timeout"
                ctx.mark_cancelled()
                running.handler_task.cancel()
                await asyncio.wait({running.handler_task}, timeout=5)
                ctx.log.error("handler timed out", timeout_seconds=claimed.timeout_seconds)
                return (
                    None,
                    TaskFailure(
                        FailureKind.TIMEOUT,
                        "TimeoutError",
                        f"handler exceeded its timeout of {claimed.timeout_seconds}s",
                        retryable=True,
                    ),
                    "timed_out",
                )
            result = running.handler_task.result()
            encoded = orjson.dumps(result, default=str)
            if len(encoded) > MAX_RESULT_BYTES:
                raise ResultTooLarge(f"result is {len(encoded)} bytes (limit {MAX_RESULT_BYTES})")
            ctx.log.info("attempt succeeded")
            return orjson.loads(encoded), None, "succeeded"
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # the supervisor itself is being cancelled (forced exit)
            return self._cancelled_outcome(running)
        except TaskCancelled:
            return self._cancelled_outcome(running)
        except Exception as exc:  # noqa: BLE001 - every handler failure is recorded on the task, never dropped
            ctx.log.error("handler raised", error_type=type(exc).__name__, error=str(exc)[:1000])
            return (
                None,
                TaskFailure(
                    FailureKind.ERROR,
                    type(exc).__name__,
                    str(exc) or repr(exc),
                    traceback.format_exc(),
                    retryable=bool(getattr(exc, "retryable", True)),
                ),
                "failed",
            )

    @staticmethod
    def _cancelled_outcome(running: Running) -> tuple[Any, TaskFailure | None, str]:
        """The handler was stopped on purpose; ``cancel_reason`` says why."""
        if running.cancel_reason == "lease_lost":
            # Another attempt may already own the task: write nothing (the write would be fenced anyway).
            return None, None, "lost"
        if running.cancel_reason == "shutdown":
            running.ctx.log.warning("interrupted by worker shutdown; released for another worker")
            failure = TaskFailure(
                FailureKind.INTERRUPTED,
                "WorkerShutdown",
                "worker shut down before the task finished",
            )
            return None, failure, "interrupted"
        running.ctx.log.warning("cancelled on request")
        failure = TaskFailure(
            FailureKind.ERROR, "Cancelled", "cancelled on request", retryable=False
        )
        return None, failure, "cancelled"

    async def _invoke(self, definition: TaskDefinition, ctx: TaskContext, payload: Any) -> Any:
        if definition.executor == "async":
            return await definition.func(ctx, payload)
        loop = asyncio.get_running_loop()
        if definition.executor == "process" and self._process_pool is not None:
            return await loop.run_in_executor(self._process_pool, definition.func, payload)
        if definition.executor == "process":
            return await loop.run_in_executor(self._thread_pool, definition.func, payload)
        return await loop.run_in_executor(
            self._thread_pool, definition.func, ctx.sync(loop), payload
        )

    async def _workflow_context(self, task_id: uuid.UUID) -> dict[str, Any] | None:
        async with self.db.session() as session:
            return await workflow_engine.workflow_context(session, task_id)

    async def _finish(
        self, claimed: ClaimedTask, result: Any, failure: TaskFailure | None
    ) -> FinishResult:
        async def write() -> FinishResult:
            async with self.db.transaction() as uow:
                if failure is None:
                    return await task_service.complete_task(
                        uow,
                        task_id=claimed.id,
                        lease_token=claimed.lease_token,
                        worker_id=self.id,
                        result=result,
                        workflow_run_id=claimed.workflow_run_id,
                    )
                return await task_service.fail_task(
                    uow,
                    task_id=claimed.id,
                    lease_token=claimed.lease_token,
                    worker_id=self.id,
                    failure=failure,
                    workflow_run_id=claimed.workflow_run_id,
                )

        # Keep trying through a short PostgreSQL outage, up to roughly one lease period.
        return await self._db_retry(
            write, op="finish", budget=max(self.settings.lease_ttl_seconds, 10.0)
        )

    async def _progress(
        self, task_id: uuid.UUID, lease_token: uuid.UUID, percent: int, message: str | None
    ) -> None:
        try:
            async with self.db.transaction() as uow:
                await task_service.update_progress(
                    uow, task_id=task_id, lease_token=lease_token, progress=percent, message=message
                )
        except Exception as exc:
            if not is_transient_db_error(exc):
                raise
            log.warning("progress_update_failed", task_id=str(task_id), error=str(exc)[:200])

    async def _db_retry(self, fn: Callable[[], Awaitable[T]], *, op: str, budget: float) -> T:
        """Retry ``fn`` on transient database errors for up to ``budget`` seconds (exponential backoff)."""
        deadline = time.monotonic() + budget
        delay = 0.1
        attempt = 0
        while True:
            attempt += 1
            try:
                return await fn()
            except Exception as exc:
                if (
                    not is_transient_db_error(exc)
                    or self._force
                    or time.monotonic() + delay > deadline
                ):
                    raise
                log.warning(
                    "db_retry",
                    op=op,
                    attempt=attempt,
                    retry_in=round(delay, 2),
                    error=str(exc)[:200],
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 3.0)

    # ------------------------------------------------------------------------------------------------ cancellation
    def _cancel_running(self, task_id: uuid.UUID, reason: str) -> None:
        running = self.inflight.get(task_id)
        if running is None or running.cancel_reason is not None:
            return
        if running.handler_task is not None and running.handler_task.done():
            return
        running.cancel_reason = reason
        running.ctx.mark_cancelled()
        if running.handler_task is not None:
            running.handler_task.cancel()
        log.info("task_cancel_signalled", task_id=str(task_id), reason=reason)

    # ------------------------------------------------------------------------------------------------ heartbeat
    def _resource_usage(self) -> tuple[float | None, int | None]:
        try:
            children = self._proc.children(recursive=True)
            live = {c.pid for c in children}
            for child in children:
                if child.pid not in self._children:
                    self._children[child.pid] = child
                    child.cpu_percent(None)
            for pid in list(self._children):
                if pid not in live:
                    del self._children[pid]
            procs = [self._proc, *self._children.values()]
            cpu = sum(p.cpu_percent(None) for p in procs) / self._cpu_count
            rss = sum(p.memory_info().rss for p in procs)
            return round(cpu, 1), int(rss)
        except psutil.Error:
            return None, None

    async def _heartbeat_loop(self) -> None:
        interval = self.settings.worker_heartbeat_interval_seconds
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._heartbeat_now.wait(), timeout=interval)
            self._heartbeat_now.clear()
            if self._stop.is_set():
                return
            await self.heartbeat_once()

    async def heartbeat_once(self) -> None:
        tokens = {r.claimed.lease_token: r for r in list(self.inflight.values())}
        cpu, rss = self._resource_usage()
        succeeded, failed = self._succeeded_delta, self._failed_delta
        try:
            async with self.db.transaction() as uow:
                reply = await worker_service.heartbeat(
                    uow,
                    worker_id=self.id,
                    active_tasks=len(self.inflight),
                    cpu_percent=cpu,
                    memory_rss_bytes=rss,
                    succeeded_delta=succeeded,
                    failed_delta=failed,
                )
                held = await task_service.renew_leases(
                    uow.session,
                    worker_id=self.id,
                    lease_tokens=list(tokens),
                    ttl_seconds=self.settings.lease_ttl_seconds,
                )
        except NotFound:
            log.warning("worker_row_missing_reregistering")
            await self._reregister()
            return
        except Exception as exc:
            if not is_transient_db_error(exc):
                raise
            log.warning("heartbeat_failed", error=str(exc)[:200])
            return
        self._succeeded_delta -= succeeded
        self._failed_delta -= failed
        for token, running in tokens.items():
            state = held.get(token)
            if state is None:
                self._cancel_running(running.claimed.id, "lease_lost")
            elif state is TaskState.CANCEL_REQUESTED:
                self._cancel_running(running.claimed.id, "cancel_requested")
        if reply.drain_requested != self.draining:
            self.draining = reply.drain_requested
            log.info("worker_drain_state", draining=self.draining)
        if self.draining and self.settings.worker_exit_when_drained and not self.inflight:
            log.info("worker_drained_exiting")
            self.request_stop()

    async def _reregister(self) -> None:
        async with self.db.transaction() as uow:
            await worker_service.register_worker(
                uow,
                worker_id=self.id,
                name=self.name,
                hostname=socket.gethostname(),
                pid=os.getpid(),
                version=__version__,
                queues=self.queues,
                task_types=self.registry.names(),
                concurrency=self.concurrency,
                metadata={"consumer": self.consumer, "cpu_count": self._cpu_count},
            )

    # ------------------------------------------------------------------------------------------------ reclaim
    async def _reclaim_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep_or_stop(self.settings.worker_reclaim_interval_seconds)
            if self._stop.is_set() or self.draining:
                continue
            await self.reclaim_once()

    async def reclaim_once(self) -> int:
        reclaimed = 0
        for stream in self.streams:
            free = self.concurrency - self._busy
            if free <= 0:
                break
            cursor = self._reclaim_cursors.get(stream, "0-0")
            try:
                next_cursor, messages = await self.broker.autoclaim(
                    stream, self.consumer, self.settings.worker_reclaim_min_idle_ms, free, cursor
                )
            except (RedisError, OSError) as exc:
                log.warning("reclaim_failed", stream=stream, error=str(exc)[:200])
                break
            self._reclaim_cursors[stream] = next_cursor
            for message in messages:
                metrics.MESSAGES_RECLAIMED.inc()
                reclaimed += 1
                log.info("pending_entry_reclaimed", stream=stream, message_id=message.message_id)
                self._start_handling(message)
        return reclaimed

    # ------------------------------------------------------------------------------------------------ logs
    async def _log_flush_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep_or_stop(1.0)
            await self._flush_logs()

    async def _flush_logs(self) -> None:
        rows = self._logs.drain()
        if not rows:
            return
        try:
            async with self.db.transaction() as uow:
                # Core execution (no ORM bulk processing); returned ids are matched to rows by position.
                conn = await uow.session.connection()
                result = await conn.execute(
                    insert(TaskLog).returning(
                        TaskLog.id, TaskLog.task_id, sort_by_parameter_order=True
                    ),
                    rows,
                )
                ids = result.all()
        except Exception as exc:
            if not is_transient_db_error(exc):
                raise
            log.warning("log_flush_failed", rows=len(rows), error=str(exc)[:200])
            if len(self._logs) + len(rows) <= MAX_PENDING_LOG_ROWS:
                for row in rows:
                    self._logs.append(row)
            return
        by_task: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        for (log_id, task_id), row in zip(ids, rows, strict=True):
            by_task[task_id].append(
                {
                    "id": log_id,
                    "attempt_number": row["attempt_number"],
                    "level": row["level"],
                    "message": row["message"],
                    "fields": row["fields"],
                    "logged_at": row["logged_at"].isoformat(),
                }
            )
        prefix = self.settings.redis_prefix
        await self.publisher.publish(
            [],
            [
                (
                    f"{prefix}:events:tasklog:{task_id}",
                    {"type": "task.log", "task_id": str(task_id), "lines": lines},
                )
                for task_id, lines in by_task.items()
            ],
        )

    # ------------------------------------------------------------------------------------------------ control
    async def _control_loop(self) -> None:
        channel = worker_control_channel(str(self.id), self.settings.redis_prefix)
        backoff = 0.5
        while not self._stop.is_set():
            try:
                async with self.redis.pubsub(ignore_subscribe_messages=True) as pubsub:
                    await pubsub.subscribe(channel)
                    backoff = 0.5
                    while not self._stop.is_set():
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if message is None:
                            continue
                        self._on_control(message.get("data"))
            except (RedisError, OSError) as exc:
                log.warning("control_channel_failed", error=str(exc)[:200], retry_in=backoff)
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, 10.0)

    def _on_control(self, raw: Any) -> None:
        try:
            data = orjson.loads(raw)
        except (orjson.JSONDecodeError, TypeError):
            log.warning("bad_control_message")
            return
        kind = data.get("type")
        if kind == "cancel":
            try:
                self._cancel_running(uuid.UUID(str(data.get("task_id"))), "cancel_requested")
            except ValueError:
                log.warning("bad_control_message")
        elif kind in ("drain", "resume"):
            # The database flag is authoritative; an immediate heartbeat picks it up.
            self._heartbeat_now.set()

    # ------------------------------------------------------------------------------------------------ queries used by tests
    async def task_logs(self, task_id: uuid.UUID) -> list[Any]:
        async with self.db.session() as session:
            return await task_queries.list_logs(session, task_id)
