import random

import pytest

from flowforge_shared.retry import (
    JitterMode,
    RetryPolicy,
    backoff_delay,
    retries_remaining,
    should_retry,
)


def test_exponential_growth_without_jitter() -> None:
    policy = RetryPolicy(
        base_delay_seconds=1, max_delay_seconds=100, multiplier=2, jitter=JitterMode.NONE
    )
    assert [backoff_delay(n, policy) for n in range(1, 8)] == [1, 2, 4, 8, 16, 32, 64]


def test_delay_is_capped() -> None:
    policy = RetryPolicy(base_delay_seconds=1, max_delay_seconds=10, jitter=JitterMode.NONE)
    assert backoff_delay(20, policy) == 10


def test_huge_retry_numbers_do_not_overflow() -> None:
    policy = RetryPolicy(
        base_delay_seconds=1, max_delay_seconds=300, multiplier=10, jitter=JitterMode.NONE
    )
    assert backoff_delay(10_000, policy) == 300


@pytest.mark.parametrize("retry_number", [1, 2, 5, 12])
def test_equal_jitter_bounds(retry_number: int) -> None:
    policy = RetryPolicy(base_delay_seconds=2, max_delay_seconds=60, jitter=JitterMode.EQUAL)
    cap = min(60, 2 * 2 ** (retry_number - 1))
    rng = random.Random(42)
    samples = [backoff_delay(retry_number, policy, rng) for _ in range(2000)]
    assert min(samples) >= cap / 2
    assert max(samples) <= cap
    # jitter actually spreads values (not a constant)
    assert max(samples) - min(samples) > cap * 0.3


def test_full_jitter_bounds() -> None:
    policy = RetryPolicy(base_delay_seconds=4, max_delay_seconds=60, jitter=JitterMode.FULL)
    rng = random.Random(7)
    samples = [backoff_delay(3, policy, rng) for _ in range(2000)]
    assert min(samples) >= 0
    assert max(samples) <= 16
    assert min(samples) < 2  # full jitter can retry almost immediately


def test_jitter_is_deterministic_with_seeded_rng() -> None:
    policy = RetryPolicy()
    a = [backoff_delay(n, policy, random.Random(1)) for n in range(1, 5)]
    b = [backoff_delay(n, policy, random.Random(1)) for n in range(1, 5)]
    assert a == b


def test_zero_base_delay_means_immediate_retry() -> None:
    policy = RetryPolicy(base_delay_seconds=0, max_delay_seconds=0)
    assert backoff_delay(3, policy) == 0


def test_retry_number_starts_at_one() -> None:
    with pytest.raises(ValueError, match="starts at 1"):
        backoff_delay(0, RetryPolicy())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"base_delay_seconds": -1},
        {"base_delay_seconds": 10, "max_delay_seconds": 5},
        {"multiplier": 0.5},
    ],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_should_retry_and_remaining() -> None:
    assert should_retry(retry_count=0, max_retries=3, retryable=True)
    assert should_retry(retry_count=2, max_retries=3, retryable=True)
    assert not should_retry(retry_count=3, max_retries=3, retryable=True)
    assert not should_retry(retry_count=0, max_retries=3, retryable=False)
    assert not should_retry(retry_count=0, max_retries=0, retryable=True)
    assert retries_remaining(1, 3) == 2
    assert retries_remaining(5, 3) == 0
