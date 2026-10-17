"""Fault injection against real processes and real connections.

Guarantees under test (docs/architecture/reliability.md):
* a task whose worker dies is recovered after its lease expires and completes elsewhere;
* partitions from Redis or PostgreSQL delay work but lose none of it, and the services heal without a restart;
* a Redis restart that loses its data is repaired by the reconciler from PostgreSQL;
* any number of scheduler replicas, even ones killed mid-cycle, fire each schedule time exactly once.

Delivery is at-least-once: a task may run twice when a crash or partition hides its first completion, so these tests
assert "no task lost, each ends SUCCEEDED once", never "each handler ran exactly once".
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from flowforge_core.config import Settings
from flowforge_core.db.models import Task, TaskAttempt
from flowforge_core.db.uow import Database
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.runtime import Worker
from tests.chaos.processes import ServiceProcess
from tests.chaos.proxy import ChaosProxy
from tests.integration.conftest import ApiUser, wait_for_task, wait_until

pytestmark = pytest.mark.timeout(240)


# ------------------------------------------------------------------------------------------------ helpers
async def submit(user: ApiUser, task_type: str, payload: dict[str, object], **extra: object) -> str:
    resp = await user.post("/tasks", json={"task_type": task_type, "payload": payload, **extra})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def attempts_of(db: Database, task_id: str) -> list[TaskAttempt]:
    async with db.session() as session:
        rows = await session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == uuid.UUID(task_id))
            .order_by(TaskAttempt.attempt_number)
        )
        return list(rows.all())


async def states(db: Database, ids: list[str]) -> dict[str, int]:
    async with db.session() as session:
        rows = await session.execute(
            select(Task.state, func.count())
            .where(Task.id.in_([uuid.UUID(i) for i in ids]))
            .group_by(Task.state)
        )
        return {state: int(n) for state, n in rows.all()}


async def assert_all_succeeded_once(db: Database, ids: list[str], timeout: float = 90) -> None:
    async def done() -> bool:
        return (await states(db, ids)).get("SUCCEEDED", 0) == len(ids)

    await wait_until(
        done, timeout=timeout, message=f"not all tasks succeeded: {await states(db, ids)}"
    )
    async with db.session() as session:
        succeeded_attempts = await session.scalar(
            select(func.count())
            .select_from(TaskAttempt)
            .where(
                TaskAttempt.task_id.in_([uuid.UUID(i) for i in ids]),
                TaskAttempt.status == "SUCCEEDED",
            )
        )
    # Exactly one successful attempt per task: retries after a fault never record a second success.
    assert succeeded_attempts == len(ids)


@pytest_asyncio.fixture
async def proxies(settings: Settings) -> AsyncIterator[Callable[[str], Awaitable[ChaosProxy]]]:
    opened: list[ChaosProxy] = []

    async def open_for(url: str) -> ChaosProxy:
        parts = urlsplit(url.replace("postgresql+asyncpg", "postgresql"))
        proxy = await ChaosProxy(parts.hostname or "127.0.0.1", parts.port or 0).start()
        opened.append(proxy)
        return proxy

    yield open_for
    for proxy in opened:
        await proxy.close()


@pytest.fixture
def services(tmp_path: Path) -> Iterator[Callable[..., ServiceProcess]]:
    started: list[ServiceProcess] = []

    def spawn(
        module: str, *args: str, name: str, env: dict[str, str] | None = None
    ) -> ServiceProcess:
        proc = ServiceProcess(module, *args, log_dir=tmp_path, name=name, env=env).start()
        started.append(proc)
        return proc

    yield spawn
    for proc in started:
        proc.stop()


@contextlib.asynccontextmanager
async def control_plane_with(settings: Settings) -> AsyncIterator[ControlPlane]:
    plane = ControlPlane(settings)
    task = asyncio.create_task(plane.run())
    await asyncio.wait_for(plane.started.wait(), timeout=15)
    try:
        yield plane
    finally:
        plane.request_stop()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=20)


# ------------------------------------------------------------------------------------------------ worker crash
async def test_worker_killed_mid_task_is_recovered_by_another_worker(
    user: ApiUser,
    db: Database,
    catalog: None,
    control_plane: ControlPlane,
    services: Callable[..., ServiceProcess],
) -> None:
    doomed = services("flowforge_worker", "--concurrency", "2", name="worker-doomed")
    task_id = await submit(user, "demo.sleep", {"seconds": 4}, max_retries=2)
    await wait_for_task(db, task_id, {"RUNNING"}, timeout=30)

    doomed.kill()  # mid-attempt: no completion, no lease release, no goodbye
    assert not doomed.running

    rescuer = services("flowforge_worker", "--concurrency", "2", name="worker-rescuer")
    try:
        await wait_for_task(db, task_id, {"SUCCEEDED"}, timeout=60)
    except AssertionError:
        print(rescuer.log_tail())  # noqa: T201 - shown only when the test fails
        raise
    attempts = await attempts_of(db, task_id)
    assert [a.status for a in attempts] == ["LOST", "SUCCEEDED"]
    assert attempts[0].worker_id != attempts[1].worker_id


# ------------------------------------------------------------------------------------------------ Redis
async def test_redis_partition_delays_but_loses_nothing(
    settings: Settings,
    user: ApiUser,
    db: Database,
    catalog: None,
    worker_factory: Callable[..., Awaitable[Worker]],
    proxies: Callable[[str], Awaitable[ChaosProxy]],
) -> None:
    proxy = await proxies(settings.redis_url)
    partitioned = {"redis_url": proxy.url_for(settings.redis_url)}
    worker = await worker_factory(settings_override=partitioned, concurrency=8)
    async with control_plane_with(settings.model_copy(update=partitioned)):
        before = [await submit(user, "test.echo", {"value": i}) for i in range(15)]
        await proxy.cut()
        during = [
            await submit(user, "test.echo", {"value": i}) for i in range(15)
        ]  # API itself is unaffected
        await asyncio.sleep(
            4
        )  # longer than the worker's heartbeat interval and lease TTL in the test config
        proxy.restore()
        after = [await submit(user, "test.echo", {"value": i}) for i in range(15)]
        await assert_all_succeeded_once(db, before + during + after)
    assert not worker.stopping, "the worker must ride out the partition, not exit"


async def test_redis_restart_without_data_is_repaired_by_reconciler(
    user: ApiUser,
    db: Database,
    redis: Redis,
    catalog: None,
    worker_factory: Callable[..., Awaitable[Worker]],
    control_plane: ControlPlane,
) -> None:
    # Dispatched while no worker runs, then Redis "restarts" empty: every stream entry is gone.
    ids = [await submit(user, "test.echo", {"value": i}) for i in range(10)]
    await redis.flushdb()
    await worker_factory(concurrency=8)
    await assert_all_succeeded_once(db, ids, timeout=60)


# ------------------------------------------------------------------------------------------------ PostgreSQL
async def test_postgres_partition_heals_without_restarting_services(
    settings: Settings,
    user: ApiUser,
    db: Database,
    catalog: None,
    worker_factory: Callable[..., Awaitable[Worker]],
    proxies: Callable[[str], Awaitable[ChaosProxy]],
) -> None:
    proxy = await proxies(settings.database_url)
    partitioned = {"database_url": proxy.url_for(settings.database_url)}
    worker = await worker_factory(settings_override=partitioned, concurrency=8)
    async with control_plane_with(settings.model_copy(update=partitioned)):
        ids = [await submit(user, "test.echo", {"value": i}) for i in range(20)]
        await asyncio.sleep(0.3)  # some tasks are mid-flight when the database disappears
        await proxy.cut()
        ids += [
            await submit(user, "test.echo", {"value": i}) for i in range(10)
        ]  # the API's own pool is fine
        await asyncio.sleep(4)
        proxy.restore()
        await assert_all_succeeded_once(db, ids, timeout=120)
    assert proxy.cuts == 1
    assert not worker.stopping, "the worker must reconnect, not exit"


# ------------------------------------------------------------------------------------------------ scheduler
async def test_scheduler_replicas_fire_exactly_once_even_when_one_is_killed(
    user: ApiUser,
    db: Database,
    catalog: None,
    services: Callable[..., ServiceProcess],
) -> None:
    # A yearly schedule made due now has exactly one fire time to process during the test.
    resp = await user.post(
        "/schedules",
        json={
            "name": "chaos",
            "cron_expression": "0 0 1 1 *",
            "timezone": "UTC",
            "task_type": "test.echo",
            "payload": {"value": "tick"},
        },
    )
    assert resp.status_code == 201, resp.text
    schedule_id = resp.json()["id"]
    async with db.session() as session:
        await session.execute(
            text("UPDATE schedules SET next_run_at = now() - interval '5 seconds' WHERE id = :id"),
            {"id": uuid.UUID(schedule_id)},
        )
        await session.commit()

    replicas = [services("flowforge_scheduler", name=f"scheduler-{i}") for i in range(3)]
    await asyncio.sleep(0.4)
    replicas[
        0
    ].kill()  # possibly mid-transaction: its work must roll back or commit, never half-apply

    async def fired() -> int:
        async with db.session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.schedule_id == uuid.UUID(schedule_id))
            )
        return int(count or 0)

    await wait_until(lambda: _at_least(fired, 1), timeout=30, message="schedule never fired")
    replicas[1].kill()
    replicas.append(services("flowforge_scheduler", name="scheduler-restarted"))
    await asyncio.sleep(3)  # restarted and surviving replicas both scan for due schedules
    assert await fired() == 1


async def _at_least(probe: Callable[[], Awaitable[int]], n: int) -> bool:
    return await probe() >= n
