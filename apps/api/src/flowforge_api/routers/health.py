from __future__ import annotations

import asyncio
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from flowforge_api.deps import PrincipalDep, StateDep
from flowforge_core.errors import AuthenticationFailed
from flowforge_core.services import outbox
from flowforge_core.services import workers as worker_service

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Liveness probe (process is up)")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db(state: StateDep) -> str | None:
    try:
        async with state.db.session() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2)
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"; reported in the response
        return f"{type(exc).__name__}: {str(exc)[:200]}"


async def _check_redis(state: StateDep) -> str | None:
    try:
        await asyncio.wait_for(state.redis.ping(), timeout=2)
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"; reported in the response
        return f"{type(exc).__name__}: {str(exc)[:200]}"


@router.get("/health/ready", summary="Readiness probe (PostgreSQL and Redis reachable)")
async def ready(state: StateDep) -> JSONResponse:
    db_error, redis_error = await asyncio.gather(_check_db(state), _check_redis(state))
    checks: dict[str, Any] = {
        "database": "ok" if db_error is None else "error",
        "redis": "ok" if redis_error is None else "error",
    }
    healthy = db_error is None and redis_error is None
    if not healthy and not state.settings.is_production:
        checks["errors"] = {"database": db_error, "redis": redis_error}
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    state: StateDep, authorization: Annotated[str | None, Header()] = None
) -> Response:
    token = state.settings.metrics_token
    if token is not None and token.get_secret_value():
        expected = f"Bearer {token.get_secret_value()}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise AuthenticationFailed("metrics token required")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


system_router = APIRouter(prefix="/system", tags=["system"])


@system_router.get("/status", summary="Platform status summary for the dashboard top bar")
async def system_status(state: StateDep, _: PrincipalDep) -> dict[str, Any]:
    db_error, redis_error = await asyncio.gather(_check_db(state), _check_redis(state))
    workers: dict[str, int] = {}
    backlog = None
    if db_error is None:
        async with state.db.session() as session:
            workers = await worker_service.counts_by_status(session)
            backlog = await outbox.backlog(session)
    degraded = db_error is not None or redis_error is not None or workers.get("ACTIVE", 0) == 0
    return {
        "status": "outage" if db_error else ("degraded" if degraded else "operational"),
        "database": db_error is None,
        "redis": redis_error is None,
        "realtime": state.hub.connected,
        "workers": workers,
        "outbox_backlog": backlog,
    }
