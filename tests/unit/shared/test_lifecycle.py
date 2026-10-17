import random

import pytest

from flowforge_shared.lifecycle import FailureKind, decide_failure
from flowforge_shared.states import AttemptStatus, TaskState, can_transition

BASE = {
    "retryable": True,
    "retry_count": 0,
    "max_retries": 3,
    "dead_letter": True,
    "backoff_base_seconds": 2.0,
    "backoff_max_seconds": 60.0,
}


def decide(**overrides: object):  # type: ignore[no-untyped-def]
    args = {**BASE, "current_state": TaskState.RUNNING, "kind": FailureKind.ERROR, **overrides}
    return decide_failure(rng=random.Random(0), **args)  # type: ignore[arg-type]


def test_first_failure_schedules_retry_with_backoff() -> None:
    d = decide()
    assert d.next_state is TaskState.RETRY_SCHEDULED
    assert d.attempt_status is AttemptStatus.FAILED
    assert d.consumes_retry
    assert d.retry_delay_seconds is not None
    assert 1.0 <= d.retry_delay_seconds <= 2.0  # equal jitter of base delay 2s


def test_backoff_grows_with_retry_count() -> None:
    d = decide(retry_count=2)
    assert d.retry_delay_seconds is not None
    assert 4.0 <= d.retry_delay_seconds <= 8.0


def test_retries_exhausted_dead_letters() -> None:
    d = decide(retry_count=3)
    assert d.next_state is TaskState.DEAD_LETTERED
    assert "exhausted" in d.reason
    assert not d.consumes_retry


def test_retries_exhausted_without_dlq_fails() -> None:
    assert decide(retry_count=3, dead_letter=False).next_state is TaskState.FAILED


def test_non_retryable_error_skips_retries() -> None:
    d = decide(retryable=False)
    assert d.next_state is TaskState.DEAD_LETTERED
    assert d.reason == "non-retryable error"


@pytest.mark.parametrize(
    ("kind", "status"),
    [(FailureKind.TIMEOUT, AttemptStatus.TIMED_OUT), (FailureKind.LOST, AttemptStatus.LOST)],
)
def test_timeouts_and_lost_leases_are_retried(kind: FailureKind, status: AttemptStatus) -> None:
    d = decide(kind=kind)
    assert d.next_state is TaskState.RETRY_SCHEDULED
    assert d.attempt_status is status


def test_interrupted_requeues_immediately_without_consuming_retry() -> None:
    d = decide(kind=FailureKind.INTERRUPTED, retry_count=3)
    assert d.next_state is TaskState.RETRY_SCHEDULED
    assert d.retry_delay_seconds == 0
    assert not d.consumes_retry
    assert d.attempt_status is AttemptStatus.INTERRUPTED


@pytest.mark.parametrize("kind", list(FailureKind))
def test_cancel_requested_always_ends_cancelled(kind: FailureKind) -> None:
    d = decide(current_state=TaskState.CANCEL_REQUESTED, kind=kind)
    assert d.next_state is TaskState.CANCELLED
    assert d.attempt_status is AttemptStatus.CANCELLED


@pytest.mark.parametrize("state", [TaskState.QUEUED, TaskState.SUCCEEDED, TaskState.DEAD_LETTERED])
def test_rejects_non_leased_states(state: TaskState) -> None:
    with pytest.raises(ValueError):
        decide(current_state=state)


@pytest.mark.parametrize("retry_count", range(5))
@pytest.mark.parametrize("current", [TaskState.RUNNING, TaskState.CANCEL_REQUESTED])
@pytest.mark.parametrize("kind", list(FailureKind))
@pytest.mark.parametrize("dead_letter", [True, False])
@pytest.mark.parametrize("retryable", [True, False])
def test_every_decision_is_a_legal_transition(
    retry_count: int, current: TaskState, kind: FailureKind, dead_letter: bool, retryable: bool
) -> None:
    d = decide(
        current_state=current,
        kind=kind,
        retry_count=retry_count,
        dead_letter=dead_letter,
        retryable=retryable,
    )
    assert can_transition(current, d.next_state)
