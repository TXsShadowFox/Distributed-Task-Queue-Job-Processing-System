from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flowforge_api import __version__
from flowforge_api.errors import install_error_handlers
from flowforge_api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from flowforge_api.ratelimit import RateLimiter
from flowforge_api.realtime import EventHub
from flowforge_api.routers import (
    analytics,
    api_keys,
    auth,
    events,
    health,
    notifications,
    organizations,
    schedules,
    tasks,
    workers,
    workflows,
)
from flowforge_api.state import AppState
from flowforge_core.broker import StreamBroker
from flowforge_core.config import Settings, get_settings
from flowforge_core.db.session import create_engine, create_sessionmaker
from flowforge_core.db.uow import Database
from flowforge_core.events import EventPublisher
from flowforge_core.logs import get_logger
from flowforge_core.observability import metrics
from flowforge_core.observability.tracing import instrument_engine, setup_tracing, tracing_enabled
from flowforge_core.redis_client import create_redis
from flowforge_core.services.task_types import TaskTypeCatalog

log = get_logger(__name__)

DESCRIPTION = """
FlowForge — **Orchestrate. Execute. Observe.**

Distributed task execution with at-least-once delivery, retries with exponential backoff, a dead-letter queue,
delayed and cron-scheduled jobs and DAG workflows.

**Authentication**: `Authorization: Bearer <access token>` plus `X-Project-ID: <project uuid>` for users, or a
project-scoped API key (`Authorization: Bearer ffk_…` / `X-API-Key: ffk_…`).

**Errors** always use the envelope `{"error": {"code", "message", "details", "request_id"}}`.
"""

TAGS = [
    {"name": "auth", "description": "Sign-up, sign-in and session rotation"},
    {"name": "tasks", "description": "Submit, inspect, cancel and retry tasks"},
    {
        "name": "dead-letter queue",
        "description": "Inspect, retry and archive permanently failed tasks",
    },
    {"name": "workflows", "description": "DAG workflow definitions and runs"},
    {"name": "schedules", "description": "Cron schedules with time zones"},
    {"name": "workers", "description": "Worker fleet status and draining"},
    {
        "name": "analytics",
        "description": "Throughput, failures, latency percentiles and utilization",
    },
    {"name": "api keys", "description": "Project-scoped API keys"},
    {"name": "organizations", "description": "Organizations, members, roles and projects"},
    {"name": "realtime", "description": "Server-Sent Events streams"},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_tracing("flowforge-api", settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings, application_name="flowforge-api")
        instrument_engine(engine)
        metrics.DB_POOLS.register("api", engine.pool)
        sessionmaker = create_sessionmaker(engine)
        redis = create_redis(settings, client_name="flowforge-api")
        hub = EventHub(redis, settings.redis_prefix)
        await hub.start()
        app.state.ff = AppState(
            settings=settings,
            db=Database(engine, sessionmaker, EventPublisher(redis, settings.redis_prefix)),
            redis=redis,
            broker=StreamBroker(redis, settings.redis_prefix),
            catalog=TaskTypeCatalog(sessionmaker),
            hub=hub,
            limiter=RateLimiter(
                redis, prefix=settings.redis_prefix, enabled=settings.rate_limit_enabled
            ),
        )
        log.info("api_started", environment=settings.environment, version=__version__)
        try:
            yield
        finally:
            await hub.stop()
            await redis.aclose()
            await engine.dispose()
            log.info("api_stopped")

    app = FastAPI(
        title="FlowForge API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    install_error_handlers(app)

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health.router)
    v1.include_router(health.system_router)
    v1.include_router(auth.router)
    v1.include_router(organizations.router)
    v1.include_router(organizations.projects_router)
    v1.include_router(tasks.router)
    v1.include_router(tasks.types_router)
    v1.include_router(tasks.dlq_router)
    v1.include_router(workflows.router)
    v1.include_router(workflows.runs_router)
    v1.include_router(workers.router)
    v1.include_router(schedules.router)
    v1.include_router(analytics.router)
    v1.include_router(api_keys.router)
    v1.include_router(notifications.router)
    v1.include_router(events.router)
    app.include_router(v1)
    # Unversioned probes for orchestrators and Prometheus.
    app.include_router(health.router)

    # Added innermost-first: the last middleware added is the outermost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Project-ID",
            "X-Requested-With",
            "Idempotency-Key",
            "X-Request-ID",
            "X-API-Key",
            "Last-Event-ID",
        ],
        expose_headers=["X-Request-ID", "Idempotent-Replayed", "Retry-After", "Location"],
        max_age=600,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(RequestContextMiddleware, trusted_proxy_count=settings.trusted_proxy_count)

    if tracing_enabled():
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
    return app
