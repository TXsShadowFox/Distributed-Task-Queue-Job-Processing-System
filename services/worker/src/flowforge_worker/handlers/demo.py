"""Demo and benchmark handlers. They simulate work; none touches external systems."""

from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flowforge_worker.context import TaskContext
from flowforge_worker.errors import NonRetryableError, RetryableError
from flowforge_worker.registry import TaskRegistry

registry = TaskRegistry()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoopPayload(_Strict):
    tag: str | None = Field(
        default=None, max_length=100, description="Free-form label (benchmarks)"
    )


@registry.task(
    "demo.noop",
    payload=NoopPayload,
    description="Does nothing. Measures pure platform overhead (dispatch, claim, completion).",
    max_retries=0,
    timeout=30,
    example={"tag": "bench"},
)
async def noop(ctx: TaskContext, payload: NoopPayload) -> dict[str, Any]:
    return {"ok": True, "tag": payload.tag}


class SleepPayload(_Strict):
    seconds: float = Field(
        default=1.0, ge=0, le=3600, description="How long to wait (non-blocking)"
    )
    report_progress: bool = Field(default=True)


@registry.task(
    "demo.sleep",
    payload=SleepPayload,
    description="I/O-bound simulation: waits without blocking the event loop, reporting progress.",
    timeout=3700,
    example={"seconds": 3},
)
async def sleep(ctx: TaskContext, payload: SleepPayload) -> dict[str, Any]:
    steps = 10 if payload.report_progress and payload.seconds >= 1 else 1
    for step in range(steps):
        await asyncio.sleep(payload.seconds / steps)
        if steps > 1:
            await ctx.progress(
                int((step + 1) * 100 / steps), f"waited {payload.seconds * (step + 1) / steps:.1f}s"
            )
    return {"slept_seconds": payload.seconds}


class CpuHashPayload(_Strict):
    iterations: int = Field(default=200_000, ge=1, le=50_000_000, description="SHA-256 rounds")


@registry.task(
    "demo.cpu_hash",
    payload=CpuHashPayload,
    description="CPU-bound simulation: iterated SHA-256, executed in a separate worker process.",
    executor="process",
    timeout=600,
    example={"iterations": 200000},
)
def cpu_hash(payload: CpuHashPayload) -> dict[str, Any]:
    digest = b"flowforge"
    for _ in range(payload.iterations):
        digest = hashlib.sha256(digest).digest()
    return {"iterations": payload.iterations, "digest": digest.hex()}


class FlakyPayload(_Strict):
    failure_rate: float = Field(
        default=0.5, ge=0, le=1, description="Probability that an attempt fails"
    )
    work_seconds: float = Field(default=0.2, ge=0, le=60)


@registry.task(
    "demo.flaky",
    payload=FlakyPayload,
    description="Fails randomly with a retryable error — demonstrates retries with exponential backoff.",
    max_retries=4,
    timeout=120,
    example={"failure_rate": 0.6, "work_seconds": 0.5},
)
async def flaky(ctx: TaskContext, payload: FlakyPayload) -> dict[str, Any]:
    await asyncio.sleep(payload.work_seconds)
    roll = random.random()  # nosec B311 - simulated failure rate (demo handler)
    if roll < payload.failure_rate:
        raise RetryableError(
            f"simulated transient failure (attempt {ctx.attempt}, roll={roll:.2f})"
        )
    ctx.log.info("succeeded", attempt=ctx.attempt)
    return {"attempt": ctx.attempt, "roll": round(roll, 4)}


class FailPayload(_Strict):
    message: str = Field(default="simulated permanent failure", max_length=500)
    retryable: bool = Field(default=False, description="Retry before dead-lettering?")


@registry.task(
    "demo.fail",
    payload=FailPayload,
    description="Always fails — demonstrates the dead-letter queue.",
    max_retries=2,
    timeout=60,
    example={"message": "upstream system rejected the request", "retryable": True},
)
async def fail(ctx: TaskContext, payload: FailPayload) -> None:
    ctx.log.warning("about to fail on purpose", retryable=payload.retryable)
    if payload.retryable:
        raise RetryableError(payload.message)
    raise NonRetryableError(payload.message)
