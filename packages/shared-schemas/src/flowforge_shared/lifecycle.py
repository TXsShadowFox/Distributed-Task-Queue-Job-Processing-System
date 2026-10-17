"""Pure decision logic for what happens after an attempt ends without success."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from flowforge_shared.retry import JitterMode, RetryPolicy, backoff_delay, should_retry
from flowforge_shared.states import AttemptStatus, TaskState


class FailureKind(StrEnum):
    ERROR = "error"  # handler raised
    TIMEOUT = "timeout"  # handler exceeded timeout_seconds
    LOST = "lost"  # lease expired (worker crashed / hung / partitioned)
    INTERRUPTED = "interrupted"  # graceful shutdown released the task before it finished


@dataclass(frozen=True, slots=True)
class FailureDecision:
    next_state: TaskState
    attempt_status: AttemptStatus
    retry_delay_seconds: float | None
    consumes_retry: bool
    reason: str


def decide_failure(
    *,
    current_state: TaskState,
    kind: FailureKind,
    retryable: bool,
    retry_count: int,
    max_retries: int,
    dead_letter: bool,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
    rng: random.Random | None = None,
) -> FailureDecision:
    if current_state not in (TaskState.RUNNING, TaskState.CANCEL_REQUESTED):
        raise ValueError(f"cannot decide failure for a task in state {current_state}")

    if current_state is TaskState.CANCEL_REQUESTED:
        # The operator asked for cancellation; whatever went wrong, the task ends cancelled and is not retried.
        return FailureDecision(
            TaskState.CANCELLED, AttemptStatus.CANCELLED, None, False, "cancelled while running"
        )

    if kind is FailureKind.INTERRUPTED:
        # Not the task's fault: requeue immediately without consuming a retry.
        return FailureDecision(
            TaskState.RETRY_SCHEDULED, AttemptStatus.INTERRUPTED, 0.0, False, "worker shutdown"
        )

    attempt_status = {
        FailureKind.ERROR: AttemptStatus.FAILED,
        FailureKind.TIMEOUT: AttemptStatus.TIMED_OUT,
        FailureKind.LOST: AttemptStatus.LOST,
    }[kind]

    if should_retry(retry_count=retry_count, max_retries=max_retries, retryable=retryable):
        policy = RetryPolicy(
            max_retries=max_retries,
            base_delay_seconds=backoff_base_seconds,
            max_delay_seconds=max(backoff_max_seconds, backoff_base_seconds),
            jitter=JitterMode.EQUAL,
        )
        delay = backoff_delay(retry_count + 1, policy, rng)
        return FailureDecision(
            TaskState.RETRY_SCHEDULED,
            attempt_status,
            delay,
            True,
            f"retry {retry_count + 1}/{max_retries} in {delay:.2f}s",
        )

    reason = "non-retryable error" if not retryable else f"retries exhausted ({max_retries})"
    final = TaskState.DEAD_LETTERED if dead_letter else TaskState.FAILED
    return FailureDecision(final, attempt_status, None, False, reason)
