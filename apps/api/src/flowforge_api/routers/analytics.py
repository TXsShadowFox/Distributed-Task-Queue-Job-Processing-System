from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_core.services import analytics
from flowforge_shared.rbac import Permission

router = APIRouter(prefix="/analytics", tags=["analytics"])

AnalyticsRead = Annotated[ProjectContext, Depends(project_context(Permission.ANALYTICS_READ))]
WindowParam = Literal["1h", "6h", "24h", "7d", "30d"]


def _stamp(data: dict[str, Any]) -> dict[str, Any]:
    data["generated_at"] = datetime.now(UTC)
    return data


@router.get(
    "/overview", summary="Headline numbers for the overview page (all computed from live data)"
)
async def overview(ctx: AnalyticsRead, state: StateDep) -> dict[str, Any]:
    async with state.db.session() as session:
        return _stamp(await analytics.overview(session, ctx.project_id))


@router.get("/throughput", summary="Submitted / succeeded / failed / retried per time bucket")
async def throughput(
    ctx: AnalyticsRead, state: StateDep, window: WindowParam = "24h"
) -> dict[str, Any]:
    async with state.db.session() as session:
        return _stamp(await analytics.throughput(session, ctx.project_id, window))


@router.get("/failures", summary="Failure rates by task type and error type, plus recent failures")
async def failures(
    ctx: AnalyticsRead, state: StateDep, window: WindowParam = "24h"
) -> dict[str, Any]:
    async with state.db.session() as session:
        return _stamp(await analytics.failures(session, ctx.project_id, window))


@router.get("/latency", summary="p50/p95/p99 execution time, queue wait and end-to-end latency")
async def latency(
    ctx: AnalyticsRead, state: StateDep, window: WindowParam = "24h"
) -> dict[str, Any]:
    async with state.db.session() as session:
        return _stamp(await analytics.latency(session, ctx.project_id, window))


@router.get("/workers", summary="Per-worker slot utilization over the window")
async def worker_utilization(
    ctx: AnalyticsRead, state: StateDep, window: WindowParam = "1h"
) -> dict[str, Any]:
    async with state.db.session() as session:
        return _stamp(await analytics.worker_utilization(session, ctx.project_id, window))
