"""End-to-end task lifecycle through the real API, outbox relay, Redis Streams, worker runtime and PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from flowforge_core.db.models import OutboxEvent, TaskAttempt, TaskEvent
from flowforge_core.db.uow import Database
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.runtime import Worker
from tests.integration.conftest import EXECUTIONS, ApiUser, wait_for_task

pytestmark = pytest.mark.integration


async def test_submit_executes_and_records_full_history(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post("/tasks", json={"task_type": "test.echo", "payload": {"value": "hello"}})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "QUEUED"
    assert resp.headers["location"].endswith(body["id"])

    task = await wait_for_task(db, body["id"], {"SUCCEEDED"})
    assert task.result == {"value": "hello", "attempt": 1}
    assert task.attempt_count == 1
    assert task.lease_token is None and task.lease_owner is None
    assert task.last_worker_id == worker.id

    detail = (await user.get(f"/tasks/{body['id']}")).json()
    assert detail["state"] == "SUCCEEDED"
    assert detail["worker_name"] == worker.name
    assert detail["duration_ms"] is not None

    async with db.session() as session:
        events = (
            await session.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id)
            )
        ).all()
        attempts = (
            await session.scalars(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        ).all()
        unpublished = await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
        )
    transitions = [(e.from_state, e.to_state) for e in events if e.event_type == "STATE_CHANGED"]
    assert transitions == [("CREATED", "QUEUED"), ("QUEUED", "RUNNING"), ("RUNNING", "SUCCEEDED")]
    assert [a.status for a in attempts] == ["SUCCEEDED"]
    assert attempts[0].queue_wait_ms is not None and attempts[0].duration_ms is not None
    assert unpublished == 0
    assert EXECUTIONS[body["id"]] == 1

    logs = (await user.get(f"/tasks/{body['id']}/logs")).json()["items"]
    assert any(line["message"] == "echo" for line in logs)


async def test_unknown_task_type_and_invalid_payload_are_rejected(
    user: ApiUser, worker: Worker
) -> None:
    resp = await user.post("/tasks", json={"task_type": "does.not.exist", "payload": {}})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"

    resp = await user.post(
        "/tasks", json={"task_type": "demo.sleep", "payload": {"seconds": "soon"}}
    )
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert details[0]["loc"] == ["payload", "seconds"]

    resp = await user.post("/tasks", json={"task_type": "demo.sleep", "payload": {"unexpected": 1}})
    assert resp.status_code == 422  # handler models forbid unknown fields


async def test_idempotency_key_replays_and_conflicts(user: ApiUser, worker: Worker) -> None:
    body = {"task_type": "test.echo", "payload": {"value": 1}, "idempotency_key": "order-42"}
    first = await user.post("/tasks", json=body)
    replay = await user.post("/tasks", json=body)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]

    conflict = await user.post("/tasks", json={**body, "payload": {"value": 2}})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    header_key = await user.post(
        "/tasks",
        json={"task_type": "test.echo", "payload": {}},
        headers={"Idempotency-Key": "hdr-1"},
    )
    assert header_key.json()["idempotency_key"] == "hdr-1"


async def test_concurrent_identical_submissions_create_one_task(
    user: ApiUser, worker: Worker
) -> None:
    body = {"task_type": "test.echo", "payload": {"value": "x"}, "idempotency_key": "race-1"}
    responses = await asyncio.gather(*(user.post("/tasks", json=body) for _ in range(10)))
    assert {r.status_code for r in responses} <= {200, 201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(1 for r in responses if r.status_code == 201) == 1


async def test_batch_submission_is_atomic(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    ok = await user.post(
        "/tasks/batch",
        json={"tasks": [{"task_type": "test.echo", "payload": {"value": i}} for i in range(25)]},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["created"] == 25
    for item in ok.json()["items"]:
        await wait_for_task(db, item["id"], {"SUCCEEDED"})

    bad = await user.post(
        "/tasks/batch",
        json={
            "tasks": [
                {"task_type": "test.echo", "payload": {}},
                {"task_type": "nope", "payload": {}},
            ]
        },
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"][0]["index"] == 1
    listing = (await user.get("/tasks", params={"limit": 100, "include_total": "true"})).json()
    assert listing["total"] == 25  # nothing from the invalid batch was persisted


async def test_delayed_task_waits_then_runs(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks", json={"task_type": "test.echo", "payload": {}, "delay_seconds": 1.5}
    )
    assert resp.json()["state"] == "SCHEDULED"
    await asyncio.sleep(0.5)
    assert (await user.get(f"/tasks/{resp.json()['id']}")).json()["state"] == "SCHEDULED"
    task = await wait_for_task(db, resp.json()["id"], {"SUCCEEDED"})
    assert task.started_at is not None and task.started_at >= task.created_at


async def test_retries_with_backoff_then_succeeds(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks",
        json={
            "task_type": "test.fail_n",
            "payload": {"failures": 2},
            "max_retries": 3,
            "retry_backoff_seconds": 0.2,
            "retry_backoff_max_seconds": 1,
        },
    )
    task = await wait_for_task(db, resp.json()["id"], {"SUCCEEDED"})
    assert task.result == {"succeeded_on_attempt": 3}
    assert task.retry_count == 2
    attempts = (await user.get(f"/tasks/{task.id}/attempts")).json()
    assert [a["status"] for a in attempts] == ["FAILED", "FAILED", "SUCCEEDED"]
    assert attempts[0]["error_type"] == "RetryableError"


async def test_exhausted_retries_dead_letter_then_manual_retry(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks",
        json={
            "task_type": "demo.fail",
            "payload": {"retryable": True, "message": "upstream down"},
            "max_retries": 1,
            "retry_backoff_seconds": 0.1,
            "retry_backoff_max_seconds": 0.2,
        },
    )
    task_id = resp.json()["id"]
    task = await wait_for_task(db, task_id, {"DEAD_LETTERED"})
    assert task.attempt_count == 2 and task.error is not None
    assert task.error["type"] == "RetryableError"

    dlq = (await user.get("/dead-letter")).json()
    assert [t["id"] for t in dlq["items"]] == [task_id]

    retried = await user.post(f"/dead-letter/{task_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["state"] == "QUEUED" and retried.json()["retry_count"] == 0
    await wait_for_task(db, task_id, {"DEAD_LETTERED"})

    archived = await user.post(f"/dead-letter/{task_id}/archive")
    assert archived.json()["archived_at"] is not None
    assert (await user.get("/dead-letter")).json()["items"] == []


async def test_non_retryable_error_skips_retries(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks", json={"task_type": "demo.fail", "payload": {"retryable": False}, "max_retries": 5}
    )
    task = await wait_for_task(db, resp.json()["id"], {"DEAD_LETTERED"})
    assert task.attempt_count == 1


async def test_dead_letter_disabled_ends_failed(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks",
        json={"task_type": "demo.fail", "payload": {}, "max_retries": 0, "dead_letter": False},
    )
    await wait_for_task(db, resp.json()["id"], {"FAILED"})


async def test_cancel_queued_task_is_immediate_and_never_runs(
    user: ApiUser, catalog: None, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post("/tasks", json={"task_type": "test.echo", "payload": {}})
    task_id = resp.json()["id"]
    cancelled = await user.post(f"/tasks/{task_id}/cancel")
    assert cancelled.json()["state"] == "CANCELLED"
    # A worker started afterwards receives the stale stream entry and discards it.
    from flowforge_core.config import get_settings
    from tests.integration.conftest import make_registry

    worker = Worker(get_settings(), make_registry(), install_signal_handlers=False, concurrency=2)
    runner = asyncio.create_task(worker.run())
    await worker.wait_started()
    await asyncio.sleep(1.0)
    worker.request_stop()
    await asyncio.wait_for(runner, 20)
    assert worker.stats.duplicates >= 1
    assert task_id not in EXECUTIONS
    assert (await user.post(f"/tasks/{task_id}/cancel")).status_code == 409


async def test_cancel_running_task_is_cooperative(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post("/tasks", json={"task_type": "test.block", "payload": {"seconds": 30}})
    task_id = resp.json()["id"]
    await wait_for_task(db, task_id, {"RUNNING"})
    cancel = await user.post(f"/tasks/{task_id}/cancel")
    assert cancel.json()["state"] == "CANCEL_REQUESTED"
    task = await wait_for_task(db, task_id, {"CANCELLED"}, timeout=10)
    assert task.finished_at is not None
    attempts = (await user.get(f"/tasks/{task_id}/attempts")).json()
    assert attempts[-1]["status"] == "CANCELLED"


async def test_timeout_is_enforced_and_retried(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    resp = await user.post(
        "/tasks",
        json={
            "task_type": "test.block",
            "payload": {"seconds": 10},
            "timeout_seconds": 1,
            "max_retries": 1,
            "retry_backoff_seconds": 0.1,
            "retry_backoff_max_seconds": 0.1,
        },
    )
    task = await wait_for_task(db, resp.json()["id"], {"DEAD_LETTERED"}, timeout=15)
    attempts = (await user.get(f"/tasks/{task.id}/attempts")).json()
    assert [a["status"] for a in attempts] == ["TIMED_OUT", "TIMED_OUT"]


async def test_priority_bands_are_respected(
    user: ApiUser, catalog: None, control_plane: ControlPlane, db: Database
) -> None:
    # Queue tasks while no worker runs, then start a single-slot worker and compare start order.
    low = [
        (
            await user.post("/tasks", json={"task_type": "test.echo", "payload": {}, "priority": 1})
        ).json()["id"]
        for _ in range(5)
    ]
    high = [
        (
            await user.post("/tasks", json={"task_type": "test.echo", "payload": {}, "priority": 9})
        ).json()["id"]
        for _ in range(5)
    ]
    await asyncio.sleep(1.0)  # let the relay publish everything
    from flowforge_core.config import get_settings
    from tests.integration.conftest import make_registry

    worker = Worker(get_settings(), make_registry(), install_signal_handlers=False, concurrency=1)
    runner = asyncio.create_task(worker.run())
    for task_id in [*low, *high]:
        await wait_for_task(db, task_id, {"SUCCEEDED"})
    worker.request_stop()
    await asyncio.wait_for(runner, 20)
    async with db.session() as session:
        rows = (await session.execute(select(TaskAttempt.task_id, TaskAttempt.started_at))).all()
    order = [str(r.task_id) for r in sorted(rows, key=lambda r: r.started_at)]
    first_five = set(order[:5])
    # Smooth weighted round-robin gives the high band ~60% of first looks, so most of the first starts are high.
    assert len(first_five & set(high)) >= 4


async def test_list_filters_search_and_pagination(
    user: ApiUser, catalog: None, db: Database
) -> None:
    ids = []
    for i in range(7):
        r = await user.post(
            "/tasks",
            json={"task_type": "test.echo", "payload": {"value": i}, "idempotency_key": f"k-{i}"},
        )
        ids.append(r.json()["id"])
    await user.post("/tasks", json={"task_type": "demo.noop", "payload": {}})

    page1 = (
        await user.get(
            "/tasks", params={"limit": 3, "task_type": "test.echo", "include_total": "true"}
        )
    ).json()
    assert page1["total"] == 7 and len(page1["items"]) == 3 and page1["has_more"]
    page2 = (
        await user.get(
            "/tasks", params={"limit": 3, "task_type": "test.echo", "cursor": page1["next_cursor"]}
        )
    ).json()
    page3 = (
        await user.get(
            "/tasks", params={"limit": 3, "task_type": "test.echo", "cursor": page2["next_cursor"]}
        )
    ).json()
    seen = [t["id"] for t in page1["items"] + page2["items"] + page3["items"]]
    assert seen == list(reversed(ids)) and not page3["has_more"]

    by_key = (await user.get("/tasks", params={"q": "k-3"})).json()["items"]
    assert [t["id"] for t in by_key] == [ids[3]]
    by_prefix = (await user.get("/tasks", params={"q": ids[5][:13]})).json()["items"]
    assert ids[5] in [t["id"] for t in by_prefix]
    by_state = (
        await user.get("/tasks", params=[("state", "QUEUED"), ("state", "RUNNING")])
    ).json()["items"]
    assert len(by_state) == 8

    bad_cursor = await user.get("/tasks", params={"cursor": "not-a-cursor"})
    assert bad_cursor.status_code == 422


async def test_task_types_catalog_lists_registered_handlers(user: ApiUser, worker: Worker) -> None:
    catalog = {t["name"]: t for t in (await user.get("/task-types")).json()}
    assert {"demo.noop", "generate_report", "send_email", "etl.extract"} <= set(catalog)
    report = catalog["generate_report"]
    assert report["queue"] == "reports" and report["executor"] == "thread"
    assert "report_id" in report["payload_schema"]["properties"]


async def test_events_and_unknown_ids_are_tenant_scoped(user: ApiUser) -> None:
    missing = await user.get(f"/tasks/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["request_id"]
