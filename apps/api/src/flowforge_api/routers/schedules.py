from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.schedules import (
    FireTimeOut,
    PreviewIn,
    PreviewOut,
    ScheduleIn,
    ScheduleOut,
    SchedulePatch,
)
from flowforge_core.db.models import Schedule
from flowforge_core.services import schedules as schedule_service
from flowforge_shared.cron import describe, format_offset, next_offset_change
from flowforge_shared.rbac import Permission
from flowforge_shared.states import MisfirePolicy, OverlapPolicy

router = APIRouter(prefix="/schedules", tags=["schedules"])

SchedRead = Annotated[ProjectContext, Depends(project_context(Permission.SCHEDULES_READ))]
SchedWrite = Annotated[ProjectContext, Depends(project_context(Permission.SCHEDULES_WRITE))]


def _out(s: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        name=s.name,
        description=s.description,
        cron_expression=s.cron_expression,
        cron_description=describe(s.cron_expression),
        timezone=s.timezone,
        task_type=s.task_type,
        payload=s.payload,
        queue=s.queue,
        priority=s.priority,
        max_retries=s.max_retries,
        timeout_seconds=s.timeout_seconds,
        status=s.status,
        misfire_policy=s.misfire_policy,
        overlap_policy=s.overlap_policy,
        next_run_at=s.next_run_at,
        last_run_at=s.last_run_at,
        last_task_id=s.last_task_id,
        total_runs=s.total_runs,
        skipped_runs=s.skipped_runs,
        version=s.version,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.post(
    "/preview",
    response_model=PreviewOut,
    summary="Validate a cron expression and preview fire times",
)
async def preview(body: PreviewIn, _: SchedRead) -> PreviewOut:
    fires = schedule_service.preview(
        body.cron_expression, body.timezone, datetime.now(UTC), body.count
    )
    tz = ZoneInfo(body.timezone)
    local = [f.astimezone(tz) for f in fires]
    offsets = {lt.utcoffset() for lt in local}
    semantics = (
        "Runs keep their local time, so their UTC time shifts. A run inside a skipped hour starts right after "
        "the change; one inside a repeated hour runs once."
    )
    note = None
    if len(offsets) > 1:
        note = f"These runs span a daylight-saving change in {body.timezone}. {semantics}"
    elif (change := next_offset_change(tz, datetime.now(UTC))) is not None:
        before = (change - timedelta(minutes=1)).astimezone(tz).utcoffset()
        after = change.astimezone(tz).utcoffset()
        note = (
            f"{body.timezone} changes its clocks on {change.astimezone(tz):%d %b %Y} "
            f"(UTC{format_offset(before)} to UTC{format_offset(after)}). {semantics}"
        )
    return PreviewOut(
        cron_expression=body.cron_expression,
        description=describe(body.cron_expression),
        timezone=body.timezone,
        next_runs=[
            FireTimeOut(utc=f, local=lt.strftime("%Y-%m-%d %H:%M %Z"), utc_offset=lt.strftime("%z"))
            for f, lt in zip(fires, local, strict=True)
        ],
        dst_note=note,
    )


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(ctx: SchedRead, state: StateDep) -> list[ScheduleOut]:
    async with state.db.session() as session:
        rows = await schedule_service.list_schedules(session, ctx.project_id)
    return [_out(s) for s in rows]


@router.post(
    "", status_code=201, response_model=ScheduleOut, responses=error_responses(401, 403, 409, 422)
)
async def create_schedule(body: ScheduleIn, ctx: SchedWrite, state: StateDep) -> ScheduleOut:
    data = schedule_service.ScheduleInput(
        name=body.name,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        task_type=body.task_type,
        payload=body.payload,
        description=body.description,
        queue=body.queue,
        priority=body.priority,
        max_retries=body.max_retries,
        timeout_seconds=body.timeout_seconds,
        misfire_policy=MisfirePolicy(body.misfire_policy),
        overlap_policy=OverlapPolicy(body.overlap_policy),
        paused=body.paused,
    )
    async with state.db.transaction() as uow:
        schedule = await schedule_service.create_schedule(
            uow,
            catalog=state.catalog,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            data=data,
            actor=ctx.actor,
        )
    return _out(schedule)


@router.get("/{schedule_id}", response_model=ScheduleOut, responses=error_responses(401, 403, 404))
async def get_schedule(schedule_id: uuid.UUID, ctx: SchedRead, state: StateDep) -> ScheduleOut:
    async with state.db.session() as session:
        return _out(await schedule_service.get_schedule(session, ctx.project_id, schedule_id))


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleOut,
    responses=error_responses(401, 403, 404, 409, 422),
    summary="Update, pause or resume a schedule (optimistic concurrency via expected_version)",
)
async def update_schedule(
    schedule_id: uuid.UUID, body: SchedulePatch, ctx: SchedWrite, state: StateDep
) -> ScheduleOut:
    changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    async with state.db.transaction() as uow:
        schedule = await schedule_service.update_schedule(
            uow,
            catalog=state.catalog,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            schedule_id=schedule_id,
            changes=changes,
            expected_version=body.expected_version,
            actor=ctx.actor,
        )
    return _out(schedule)


@router.delete("/{schedule_id}", status_code=204, responses=error_responses(401, 403, 404))
async def delete_schedule(schedule_id: uuid.UUID, ctx: SchedWrite, state: StateDep) -> Response:
    async with state.db.transaction() as uow:
        await schedule_service.delete_schedule(
            uow,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            schedule_id=schedule_id,
            actor=ctx.actor,
        )
    return Response(status_code=204)
