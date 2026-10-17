"""DAG workflows: validation, versioning, execution order, data passing, failure propagation, resume, cancellation and
fan-in under concurrency."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from flowforge_core.db.models import Task
from flowforge_core.db.uow import Database
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.runtime import Worker
from tests.integration.conftest import ApiUser, wait_until

pytestmark = pytest.mark.integration


def node(
    key: str, task_type: str, payload: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "name": key.replace("_", " ").title(),
        "task_type": task_type,
        "payload": payload or {},
        **extra,
    }


def edge(source: str, target: str) -> dict[str, str]:
    return {"source": source, "target": target}


async def create_workflow(
    user: ApiUser, name: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]]
) -> dict[str, Any]:
    resp = await user.post(
        "/workflows", json={"name": name, "graph": {"nodes": nodes, "edges": edges}}
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def wait_for_run(
    user: ApiUser, run_id: str, states: set[str], timeout: float = 30
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    async def done() -> bool:
        nonlocal result
        result = (await user.get(f"/workflow-runs/{run_id}")).json()
        return result["state"] in states

    await wait_until(done, timeout=timeout, message=f"run {run_id} did not reach {states}")
    return result


DIAMOND_NODES = [
    node("extract_orders", "etl.extract", {"source": "orders", "records": 100}),
    node("extract_users", "etl.extract", {"source": "users", "records": 40}),
    node("transform", "etl.transform", {"operation": "aggregate"}),
    node("load", "etl.load", {"destination": "warehouse.daily"}),
]
DIAMOND_EDGES = [
    edge("extract_orders", "transform"),
    edge("extract_users", "transform"),
    edge("transform", "load"),
]


async def test_diamond_workflow_runs_in_dependency_order_and_passes_data(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    wf = await create_workflow(user, "daily-etl", DIAMOND_NODES, DIAMOND_EDGES)
    assert wf["latest_version"] == 1 and len(wf["graph"]["nodes"]) == 4
    run = (
        await user.post(f"/workflows/{wf['id']}/execute", json={"input": {"date": "2026-10-11"}})
    ).json()
    assert run["state"] == "RUNNING"
    started = {n["key"]: n["state"] for n in run["nodes"]}
    assert started["extract_orders"] == "RUNNING" and started["transform"] == "PENDING"

    final = await wait_for_run(user, run["id"], {"SUCCEEDED", "FAILED"})
    assert final["state"] == "SUCCEEDED", final
    assert {n["state"] for n in final["nodes"]} == {"SUCCEEDED"}
    by_key = {n["key"]: n for n in final["nodes"]}
    transform = (await user.get(f"/tasks/{by_key['transform']['task_id']}")).json()
    assert transform["result"]["records"] == 140  # aggregated from both upstream extracts
    assert sorted(transform["result"]["inputs"]) == ["extract_orders", "extract_users"]
    load = (await user.get(f"/tasks/{by_key['load']['task_id']}")).json()
    assert load["result"]["records"] == 140
    # The inputs above prove the dependency order: transform was created with both extracts' results. Node timestamps
    # cannot prove it. They are transaction start times (now()), so when the two extracts finish together, the
    # transaction that waited for the run lock records an earlier time than the one it waited for.


async def test_failure_skips_descendants_but_independent_branches_finish(
    user: ApiUser, worker: Worker, control_plane: ControlPlane
) -> None:
    nodes = [
        node("extract", "etl.extract", {"records": 5}),
        node("validate", "etl.validate", {"min_records": 1000}),
        node("load", "etl.load"),
        node("side", "test.echo", {"value": "independent"}),
    ]
    wf = await create_workflow(
        user, "validated", nodes, [edge("extract", "validate"), edge("validate", "load")]
    )
    run = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    final = await wait_for_run(user, run["id"], {"SUCCEEDED", "FAILED"})
    states = {n["key"]: n["state"] for n in final["nodes"]}
    assert final["state"] == "FAILED"
    assert states == {
        "extract": "SUCCEEDED",
        "validate": "FAILED",
        "load": "SKIPPED",
        "side": "SUCCEEDED",
    }
    assert "validate" in final["error"]


async def test_manual_retry_of_failed_node_resumes_the_run(
    user: ApiUser, worker: Worker, control_plane: ControlPlane
) -> None:
    nodes = [
        node("flaky", "test.fail_n", {"failures": 1}, max_retries=0),
        node("after", "test.echo", {"value": 1}),
    ]
    wf = await create_workflow(user, "resumable", nodes, [edge("flaky", "after")])
    run = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    failed = await wait_for_run(user, run["id"], {"FAILED"})
    flaky_task = next(n["task_id"] for n in failed["nodes"] if n["key"] == "flaky")
    assert {n["key"]: n["state"] for n in failed["nodes"]} == {
        "flaky": "FAILED",
        "after": "SKIPPED",
    }

    retry = await user.post(f"/tasks/{flaky_task}/retry")
    assert retry.status_code == 200, retry.text
    resumed = (await user.get(f"/workflow-runs/{run['id']}")).json()
    assert resumed["state"] == "RUNNING"
    final = await wait_for_run(user, run["id"], {"SUCCEEDED", "FAILED"})
    assert final["state"] == "SUCCEEDED"
    assert {n["state"] for n in final["nodes"]} == {"SUCCEEDED"}


async def test_cancel_run_cancels_pending_and_running_nodes(
    user: ApiUser, worker: Worker, control_plane: ControlPlane
) -> None:
    nodes = [node("slow", "test.block", {"seconds": 30}), node("next", "test.echo")]
    wf = await create_workflow(user, "cancellable", nodes, [edge("slow", "next")])
    run = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    slow_task = next(n["task_id"] for n in run["nodes"] if n["key"] == "slow")

    async def task_state() -> str:
        state: str = (await user.get(f"/tasks/{slow_task}")).json()["state"]
        return state

    async def running() -> bool:
        return await task_state() == "RUNNING"

    await wait_until(running, timeout=10, message="slow node never started")
    cancelled = (await user.post(f"/workflow-runs/{run['id']}/cancel")).json()
    assert cancelled["state"] == "CANCELLED"
    assert {n["key"]: n["state"] for n in cancelled["nodes"]}["next"] == "CANCELLED"

    async def task_cancelled() -> bool:
        return await task_state() == "CANCELLED"

    await wait_until(task_cancelled, timeout=10, message="running node task not cancelled")
    final = (await user.get(f"/workflow-runs/{run['id']}")).json()
    assert {n["key"]: n["state"] for n in final["nodes"]} == {
        "slow": "CANCELLED",
        "next": "CANCELLED",
    }
    again = await user.post(f"/workflow-runs/{run['id']}/cancel")
    assert again.status_code == 200 and again.json()["state"] == "CANCELLED"  # idempotent


async def test_fan_in_starts_sink_exactly_once_under_concurrency(
    user: ApiUser, worker_factory: object, control_plane: ControlPlane, db: Database
) -> None:
    for i in range(2):
        await worker_factory(concurrency=10, name=f"fan-{i}")  # type: ignore[operator]
    roots = [node(f"r{i}", "test.echo", {"value": i}) for i in range(12)]
    wf = await create_workflow(
        user,
        "fan-in",
        [*roots, node("sink", "test.echo")],
        [edge(f"r{i}", "sink") for i in range(12)],
    )
    run = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    final = await wait_for_run(user, run["id"], {"SUCCEEDED", "FAILED"})
    assert final["state"] == "SUCCEEDED"
    async with db.session() as session:
        sink_tasks = await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.workflow_run_id == final["id"], Task.meta["node_key"].astext == "sink")
        )
    assert sink_tasks == 1


async def test_versions_are_immutable_and_runs_pin_their_version(
    user: ApiUser, worker: Worker, control_plane: ControlPlane
) -> None:
    wf = await create_workflow(user, "versioned", [node("a", "test.block", {"seconds": 1.5})], [])
    run_v1 = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    updated = await user.put(
        f"/workflows/{wf['id']}",
        json={
            "graph": {
                "nodes": [node("a", "test.echo"), node("b", "test.echo")],
                "edges": [edge("a", "b")],
            },
            "expected_version": 1,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["latest_version"] == 2
    stale = await user.put(
        f"/workflows/{wf['id']}",
        json={"graph": {"nodes": [node("x", "test.echo")], "edges": []}, "expected_version": 1},
    )
    assert stale.status_code == 409

    v1 = await wait_for_run(user, run_v1["id"], {"SUCCEEDED"})
    assert [n["key"] for n in v1["nodes"]] == ["a"] and v1["version"] == 1
    run_v2 = (await user.post(f"/workflows/{wf['id']}/execute", json={})).json()
    v2 = await wait_for_run(user, run_v2["id"], {"SUCCEEDED"})
    assert [n["key"] for n in v2["nodes"]] == ["a", "b"] and v2["version"] == 2
    old_graph = (await user.get(f"/workflows/{wf['id']}", params={"version": 1})).json()["graph"]
    assert [n["key"] for n in old_graph["nodes"]] == ["a"]


async def test_invalid_graphs_are_rejected_with_all_problems(user: ApiUser, catalog: None) -> None:
    cyclic = {
        "nodes": [node("a", "test.echo"), node("b", "test.echo"), node("c", "nope.unknown")],
        "edges": [edge("a", "b"), edge("b", "a")],
    }
    validation = (await user.post("/workflows/validate", json=cyclic)).json()
    assert validation["valid"] is False
    codes = {e["code"] for e in validation["errors"]}
    assert codes == {"CYCLE", "UNKNOWN_TASK_TYPE"}

    resp = await user.post("/workflows", json={"name": "bad", "graph": cyclic})
    assert resp.status_code == 422
    ok = (
        await user.post(
            "/workflows/validate", json={"nodes": DIAMOND_NODES, "edges": DIAMOND_EDGES}
        )
    ).json()
    assert ok["valid"] and ok["order"][-1] == "load"

    bad_payload = {"nodes": [node("a", "demo.sleep", {"seconds": -1})], "edges": []}
    errors = (await user.post("/workflows/validate", json=bad_payload)).json()["errors"]
    assert errors[0]["code"] == "INVALID_PAYLOAD"


async def test_archived_workflow_cannot_run_and_names_are_unique(
    user: ApiUser, catalog: None
) -> None:
    wf = await create_workflow(user, "once", [node("a", "test.echo")], [])
    dup = await user.post(
        "/workflows",
        json={"name": "once", "graph": {"nodes": [node("a", "test.echo")], "edges": []}},
    )
    assert dup.status_code == 409
    archived = await user.delete(f"/workflows/{wf['id']}")
    assert archived.json()["archived_at"] is not None
    assert (await user.post(f"/workflows/{wf['id']}/execute", json={})).status_code == 409
    listed = (await user.get("/workflows")).json()
    assert listed == []
    assert len((await user.get("/workflows", params={"include_archived": "true"})).json()) == 1


async def test_workflow_runs_listing(
    user: ApiUser, worker: Worker, control_plane: ControlPlane
) -> None:
    wf = await create_workflow(user, "listed", [node("a", "test.echo")], [])
    for _ in range(3):
        await user.post(f"/workflows/{wf['id']}/execute", json={})
    runs = (await user.get(f"/workflows/{wf['id']}/runs")).json()["items"]
    assert len(runs) == 3
    all_runs = (await user.get("/workflow-runs")).json()["items"]
    assert {r["workflow_name"] for r in all_runs} == {"listed"}
    await asyncio.gather(*(wait_for_run(user, r["id"], {"SUCCEEDED"}) for r in runs))
    summary = (await user.get("/workflows")).json()[0]
    assert summary["last_run"]["state"] == "SUCCEEDED" and summary["node_count"] == 1
    assert summary["last_run"]["version"] == 1
