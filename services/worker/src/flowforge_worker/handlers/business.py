"""Example "business" handlers: a synthetic report generator, a simulated email sender and an ETL pipeline for
workflows. Data is generated deterministically; no external services are contacted (clearly labelled as simulated)."""

from __future__ import annotations

import asyncio
import hashlib
import random
import statistics
import uuid
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from flowforge_worker.context import SyncTaskContext, TaskContext
from flowforge_worker.errors import NonRetryableError
from flowforge_worker.registry import TaskRegistry

registry = TaskRegistry()

REGIONS = ("north", "south", "east", "west", "central")
PRODUCTS = tuple(f"SKU-{i:03d}" for i in range(1, 41))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateReportPayload(_Strict):
    report_id: int = Field(
        ge=1, description="Seeds the synthetic data set (same id -> same report)"
    )
    rows: int = Field(default=20_000, ge=1, le=2_000_000)
    top_n: int = Field(default=5, ge=1, le=40)


@registry.task(
    "generate_report",
    payload=GenerateReportPayload,
    description="Builds a deterministic synthetic sales report and returns its summary statistics.",
    queue="reports",
    executor="thread",
    timeout=900,
    example={"report_id": 42, "rows": 20000},
)
def generate_report(ctx: SyncTaskContext, payload: GenerateReportPayload) -> dict[str, Any]:
    rng = random.Random(payload.report_id)  # nosec B311 - seeded synthetic demo data
    revenue_by_region: dict[str, float] = defaultdict(float)
    revenue_by_product: dict[str, float] = defaultdict(float)
    order_values: list[float] = []
    chunk = max(1, payload.rows // 20)
    ctx.log.info("generating report", rows=payload.rows)
    for i in range(payload.rows):
        if i % chunk == 0:
            ctx.check_cancelled()
            ctx.progress(int(i * 100 / payload.rows), f"processed {i:,} of {payload.rows:,} rows")
        quantity = rng.randint(1, 12)
        price = round(rng.uniform(4.99, 249.99), 2)
        value = quantity * price
        revenue_by_region[rng.choice(REGIONS)] += value
        revenue_by_product[rng.choice(PRODUCTS)] += value
        order_values.append(value)
    total = sum(order_values)
    quantiles = (
        statistics.quantiles(order_values, n=100) if len(order_values) >= 2 else [total] * 99
    )
    top = sorted(revenue_by_product.items(), key=lambda kv: kv[1], reverse=True)[: payload.top_n]
    ctx.progress(100, "report ready")
    ctx.log.info("report generated", total_revenue=round(total, 2))
    return {
        "report_id": payload.report_id,
        "rows": payload.rows,
        "total_revenue": round(total, 2),
        "average_order_value": round(total / len(order_values), 2),
        "p50_order_value": round(quantiles[49], 2),
        "p95_order_value": round(quantiles[94], 2),
        "revenue_by_region": {k: round(v, 2) for k, v in sorted(revenue_by_region.items())},
        "top_products": [{"sku": sku, "revenue": round(v, 2)} for sku, v in top],
    }


class SendEmailPayload(_Strict):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    simulate_latency_ms: int = Field(default=150, ge=0, le=10_000)


@registry.task(
    "send_email",
    payload=SendEmailPayload,
    description="Simulated email delivery (no SMTP): records what would be sent. Demonstrates idempotency keys.",
    max_retries=5,
    timeout=60,
    example={"to": "user@example.com", "subject": "Your report is ready", "body": "Hello!"},
)
async def send_email(ctx: TaskContext, payload: SendEmailPayload) -> dict[str, Any]:
    # A real provider call would pass ctx.idempotency_key (or the task id) as the provider's dedupe key, so a
    # retried attempt after a crash cannot send the same email twice.
    dedupe_key = ctx.idempotency_key or str(ctx.task_id)
    await asyncio.sleep(payload.simulate_latency_ms / 1000)
    message_id = f"sim-{hashlib.sha256(dedupe_key.encode()).hexdigest()[:16]}"
    ctx.log.info("email accepted by simulated provider", to=payload.to, message_id=message_id)
    return {
        "simulated": True,
        "message_id": message_id,
        "to": payload.to,
        "subject": payload.subject,
    }


# ------------------------------------------------------------------------------------------------ ETL (workflows)
class ExtractPayload(_Strict):
    source: str = Field(default="orders", min_length=1, max_length=100)
    records: int = Field(default=1000, ge=0, le=1_000_000)
    seed: int = Field(default=7)


@registry.task(
    "etl.extract",
    payload=ExtractPayload,
    description="Workflow step: extracts a synthetic batch of records from a named source.",
    timeout=300,
    example={"source": "orders", "records": 1000},
)
async def etl_extract(ctx: TaskContext, payload: ExtractPayload) -> dict[str, Any]:
    rng = random.Random(f"{payload.source}:{payload.seed}")  # nosec B311 - seeded synthetic demo data
    values = [rng.randint(1, 1000) for _ in range(payload.records)]
    await asyncio.sleep(0.2)
    ctx.log.info("extracted", source=payload.source, records=len(values))
    return {
        "source": payload.source,
        "records": len(values),
        "sum": sum(values),
        "checksum": hashlib.sha256(str(values).encode()).hexdigest()[:16],
    }


class TransformPayload(_Strict):
    operation: Literal["normalize", "enrich", "aggregate"] = "normalize"
    multiplier: float = Field(default=1.0, ge=0, le=1000)


@registry.task(
    "etl.transform",
    payload=TransformPayload,
    description="Workflow step: transforms the outputs of its upstream steps.",
    timeout=300,
    example={"operation": "aggregate"},
)
async def etl_transform(ctx: TaskContext, payload: TransformPayload) -> dict[str, Any]:
    upstream = ctx.upstream
    records = sum(int(u.get("records", 0)) for u in upstream.values() if isinstance(u, dict))
    total = sum(float(u.get("sum", 0)) for u in upstream.values() if isinstance(u, dict))
    await asyncio.sleep(0.2)
    ctx.log.info("transformed", operation=payload.operation, inputs=sorted(upstream))
    return {
        "operation": payload.operation,
        "inputs": sorted(upstream),
        "records": records,
        "sum": round(total * payload.multiplier, 2),
    }


class ValidatePayload(_Strict):
    min_records: int = Field(default=1, ge=0)


@registry.task(
    "etl.validate",
    payload=ValidatePayload,
    description="Workflow step: fails (non-retryable) when upstream produced fewer records than required.",
    max_retries=0,
    timeout=60,
    example={"min_records": 100},
)
async def etl_validate(ctx: TaskContext, payload: ValidatePayload) -> dict[str, Any]:
    records = sum(int(u.get("records", 0)) for u in ctx.upstream.values() if isinstance(u, dict))
    if records < payload.min_records:
        raise NonRetryableError(
            f"validation failed: {records} records < required {payload.min_records}"
        )
    return {"validated_records": records}


class LoadPayload(_Strict):
    destination: str = Field(default="warehouse.sales_daily", min_length=1, max_length=200)


@registry.task(
    "etl.load",
    payload=LoadPayload,
    description="Workflow step: loads upstream results into a (simulated) destination.",
    timeout=300,
    example={"destination": "warehouse.sales_daily"},
)
async def etl_load(ctx: TaskContext, payload: LoadPayload) -> dict[str, Any]:
    records = sum(int(u.get("records", 0)) for u in ctx.upstream.values() if isinstance(u, dict))
    await asyncio.sleep(0.2)
    batch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ctx.task_id}:{payload.destination}"))
    ctx.log.info("loaded", destination=payload.destination, records=records, batch_id=batch_id)
    return {
        "simulated": True,
        "destination": payload.destination,
        "records": records,
        "batch_id": batch_id,
    }
