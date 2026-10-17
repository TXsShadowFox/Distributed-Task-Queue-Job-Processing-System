"""The Python SDK against a real API server, worker and control plane."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from flowforge_scheduler.service import ControlPlane
from flowforge_sdk import (
    AsyncFlowForgeClient,
    FlowForgeClient,
    PermissionDeniedError,
    ValidationError,
)
from flowforge_worker.runtime import Worker
from tests.integration.conftest import register_user

pytestmark = pytest.mark.integration


async def _api_key(base_url: str, scopes: list[str]) -> str:
    async with httpx.AsyncClient(base_url=base_url) as http:
        user = await register_user(http)
        resp = await user.post("/api-keys", json={"name": "sdk", "scopes": scopes})
        return str(resp.json()["secret"])


async def test_sync_sdk_end_to_end(
    live_server: str, worker: Worker, control_plane: ControlPlane
) -> None:
    key = await _api_key(live_server, ["tasks:read", "tasks:write"])

    def run() -> tuple[str, str, int]:
        with FlowForgeClient(base_url=live_server, api_key=key) as client:
            job = client.submit(
                task_type="generate_report",
                payload={"report_id": 42, "rows": 500},
                priority=5,
                max_retries=3,
            )
            done = client.wait(job.id, timeout=30)
            replay = client.submit(
                "generate_report", {"report_id": 42, "rows": 500}, idempotency_key="nightly"
            )
            again = client.submit(
                "generate_report", {"report_id": 42, "rows": 500}, idempotency_key="nightly"
            )
            assert replay.id == again.id
            listed = list(client.iter_tasks(task_type="generate_report", limit=1))
            return done.state, str(done.result["report_id"]), len(listed)

    state, report_id, listed = await asyncio.to_thread(run)
    assert state == "SUCCEEDED" and report_id == "42" and listed == 2


async def test_async_sdk_errors_and_scopes(
    live_server: str, worker: Worker, control_plane: ControlPlane
) -> None:
    read_only = await _api_key(live_server, ["tasks:read"])
    async with AsyncFlowForgeClient(base_url=live_server, api_key=read_only) as client:
        with pytest.raises(PermissionDeniedError):
            await client.submit("demo.noop", {})
        page = await client.list()
        assert page.items == [] and page.has_more is False  # read scope works

    writer = await _api_key(live_server, ["tasks:read", "tasks:write"])
    async with AsyncFlowForgeClient(base_url=live_server, api_key=writer) as client:
        with pytest.raises(ValidationError) as exc:
            await client.submit("demo.sleep", {"seconds": "a while"})
        assert exc.value.details and exc.value.request_id
        job = await client.submit("demo.sleep", {"seconds": 0.1, "report_progress": False})
        done = await client.wait(job.id, timeout=20, poll_interval=0.1)
        assert done.result == {"slept_seconds": 0.1}
        logs = await client.logs(job.id)
        assert logs and logs[0]["level"] == "INFO"
