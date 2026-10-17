"""Reliability guarantees: leases, fencing, PEL reclaim, reconciliation, duplicate delivery, races, outbox behaviour
under Redis failure, graceful shutdown, draining, and the database-enforced state machine."""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import QueuePool

from flowforge_core.broker import CONSUMER_GROUP, StreamBroker
from flowforge_core.config import Settings
from flowforge_core.context import Actor
from flowforge_core.db.models import OutboxEvent, Task, TaskEvent
from flowforge_core.db.uow import Database
from flowforge_core.errors import InvalidStateTransition
from flowforge_core.services import tasks as task_service
from flowforge_core.services.tasks import TaskFailure, TaskSpec
from flowforge_scheduler.service import ControlPlane
from flowforge_shared.lifecycle import FailureKind
from flowforge_shared.messages import DispatchMessage, DispatchReason
from flowforge_shared.priority import PriorityBand, band_for_priority
from flowforge_shared.states import TASK_TRANSITIONS, TaskState
from flowforge_worker.runtime import POOL_HEADROOM, Worker
from tests.integration.conftest import EXECUTIONS, ApiUser, wait_for_task, wait_until

pytestmark = pytest.mark.integration

GHOST_WORKER = uuid.UUID(int=0xDEAD)


async def _submit(
    db: Database, project_id: str, task_type: str = "test.echo", **kw: object
) -> Task:
    spec = TaskSpec(
        task_type=task_type,
        payload=kw.pop("payload", {}),  # type: ignore[arg-type]
        queue="default",
        priority=int(kw.pop("priority", 5)),  # type: ignore[call-overload]
        max_retries=int(kw.pop("max_retries", 3)),  # type: ignore[call-overload]
        timeout_seconds=60,
        backoff_base_seconds=0.1,
        backoff_max_seconds=0.2,
    )
    async with db.transaction() as uow:
        [res] = await task_service.submit_tasks(
            uow, project_id=uuid.UUID(project_id), specs=[spec], actor=Actor("system")
        )
    return res.task


async def _register_ghost_worker(db: Database) -> None:
    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO workers (id, name, hostname, pid, version, queues, task_types, concurrency, status) "
                "VALUES (:id, 'ghost', 'ghost-host', 1, 'test', ARRAY['default'], ARRAY[]::varchar[], 1, 'ACTIVE') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": GHOST_WORKER},
        )
        await session.commit()


async def _claim_as_ghost(
    db: Database, task_id: uuid.UUID, ttl: float = 1.0
) -> task_service.ClaimedTask:
    await _register_ghost_worker(db)
    async with db.transaction() as uow:
        claimed = await task_service.claim_task(
            uow, task_id=task_id, worker_id=GHOST_WORKER, lease_ttl_seconds=ttl
        )
    assert claimed is not None
    return claimed


# ------------------------------------------------------------------------------------------------ state machine
def _legal_path(target: TaskState) -> list[TaskState]:
    """Shortest legal path CREATED -> ... -> target (BFS over the Python transition table)."""
    frontier: list[list[TaskState]] = [[TaskState.CREATED]]
    seen = {TaskState.CREATED}
    while frontier:
        path = frontier.pop(0)
        if path[-1] == target:
            return path
        for nxt in sorted(TASK_TRANSITIONS[path[-1]]):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append([*path, nxt])
    raise AssertionError(f"{target} unreachable")


_SET_STATE = text(
    "UPDATE tasks SET state = CAST(:s AS VARCHAR), "
    "lease_token = CASE WHEN CAST(:s AS VARCHAR) IN ('RUNNING','CANCEL_REQUESTED') THEN gen_random_uuid() END, "
    "available_at = CASE WHEN CAST(:s AS VARCHAR) IN ('SCHEDULED','RETRY_SCHEDULED') THEN now() END "
    "WHERE id = :id"
)
_INSERT_CREATED = text(
    "INSERT INTO tasks (id, project_id, task_type, queue, priority, state, payload, max_retries, "
    "timeout_seconds, backoff_base_seconds, backoff_max_seconds) "
    "VALUES (:id, :p, 'x', 'default', 5, 'CREATED', '{}', 0, 10, 1, 1)"
)


async def test_database_trigger_matches_python_transition_table(
    db: Database, user: ApiUser
) -> None:
    """Every (from, to) pair: the PostgreSQL trigger must allow exactly the transitions the Python table allows.

    Each source state is reached through legal transitions only (the application role cannot bypass triggers)."""
    mismatches = []
    for src, dst in itertools.product(TaskState, TaskState):
        if src == dst:
            continue
        async with db.session() as session:
            task_id = uuid.uuid4()
            await session.execute(_INSERT_CREATED, {"id": task_id, "p": uuid.UUID(user.project_id)})
            for step in _legal_path(src)[1:]:
                await session.execute(_SET_STATE, {"s": step.value, "id": task_id})
            allowed_in_db = True
            try:
                async with session.begin_nested():
                    await session.execute(_SET_STATE, {"s": dst.value, "id": task_id})
            except DBAPIError as exc:
                assert "illegal task transition" in str(exc)  # noqa: PT017 - try/except computes a boolean
                allowed_in_db = False
            await session.rollback()
        if allowed_in_db != (dst in TASK_TRANSITIONS[src]):
            mismatches.append((src.value, dst.value, allowed_in_db))
    assert mismatches == []


async def test_trigger_rejects_illegal_initial_state(db: Database, user: ApiUser) -> None:
    async with db.session() as session:
        with pytest.raises(DBAPIError, match="illegal initial task state"):
            await session.execute(
                text(
                    "INSERT INTO tasks (id, project_id, task_type, queue, priority, state, payload, max_retries, "
                    "timeout_seconds, backoff_base_seconds, backoff_max_seconds) VALUES (gen_random_uuid(), :p, "
                    "'x', 'default', 5, 'SUCCEEDED', '{}', 0, 10, 1, 1)"
                ),
                {"p": uuid.UUID(user.project_id)},
            )


# ------------------------------------------------------------------------------------------------ leases & fencing
async def test_expired_lease_is_recovered_and_task_completes(
    db: Database, user: ApiUser, catalog: None, control_plane: ControlPlane, worker_factory: object
) -> None:
    task = await _submit(db, user.project_id, "test.echo", payload={"value": "recovered"})
    # A worker claims the task and "crashes": it never heartbeats, so its lease expires.
    await _claim_as_ghost(db, task.id, ttl=1.0)
    # The healthy worker discards the stale stream entry, then runs the task once the reaper re-queues it.
    worker: Worker = await worker_factory()  # type: ignore[operator]
    final = await wait_for_task(db, task.id, {"SUCCEEDED"}, timeout=20)
    attempts = (await user.get(f"/tasks/{task.id}/attempts")).json()
    assert [a["status"] for a in attempts] == ["LOST", "SUCCEEDED"]
    assert attempts[0]["error_type"] == "LeaseExpired"
    assert final.retry_count == 1
    assert final.last_worker_id == worker.id


async def test_zombie_attempt_cannot_overwrite_new_attempt(
    db: Database, user: ApiUser, catalog: None, control_plane: ControlPlane
) -> None:
    task = await _submit(db, user.project_id, "test.echo")
    stale = await _claim_as_ghost(db, task.id, ttl=0.5)
    await wait_for_task(db, task.id, {"RETRY_SCHEDULED", "QUEUED"}, timeout=10)  # reaped
    await wait_for_task(db, task.id, {"QUEUED"}, timeout=10)
    fresh = await _claim_as_ghost(db, task.id, ttl=30)
    assert fresh.lease_token != stale.lease_token

    async with db.transaction() as uow:
        outcome = await task_service.complete_task(
            uow,
            task_id=task.id,
            lease_token=stale.lease_token,
            worker_id=GHOST_WORKER,
            result={"zombie": True},
        )
    assert not outcome.applied
    async with db.transaction() as uow:
        outcome = await task_service.fail_task(
            uow,
            task_id=task.id,
            lease_token=stale.lease_token,
            worker_id=GHOST_WORKER,
            failure=TaskFailure(FailureKind.ERROR, "Zombie", "late failure"),
        )
    assert not outcome.applied
    async with db.session() as session:
        current = await session.get(Task, task.id)
        fenced = await session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.event_type == "FENCED")
        )
    assert (
        current is not None
        and current.state == "RUNNING"
        and current.lease_token == fresh.lease_token
    )
    assert fenced == 2

    async with db.transaction() as uow:
        outcome = await task_service.complete_task(
            uow,
            task_id=task.id,
            lease_token=fresh.lease_token,
            worker_id=GHOST_WORKER,
            result={"ok": 1},
        )
    assert outcome.applied and outcome.state == TaskState.SUCCEEDED


async def test_cancel_requested_with_dead_worker_ends_cancelled(
    db: Database, user: ApiUser, catalog: None, control_plane: ControlPlane
) -> None:
    task = await _submit(db, user.project_id)
    await _claim_as_ghost(db, task.id, ttl=1.0)
    resp = await user.post(f"/tasks/{task.id}/cancel")
    assert resp.json()["state"] == "CANCEL_REQUESTED"
    final = await wait_for_task(db, task.id, {"CANCELLED"}, timeout=10)
    assert final.retry_count == 0  # cancellation is never turned into a retry


# ------------------------------------------------------------------------------------------------ races
async def _cancel(db: Database, project_id: uuid.UUID, task_id: uuid.UUID) -> str:
    try:
        async with db.transaction() as uow:
            task = await task_service.cancel_task(
                uow, project_id=project_id, task_id=task_id, actor=Actor("user")
            )
        return f"cancel:{task.state}"
    except InvalidStateTransition:
        return "cancel:conflict"


async def _complete(db: Database, claimed: task_service.ClaimedTask) -> str:
    async with db.transaction() as uow:
        result = await task_service.complete_task(
            uow,
            task_id=claimed.id,
            lease_token=claimed.lease_token,
            worker_id=GHOST_WORKER,
            result={},
        )
    return f"complete:{result.applied}"


async def _fail(db: Database, claimed: task_service.ClaimedTask) -> str:
    async with db.transaction() as uow:
        result = await task_service.fail_task(
            uow,
            task_id=claimed.id,
            lease_token=claimed.lease_token,
            worker_id=GHOST_WORKER,
            failure=TaskFailure(FailureKind.ERROR, "Boom", "boom"),
        )
    return f"fail:{result.applied}"


@pytest.mark.parametrize("cancel_first", [True, False])
async def test_concurrent_cancel_and_complete_are_consistent(
    db: Database, user: ApiUser, catalog: None, cancel_first: bool
) -> None:
    outcomes = []
    project_id = uuid.UUID(user.project_id)
    for _ in range(10):
        task = await _submit(db, user.project_id)
        claimed = await _claim_as_ghost(db, task.id, ttl=30)
        cancel = _cancel(db, project_id, task.id)
        complete = _complete(db, claimed)
        results = await asyncio.gather(
            *([cancel, complete] if cancel_first else [complete, cancel])
        )
        async with db.session() as session:
            final = await task_service.task_state(session, task.id)
        # Completion always wins (its side effects happened); cancel either preceded it or was rejected.
        assert final == TaskState.SUCCEEDED, results
        assert "complete:True" in results
        outcomes.append(tuple(sorted(results)))
    assert outcomes


async def test_concurrent_cancel_and_fail_never_retry_a_cancelled_task(
    db: Database, user: ApiUser, catalog: None
) -> None:
    project_id = uuid.UUID(user.project_id)
    for _ in range(10):
        task = await _submit(db, user.project_id, max_retries=3)
        claimed = await _claim_as_ghost(db, task.id, ttl=30)
        await asyncio.gather(_cancel(db, project_id, task.id), _fail(db, claimed))
        async with db.session() as session:
            final = await task_service.task_state(session, task.id)
        assert final == TaskState.CANCELLED


async def test_two_workers_never_both_claim_the_same_task(
    db: Database, user: ApiUser, catalog: None
) -> None:
    task = await _submit(db, user.project_id)
    await _register_ghost_worker(db)

    async def claim() -> object:
        async with db.transaction() as uow:
            return await task_service.claim_task(
                uow, task_id=task.id, worker_id=GHOST_WORKER, lease_ttl_seconds=30
            )

    results = await asyncio.gather(*(claim() for _ in range(20)))
    assert sum(1 for r in results if r is not None) == 1


# ------------------------------------------------------------------------------------------------ redis delivery
async def test_duplicate_stream_entries_execute_once(
    db: Database,
    user: ApiUser,
    catalog: None,
    control_plane: ControlPlane,
    worker: Worker,
    redis: Redis,
    settings: Settings,
) -> None:
    task = await _submit(db, user.project_id, payload={"value": "once"})
    await wait_for_task(db, task.id, {"SUCCEEDED"})
    broker = StreamBroker(redis, settings.redis_prefix)
    msg = DispatchMessage(
        task_id=task.id,
        project_id=task.project_id,
        queue="default",
        priority=5,
        reason=DispatchReason.RECONCILE,
        enqueued_at=datetime.now(UTC),
    )
    await broker.publish([(broker.stream_for("default", PriorityBand.NORMAL), msg.to_fields())] * 3)
    await wait_until(
        lambda: _async_true(worker.stats.duplicates >= 3),
        timeout=10,
        message="duplicates not discarded",
    )
    assert EXECUTIONS[str(task.id)] == 1


async def _async_true(value: bool) -> bool:
    return value


async def test_pending_entry_of_crashed_consumer_is_reclaimed(
    db: Database,
    user: ApiUser,
    catalog: None,
    control_plane: ControlPlane,
    redis: Redis,
    settings: Settings,
    worker_factory: object,
) -> None:
    task = await _submit(db, user.project_id, payload={"value": "reclaimed"})
    broker = StreamBroker(redis, settings.redis_prefix)
    stream = broker.stream_for("default", band_for_priority(5))
    await broker.ensure_group(stream)

    # Wait for the relay to publish, then read the entry as a consumer that "crashes" before claiming it.
    async def published() -> bool:
        return int(await redis.xlen(stream)) > 0

    await wait_until(published, timeout=10, message="relay did not publish")
    taken = await broker.read("crashed-consumer", [stream], 10, 100)
    assert [m.fields[b"task_id"].decode() for m in taken] == [str(task.id)]

    worker = await worker_factory()  # type: ignore[operator]
    final = await wait_for_task(db, task.id, {"SUCCEEDED"}, timeout=20)
    assert final.attempt_count == 1
    pending = await redis.xpending(stream, CONSUMER_GROUP)
    assert int(pending["pending"]) == 0
    assert worker.stats.claimed >= 1


async def test_unexpected_claim_error_keeps_entry_pending_until_fixed(
    db: Database,
    user: ApiUser,
    catalog: None,
    control_plane: ControlPlane,
    redis: Redis,
    settings: Settings,
    worker_factory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug in the claim path (e.g. a bad release) must not lose, fail or park tasks; it is counted for alerting."""

    def crashes() -> float:
        return REGISTRY.get_sample_value("flowforge_worker_handler_crashes_total") or 0.0

    async def broken_claim(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated bug in the claim path")

    real_claim = task_service.claim_task
    before = crashes()
    monkeypatch.setattr(task_service, "claim_task", broken_claim)
    task = await _submit(db, user.project_id, payload={"value": "poison"})
    worker: Worker = await worker_factory()  # type: ignore[operator]

    async def crashed_and_reclaimed() -> bool:
        # The first delivery and at least one reclaim of the same entry.
        return crashes() >= before + 2

    await wait_until(crashed_and_reclaimed, timeout=15, message="handler crashes were not counted")
    async with db.session() as session:
        assert await task_service.task_state(session, task.id) is TaskState.QUEUED
    stream = StreamBroker(redis, settings.redis_prefix).stream_for("default", band_for_priority(5))
    assert int((await redis.xpending(stream, CONSUMER_GROUP))["pending"]) == 1

    monkeypatch.setattr(task_service, "claim_task", real_claim)  # the fix is deployed
    final = await wait_for_task(db, task.id, {"SUCCEEDED"}, timeout=20)
    assert final.attempt_count == 1
    assert worker.stats.claimed == 1
    assert int((await redis.xpending(stream, CONSUMER_GROUP))["pending"]) == 0


async def test_reconciler_redispatches_tasks_lost_from_redis(
    db: Database,
    user: ApiUser,
    catalog: None,
    control_plane: ControlPlane,
    redis: Redis,
    settings: Settings,
    worker_factory: object,
) -> None:
    task = await _submit(db, user.project_id, payload={"value": "lost"})
    broker = StreamBroker(redis, settings.redis_prefix)
    stream = broker.stream_for("default", band_for_priority(5))

    async def published() -> bool:
        async with db.session() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
            )
        return pending == 0

    await wait_until(published, timeout=10, message="relay did not publish")
    await redis.delete(stream)  # simulate Redis losing the stream (e.g. restart without AOF)
    await worker_factory()  # type: ignore[operator]
    final = await wait_for_task(db, task.id, {"SUCCEEDED"}, timeout=30)
    assert final.attempt_count == 1
    async with db.session() as session:
        reconciled = await session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.event_type == "RECONCILED")
        )
    assert reconciled is not None and reconciled >= 1


async def test_outbox_rows_survive_redis_outage(
    db: Database, user: ApiUser, catalog: None, settings: Settings
) -> None:
    task = await _submit(db, user.project_id)
    plane = ControlPlane(settings)
    await plane.start()
    try:
        broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
        healthy = plane.broker
        plane.broker = StreamBroker(broken, settings.redis_prefix)
        with pytest.raises(Exception):  # noqa: B017 - any connection error
            await plane.relay_once()
        async with db.session() as session:
            unpublished = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == task.id, OutboxEvent.published_at.is_(None))
            )
        assert unpublished == 1  # rolled back, not lost
        plane.broker = healthy
        assert await plane.relay_once() == 1
        await broken.aclose()
    finally:
        await plane.shutdown()


# ------------------------------------------------------------------------------------------------ shutdown & drain
async def test_graceful_shutdown_releases_running_task_without_consuming_retry(
    db: Database, user: ApiUser, catalog: None, control_plane: ControlPlane, worker_factory: object
) -> None:
    w1: Worker = await worker_factory(settings_override={"worker_shutdown_grace_seconds": 0.5})  # type: ignore[operator]
    resp = await user.post(
        "/tasks", json={"task_type": "test.block", "payload": {"seconds": 2}, "max_retries": 0}
    )
    task_id = resp.json()["id"]
    await wait_for_task(db, task_id, {"RUNNING"})
    w1.request_stop()
    task = await wait_for_task(db, task_id, {"RETRY_SCHEDULED", "QUEUED"}, timeout=10)
    assert task.retry_count == 0  # interruption is not the task's fault
    await worker_factory()  # type: ignore[operator]
    final = await wait_for_task(db, task_id, {"SUCCEEDED"}, timeout=20)
    attempts = (await user.get(f"/tasks/{final.id}/attempts")).json()
    assert [a["status"] for a in attempts] == ["INTERRUPTED", "SUCCEEDED"]


async def test_drain_stops_new_work_and_resume_restarts_it(
    client: object, db: Database, catalog: None, control_plane: ControlPlane, worker: Worker
) -> None:
    from tests.integration.conftest import register_user

    admin = await register_user(client, email="admin@example.com")  # type: ignore[arg-type]
    assert admin.user["is_platform_admin"] is True
    drained = await admin.post(f"/workers/{worker.id}/drain")
    assert drained.status_code == 200 and drained.json()["status"] == "DRAINING"
    await wait_until(
        lambda: _async_true(worker.draining), timeout=5, message="worker did not drain"
    )

    task_id = (await admin.post("/tasks", json={"task_type": "test.echo", "payload": {}})).json()[
        "id"
    ]
    await asyncio.sleep(1.5)
    assert (await admin.get(f"/tasks/{task_id}")).json()["state"] == "QUEUED"

    resumed = await admin.post(f"/workers/{worker.id}/resume")
    assert resumed.json()["status"] == "ACTIVE"
    await wait_for_task(db, task_id, {"SUCCEEDED"}, timeout=10)


async def test_non_admin_cannot_drain_workers(user: ApiUser, worker: Worker) -> None:
    resp = await user.post(f"/workers/{worker.id}/drain")
    assert resp.status_code == 403


async def test_offline_worker_is_detected(
    db: Database, user: ApiUser, catalog: None, control_plane: ControlPlane
) -> None:
    await _register_ghost_worker(db)
    async with db.session() as session:
        await session.execute(
            text(
                "UPDATE workers SET last_heartbeat_at = now() - interval '1 minute' WHERE id = :id"
            ),
            {"id": GHOST_WORKER},
        )
        await session.commit()

    async def offline() -> bool:
        workers = (await user.get("/workers")).json()
        return any(w["id"] == str(GHOST_WORKER) and w["status"] == "OFFLINE" for w in workers)

    await wait_until(offline, timeout=10, message="worker not marked offline")
    notifications = (await user.get("/notifications")).json()
    assert any(n["category"] == "worker_offline" for n in notifications["items"])


async def test_worker_opens_its_database_pool_before_taking_work(worker_factory: object) -> None:
    worker: Worker = await worker_factory(concurrency=3)  # type: ignore[operator]
    pool = worker.db.engine.pool
    assert isinstance(pool, QueuePool)
    # One connection per execution slot plus the heartbeat and the log flush, all opened during start-up: a cold pool
    # would make the first burst of claims wait for connection set-up.
    assert pool.checkedin() + pool.checkedout() >= 3 + POOL_HEADROOM
