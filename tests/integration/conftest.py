from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import select, text

from flowforge_api.app import create_app
from flowforge_core.config import Settings, get_settings
from flowforge_core.db import migrate
from flowforge_core.db.models import Task
from flowforge_core.db.session import create_engine, create_sessionmaker
from flowforge_core.db.uow import Database
from flowforge_core.events import EventPublisher
from flowforge_core.redis_client import create_redis
from flowforge_core.services.task_types import register_task_types
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.context import TaskContext
from flowforge_worker.errors import RetryableError
from flowforge_worker.handlers import builtin_registry
from flowforge_worker.registry import TaskRegistry
from flowforge_worker.runtime import Worker

pytestmark = pytest.mark.integration

os.environ.setdefault("FLOWFORGE_WORKER_CPU_PROCESSES", "0")

# ------------------------------------------------------------------------------------------------ test handlers
test_handlers = TaskRegistry()
EXECUTIONS: dict[str, int] = {}


class EchoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = None


@test_handlers.task(
    "test.echo", payload=EchoPayload, max_retries=0, timeout=10, example={"value": 1}
)
async def echo(ctx: TaskContext, payload: EchoPayload) -> Any:
    EXECUTIONS[str(ctx.task_id)] = EXECUTIONS.get(str(ctx.task_id), 0) + 1
    ctx.log.info("echo", value=payload.value)
    return {"value": payload.value, "attempt": ctx.attempt}


class BlockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(default=30, ge=0, le=600)


@test_handlers.task(
    "test.block", payload=BlockPayload, max_retries=3, timeout=120, example={"seconds": 1}
)
async def block(ctx: TaskContext, payload: BlockPayload) -> dict[str, Any]:
    EXECUTIONS[str(ctx.task_id)] = EXECUTIONS.get(str(ctx.task_id), 0) + 1
    await asyncio.sleep(payload.seconds)
    return {"blocked": payload.seconds, "attempt": ctx.attempt}


class FailOncePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failures: int = Field(default=1, ge=0, le=10)


@test_handlers.task(
    "test.fail_n", payload=FailOncePayload, max_retries=3, timeout=10, example={"failures": 1}
)
async def fail_n(ctx: TaskContext, payload: FailOncePayload) -> dict[str, Any]:
    if ctx.attempt <= payload.failures:
        raise RetryableError(f"planned failure {ctx.attempt}/{payload.failures}")
    return {"succeeded_on_attempt": ctx.attempt}


def make_registry() -> TaskRegistry:
    registry = builtin_registry()
    registry.include(test_handlers)
    return registry


# ------------------------------------------------------------------------------------------------ infrastructure
@pytest.fixture(scope="session")
def settings() -> Settings:
    if os.environ.get("FLOWFORGE_TEST_SERVICES_RESOLVED") != "1":
        pytest.skip(
            "integration tests need PostgreSQL and Redis: run `python scripts/devstack.py up` or set "
            "FLOWFORGE_TEST_DATABASE_URL / FLOWFORGE_TEST_REDIS_URL"
        )
    get_settings.cache_clear()
    resolved = get_settings()
    # The suite truncates every table between tests: refuse to run against anything but a test database.
    db_name = resolved.database_url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in db_name:
        pytest.exit(
            f"refusing to run integration tests against non-test database {db_name!r}", returncode=2
        )
    return resolved


@pytest_asyncio.fixture(scope="session")
async def schema(settings: Settings) -> None:
    # Alembic's env.py calls asyncio.run(), so it runs in its own thread (and therefore its own event loop).
    try:
        await asyncio.to_thread(migrate.downgrade, settings.database_url, "base")
        await asyncio.to_thread(migrate.upgrade, settings.database_url)
    except OSError as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest_asyncio.fixture(scope="session")
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = create_redis(settings, client_name="flowforge-tests")
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis unavailable: {exc}")
    yield client
    await client.aclose()


@pytest_asyncio.fixture(scope="session")
async def db(settings: Settings, schema: None, redis: Redis) -> AsyncIterator[Database]:
    engine = create_engine(settings, application_name="flowforge-tests")
    database = Database(
        engine, create_sessionmaker(engine), EventPublisher(redis, settings.redis_prefix)
    )
    yield database
    await engine.dispose()


@pytest_asyncio.fixture
async def clean(db: Database, redis: Redis) -> AsyncIterator[None]:
    async with db.session() as session:
        tables = (
            await session.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        ).all()
        await session.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        await session.commit()
    await redis.flushdb()
    EXECUTIONS.clear()
    yield


@pytest_asyncio.fixture
async def client(settings: Settings, clean: None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@dataclass
class ApiUser:
    client: httpx.AsyncClient
    token: str
    user: dict[str, Any]
    org_id: str
    project_id: str
    extra_headers: dict[str, str] = field(default_factory=dict)

    def headers(self, project_id: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Project-ID": project_id or self.project_id,
            **self.extra_headers,
        }

    async def request(
        self, method: str, path: str, *, project_id: str | None = None, **kw: Any
    ) -> httpx.Response:
        headers = {**self.headers(project_id), **kw.pop("headers", {})}
        return await self.client.request(method, f"/api/v1{path}", headers=headers, **kw)

    async def get(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kw)

    async def put(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("PUT", path, **kw)

    async def delete(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kw)


async def register_user(
    client: httpx.AsyncClient,
    email: str | None = None,
    password: str = "correct-horse-battery",
    org: str | None = None,
) -> ApiUser:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "Test User",
            "organization_name": org,
        },
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    membership = me.json()["memberships"][0]
    return ApiUser(
        client=client,
        token=token,
        user=me.json()["user"],
        org_id=membership["organization_id"],
        project_id=membership["projects"][0]["id"],
    )


@pytest_asyncio.fixture
async def user(client: httpx.AsyncClient) -> ApiUser:
    return await register_user(client)


@pytest_asyncio.fixture
async def catalog(db: Database, clean: None) -> None:
    """Publish the test task-type catalog without starting a worker (workers normally do this at start-up)."""
    async with db.transaction() as uow:
        await register_task_types(
            uow.session, [d.catalog_entry() for d in make_registry().definitions()]
        )


@pytest_asyncio.fixture
async def worker_factory(
    settings: Settings, clean: None
) -> AsyncIterator[Callable[..., Awaitable[Worker]]]:
    started: list[tuple[Worker, asyncio.Task[None]]] = []

    async def start(*, settings_override: dict[str, Any] | None = None, **kwargs: Any) -> Worker:
        s = settings.model_copy(update=settings_override or {})
        worker = Worker(
            s,
            make_registry(),
            install_signal_handlers=False,
            concurrency=kwargs.pop("concurrency", 4),
            name=kwargs.pop("name", "test-worker"),
            **kwargs,
        )
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(worker.wait_started(), timeout=15)
        started.append((worker, task))
        return worker

    yield start
    for worker, _ in started:
        worker.request_stop()
    for _, task in started:
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=20)


@pytest_asyncio.fixture
async def worker(worker_factory: Callable[..., Awaitable[Worker]]) -> Worker:
    return await worker_factory()


@pytest_asyncio.fixture
async def control_plane(settings: Settings, clean: None) -> AsyncIterator[ControlPlane]:
    plane = ControlPlane(settings)
    task = asyncio.create_task(plane.run())
    await asyncio.wait_for(plane.started.wait(), timeout=15)
    yield plane
    plane.request_stop()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=20)


# ------------------------------------------------------------------------------------------------ helpers
async def wait_for_task(
    db: Database, task_id: str | uuid.UUID, states: set[str], timeout: float = 20.0
) -> Task:
    deadline = asyncio.get_running_loop().time() + timeout
    last: str | None = None
    while True:
        async with db.session() as session:
            task = await session.scalar(select(Task).where(Task.id == uuid.UUID(str(task_id))))
        if task is not None:
            last = task.state
            if task.state in states:
                return task
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"task {task_id} did not reach {states} within {timeout}s (last state {last})"
            )
        await asyncio.sleep(0.05)


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], timeout: float = 20.0, message: str = ""
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(message or "condition not met in time")
        await asyncio.sleep(0.05)


# ------------------------------------------------------------------------------------------------ live server
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest_asyncio.fixture
async def live_server(settings: Settings, clean: None) -> AsyncIterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_config=None, lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, timeout=10)
