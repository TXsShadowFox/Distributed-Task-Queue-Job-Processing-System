"""OpenTelemetry tracing. Enabled only when ``FLOWFORGE_OTEL_EXPORTER_OTLP_ENDPOINT`` is set; otherwise the global
no-op tracer is used and spans cost nothing.

Trace context crosses the queue: the API stores the W3C ``traceparent`` on the task and in the stream entry, and the
worker starts its ``task.execute`` span as a child of the submission span.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from flowforge_core.config import Settings
from flowforge_core.logs import get_logger

log = get_logger(__name__)
_propagator = TraceContextTextMapPropagator()
_configured = False


def tracing_enabled() -> bool:
    return _configured


def setup_tracing(service_name: str, settings: Settings) -> bool:
    """Install the tracer provider and Redis instrumentation (call once, before serving)."""
    global _configured
    if _configured or not settings.otel_exporter_otlp_endpoint:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "deployment.environment": settings.environment}
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    RedisInstrumentor().instrument()
    _configured = True
    log.info("tracing_enabled", endpoint=endpoint)
    return True


def instrument_engine(engine: Any) -> None:
    """Trace SQL statements of an async engine (no-op unless tracing is enabled)."""
    if not _configured:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def get_tracer(name: str = "flowforge") -> trace.Tracer:
    return trace.get_tracer(name)


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def context_from_traceparent(traceparent: str | None) -> otel_context.Context | None:
    if not traceparent:
        return None
    return _propagator.extract({"traceparent": traceparent})


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
