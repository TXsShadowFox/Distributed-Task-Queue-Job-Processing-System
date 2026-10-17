"""Retry policy and exponential backoff math."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum


class JitterMode(StrEnum):
    NONE = "none"
    #: uniform(0, delay) — best load spreading, may retry almost immediately (AWS "full jitter").
    FULL = "full"
    #: delay/2 + uniform(0, delay/2) — spreads load while keeping at least half the backoff.
    EQUAL = "equal"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0
    multiplier: float = 2.0
    jitter: JitterMode = JitterMode.EQUAL

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")


def backoff_delay(
    retry_number: int, policy: RetryPolicy, rng: random.Random | None = None
) -> float:
    """Delay in seconds before retry ``retry_number`` (1 = first retry).

    ``base * multiplier^(n-1)`` capped at ``max_delay``, then jittered. Never negative, never above the cap.
    """
    if retry_number < 1:
        raise ValueError("retry_number starts at 1")
    try:
        raw = policy.base_delay_seconds * policy.multiplier ** (retry_number - 1)
    except OverflowError:
        raw = policy.max_delay_seconds
    capped = min(policy.max_delay_seconds, raw)
    if policy.jitter is JitterMode.NONE or capped == 0:
        return capped
    r = rng or random
    if policy.jitter is JitterMode.FULL:
        return r.uniform(0, capped)
    half = capped / 2
    return half + r.uniform(0, half)


def retries_remaining(retry_count: int, max_retries: int) -> int:
    return max(0, max_retries - retry_count)


def should_retry(*, retry_count: int, max_retries: int, retryable: bool) -> bool:
    """``retry_count`` is the number of automatic retries already consumed."""
    return retryable and retry_count < max_retries
