"""Realtime SSE (through a real uvicorn server), analytics computed from real data, notifications, health and
metrics endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import orjson
import pytest

from flowforge_core.db.uow import Database
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.runtime import Worker
from tests.integration.conftest import ApiUser, register_user, wait_for_task

pytestmark = pytest.mark.integration


async def _read_events(
    response: httpx.Response, until: Any, timeout: float = 15
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def consume() -> None:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(orjson.loads(line[6:]))
                if until(events):
                    return

    await asyncio.wait_for(consume(), timeout=timeout)
    return events


async def test_sse_stream_delivers_task_updates(
    live_server: str, catalog: None, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=30) as http:
        user = await register_user(http)
        headers = user.headers()
        async with http.stream("GET", "/api/v1/events/stream", headers=headers) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            await asyncio.sleep(0.3)  # subscription established
            task = (
                await user.post("/tasks", json={"task_type": "test.echo", "payload": {"value": 7}})
            ).json()

            def finished(events: list[dict[str, Any]]) -> bool:
                return any(
                    e.get("type") == "task.updated"
                    and e["data"].get("id") == task["id"]
                    and e["data"]["state"] == "SUCCEEDED"
                    for e in events
                )

            events = await _read_events(stream, finished)
    states = [
        e["data"]["state"]
        for e in events
        if e.get("type") == "task.updated" and e["data"].get("id") == task["id"]
    ]
    assert states[0] == "QUEUED" and states[-1] == "SUCCEEDED" and "RUNNING" in states
    assert any(e["type"] == "ready" for e in events)


async def test_sse_requires_authentication_and_project(live_server: str) -> None:
    async with httpx.AsyncClient(base_url=live_server) as http:
        assert (await http.get("/api/v1/events/stream")).status_code == 401
        user = await register_user(http)
        resp = await http.get(
            "/api/v1/events/stream", headers={"Authorization": f"Bearer {user.token}"}
        )
        assert resp.status_code == 422  # project header missing


async def test_task_log_stream_backfills_history(
    live_server: str, catalog: None, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    async with httpx.AsyncClient(base_url=live_server, timeout=30) as http:
        user = await register_user(http)
        task = (
            await user.post(
                "/tasks", json={"task_type": "test.echo", "payload": {"value": "logged"}}
            )
        ).json()
        await wait_for_task(db, task["id"], {"SUCCEEDED"})
        await asyncio.sleep(1.2)  # log flush interval
        async with http.stream(
            "GET", f"/api/v1/tasks/{task['id']}/logs/stream", headers=user.headers()
        ) as stream:
            events = await _read_events(
                stream, lambda ev: any(e.get("type") == "task.log" for e in ev)
            )
    lines = next(e for e in events if e.get("type") == "task.log")["lines"]
    assert any(line["message"] == "echo" for line in lines)


async def test_analytics_reflect_real_executions(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    ok = [
        (await user.post("/tasks", json={"task_type": "test.echo", "payload": {}})).json()["id"]
        for _ in range(6)
    ]
    bad = (
        await user.post("/tasks", json={"task_type": "demo.fail", "payload": {}, "max_retries": 0})
    ).json()["id"]
    for task_id in ok:
        await wait_for_task(db, task_id, {"SUCCEEDED"})
    await wait_for_task(db, bad, {"DEAD_LETTERED"})

    overview = (await user.get("/analytics/overview")).json()
    assert overview["succeeded_24h"] == 6 and overview["failed_24h"] == 1
    assert overview["tasks_by_state"] == {"SUCCEEDED": 6, "DEAD_LETTERED": 1}
    assert overview["dead_letter_open"] == 1
    assert overview["success_rate_24h"] == pytest.approx(6 / 7, abs=1e-3)
    assert overview["workers"]["active"] >= 1 and overview["avg_duration_ms_24h"] is not None

    throughput = (await user.get("/analytics/throughput", params={"window": "1h"})).json()
    assert sum(b["succeeded"] for b in throughput["series"]) == 6
    assert sum(b["submitted"] for b in throughput["series"]) == 7
    assert throughput["bucket_seconds"] == 60

    latency = (await user.get("/analytics/latency", params={"window": "1h"})).json()
    assert latency["execution"]["count"] == 6 and latency["execution"]["p50_ms"] is not None
    assert latency["queue_wait"]["p95_ms"] is not None
    assert {t["task_type"] for t in latency["by_task_type"]} == {"test.echo"}
    # Both series cover every bucket of the window, so charts can share a time axis.
    assert latency["bucket_seconds"] == 60
    assert [b["ts"] for b in latency["series"]] == [b["ts"] for b in throughput["series"]]
    assert any(b["exec_p50_ms"] is not None for b in latency["series"])
    assert any(b["exec_p50_ms"] is None for b in latency["series"])

    failures = (await user.get("/analytics/failures", params={"window": "1h"})).json()
    demo = next(t for t in failures["by_task_type"] if t["task_type"] == "demo.fail")
    assert demo["failed"] == 1 and demo["failure_rate"] == 1.0
    assert failures["recent"][0]["task_id"] == bad

    utilization = (await user.get("/analytics/workers", params={"window": "1h"})).json()
    me = next(w for w in utilization["workers"] if w["worker_id"] == str(worker.id))
    assert me["attempts"] >= 7 and me["busy_seconds"] > 0 and 0 <= me["utilization"] <= 1
    # The worker started moments ago: utilization is relative to its online time, not the whole hour.
    assert 0 < me["online_seconds"] < 600
    assert me["utilization"] == pytest.approx(
        min(1.0, me["busy_seconds"] / (me["concurrency"] * me["online_seconds"])), abs=1e-3
    )


async def test_notifications_feed_and_read_marker(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    bad = (
        await user.post("/tasks", json={"task_type": "demo.fail", "payload": {}, "max_retries": 0})
    ).json()["id"]
    await wait_for_task(db, bad, {"DEAD_LETTERED"})
    feed = (await user.get("/notifications")).json()
    assert feed["unread_count"] == 1 and feed["items"][0]["link"] == f"/tasks/{bad}"
    assert (await user.post("/notifications/read")).status_code == 200
    assert (await user.get("/notifications")).json()["unread_count"] == 0

    muted = await user.patch(
        "/auth/me", json={"preferences": {"notifications": {"dead_letter": False}}}
    )
    assert muted.status_code == 200
    assert (await user.get("/notifications")).json()["items"] == []


async def test_health_endpoints_and_system_status(
    client: httpx.AsyncClient, user: ApiUser, worker: Worker
) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"database": "ok", "redis": "ok"}
    assert (await client.get("/api/v1/health/ready")).status_code == 200
    status = (await user.get("/system/status")).json()
    assert status["status"] == "operational" and status["workers"]["ACTIVE"] >= 1
    metrics = (await client.get("/metrics")).text
    assert "flowforge_http_request_duration_seconds_bucket" in metrics
    assert (
        'route="/api/v1/system/status"' in metrics
    )  # labelled by route template, never by raw path


async def test_openapi_documents_versioned_api(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    paths = spec["paths"]
    for path in (
        "/api/v1/tasks",
        "/api/v1/tasks/{task_id}/retry",
        "/api/v1/workflows/{workflow_id}/execute",
        "/api/v1/workers/{worker_id}/drain",
        "/api/v1/schedules/{schedule_id}",
        "/api/v1/analytics/latency",
        "/api/v1/auth/refresh",
        "/api/v1/workflow-runs/{run_id}",
    ):
        assert path in paths, path
    assert spec["info"]["title"] == "FlowForge API"
