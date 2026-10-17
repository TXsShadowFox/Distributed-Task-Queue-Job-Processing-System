"""Cron schedules: CRUD, preview with DST, exactly-once firing under concurrency, misfire and overlap policies."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text

from flowforge_core.db.models import Schedule, Task
from flowforge_core.db.uow import Database
from flowforge_core.services import schedules as schedule_service
from flowforge_scheduler.service import ControlPlane
from flowforge_worker.runtime import Worker
from tests.integration.conftest import ApiUser, wait_until

pytestmark = pytest.mark.integration


async def create(user: ApiUser, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": f"sched-{uuid.uuid4().hex[:6]}",
        "cron_expression": "*/5 * * * *",
        "timezone": "Asia/Kolkata",
        "task_type": "test.echo",
        "payload": {"value": "tick"},
        **overrides,
    }
    resp = await user.post("/schedules", json=body)
    assert resp.status_code == 201, resp.text
    schedule: dict[str, Any] = resp.json()
    return schedule


async def make_due(db: Database, schedule_id: str, minutes_ago: float) -> None:
    async with db.session() as session:
        await session.execute(
            text(
                "UPDATE schedules SET next_run_at = now() - make_interval(secs => :s) WHERE id = :id"
            ),
            {"s": minutes_ago * 60, "id": uuid.UUID(schedule_id)},
        )
        await session.commit()


async def fire(db: Database) -> int:
    async with db.transaction() as uow:
        return await schedule_service.fire_due_schedules(uow, limit=100)


async def schedule_tasks(db: Database, schedule_id: str) -> list[Task]:
    async with db.session() as session:
        return list(
            (
                await session.scalars(
                    select(Task).where(Task.schedule_id == uuid.UUID(schedule_id))
                )
            ).all()
        )


async def test_create_computes_next_run_in_timezone(user: ApiUser, catalog: None) -> None:
    sched = await create(user, cron_expression="30 9 * * *", timezone="Asia/Kolkata")
    next_run = datetime.fromisoformat(sched["next_run_at"])
    assert (
        next_run.astimezone(UTC).hour == 4 and next_run.astimezone(UTC).minute == 0
    )  # 09:30 IST == 04:00 UTC
    assert sched["cron_description"] == "At 09:30 every day"
    assert sched["queue"] == "default" and sched["status"] == "ACTIVE"


async def test_invalid_cron_timezone_and_payload_are_rejected(user: ApiUser, catalog: None) -> None:
    for bad in (
        {"cron_expression": "61 * * * *"},
        {"timezone": "Mars/Olympus"},
        {"payload": {"nope": 1}},
    ):
        resp = await user.post(
            "/schedules",
            json={
                "name": "x",
                "cron_expression": "* * * * *",
                "timezone": "UTC",
                "task_type": "test.echo",
                **bad,
            },
        )
        assert resp.status_code == 422, (bad, resp.text)


async def test_preview_reports_dst_shift(user: ApiUser, catalog: None) -> None:
    resp = await user.post(
        "/schedules/preview",
        json={"cron_expression": "0 12 * * *", "timezone": "America/New_York", "count": 50},
    )
    body = resp.json()
    assert resp.status_code == 200 and len(body["next_runs"]) == 50
    # New York changes its clocks twice a year, so a note is always present (wording depends on today's date).
    assert body["dst_note"] and "New_York" in body["dst_note"]
    assert body["description"] == "At 12:00 every day"


async def test_preview_has_no_dst_note_for_zones_without_dst(user: ApiUser, catalog: None) -> None:
    resp = await user.post(
        "/schedules/preview",
        json={"cron_expression": "0 12 * * *", "timezone": "Asia/Kolkata", "count": 5},
    )
    assert resp.status_code == 200 and resp.json()["dst_note"] is None


async def test_concurrent_fires_create_exactly_one_task_per_fire_time(
    user: ApiUser, catalog: None, db: Database
) -> None:
    sched = await create(user, cron_expression="* * * * *")
    await make_due(db, sched["id"], minutes_ago=0.1)
    results = await asyncio.gather(*(fire(db) for _ in range(8)))
    assert sum(results) == 1
    tasks = await schedule_tasks(db, sched["id"])
    assert len(tasks) == 1
    assert tasks[0].idempotency_key and tasks[0].idempotency_key.startswith(
        f"schedule:{sched['id']}:"
    )
    async with db.session() as session:
        row = await session.get(Schedule, uuid.UUID(sched["id"]))
    assert row is not None and row.total_runs == 1 and row.next_run_at is not None
    assert row.next_run_at > datetime.now(UTC)


async def test_refiring_the_same_fire_time_is_idempotent(
    user: ApiUser, catalog: None, db: Database
) -> None:
    sched = await create(user, cron_expression="* * * * *")
    await make_due(db, sched["id"], minutes_ago=0.1)
    async with db.session() as session:
        fire_time = await session.scalar(
            select(Schedule.next_run_at).where(Schedule.id == uuid.UUID(sched["id"]))
        )
    await fire(db)
    # Simulate a scheduler that crashed after committing and re-reads the old next_run_at.
    async with db.session() as session:
        await session.execute(
            text("UPDATE schedules SET next_run_at = :t WHERE id = :id"),
            {"t": fire_time, "id": uuid.UUID(sched["id"])},
        )
        await session.commit()
    await fire(db)
    assert len(await schedule_tasks(db, sched["id"])) == 1


async def test_misfire_run_once_fires_a_single_catch_up(
    user: ApiUser, catalog: None, db: Database
) -> None:
    sched = await create(user, cron_expression="* * * * *", misfire_policy="RUN_ONCE")
    await make_due(db, sched["id"], minutes_ago=10)
    assert await fire(db) == 1
    async with db.session() as session:
        row = await session.get(Schedule, uuid.UUID(sched["id"]))
    assert row is not None and row.skipped_runs >= 9


async def test_misfire_skip_drops_stale_fire_times(
    user: ApiUser, catalog: None, db: Database
) -> None:
    # Yearly cron: the missed fire time is the only one in the window and is 10 minutes late (> 60 s grace).
    sched = await create(user, cron_expression="0 0 1 1 *", misfire_policy="SKIP")
    await make_due(db, sched["id"], minutes_ago=10)
    assert await fire(db) == 0
    assert await schedule_tasks(db, sched["id"]) == []
    async with db.session() as session:
        row = await session.get(Schedule, uuid.UUID(sched["id"]))
    assert row is not None and row.skipped_runs == 1
    assert row.next_run_at is not None and row.next_run_at > datetime.now(UTC) + timedelta(days=1)


async def test_overlap_skip_waits_for_previous_run(
    user: ApiUser, catalog: None, db: Database
) -> None:
    sched = await create(user, cron_expression="* * * * *", overlap_policy="SKIP")
    await make_due(db, sched["id"], minutes_ago=0.1)
    assert await fire(db) == 1  # first run is QUEUED and stays active (no worker)
    await make_due(db, sched["id"], minutes_ago=0.05)
    assert await fire(db) == 0
    async with db.session() as session:
        row = await session.get(Schedule, uuid.UUID(sched["id"]))
    assert row is not None and row.skipped_runs == 1


async def test_pause_resume_update_and_delete(user: ApiUser, catalog: None, db: Database) -> None:
    sched = await create(user)
    paused = await user.patch(
        f"/schedules/{sched['id']}", json={"paused": True, "expected_version": sched["version"]}
    )
    assert paused.json()["status"] == "PAUSED"
    await make_due(db, sched["id"], minutes_ago=1)
    assert await fire(db) == 0  # paused schedules never fire

    stale = await user.patch(
        f"/schedules/{sched['id']}", json={"paused": False, "expected_version": sched["version"]}
    )
    assert stale.status_code == 409  # optimistic concurrency

    resumed = await user.patch(
        f"/schedules/{sched['id']}", json={"paused": False, "cron_expression": "0 * * * *"}
    )
    body = resumed.json()
    assert body["status"] == "ACTIVE" and body["cron_expression"] == "0 * * * *"
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(
        UTC
    )  # missed fires while paused are not replayed

    assert (await user.delete(f"/schedules/{sched['id']}")).status_code == 204
    assert (await user.get(f"/schedules/{sched['id']}")).status_code == 404


async def test_control_plane_fires_and_worker_executes(
    user: ApiUser, worker: Worker, control_plane: ControlPlane, db: Database
) -> None:
    sched = await create(user, cron_expression="* * * * *")
    await make_due(db, sched["id"], minutes_ago=0.1)

    async def executed() -> bool:
        tasks = await schedule_tasks(db, sched["id"])
        return bool(tasks) and tasks[0].state == "SUCCEEDED"

    await wait_until(executed, timeout=15, message="scheduled task did not run")
    listed = (await user.get("/tasks", params={"schedule_id": sched["id"]})).json()["items"]
    assert len(listed) == 1 and listed[0]["schedule_id"] == sched["id"]
    async with db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(Task).where(Task.schedule_id == uuid.UUID(sched["id"]))
        )
    assert count == 1
    assert (datetime.now(UTC) - timedelta(minutes=2)) < datetime.fromisoformat(
        (await user.get(f"/schedules/{sched['id']}")).json()["last_run_at"]
    )
