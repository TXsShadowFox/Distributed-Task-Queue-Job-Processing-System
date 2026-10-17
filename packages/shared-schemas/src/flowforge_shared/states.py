"""Validated state machines for tasks, workflow runs and workflow node runs.

The task transition table defined here is the single source of truth. It is enforced in three places:

1. ``assert_transition`` — application-level validation with a clear error.
2. Guarded SQL updates (``UPDATE … WHERE state IN (legal predecessors)``) so concurrent writers cannot
   apply a transition from a state that changed underneath them.
3. A PostgreSQL trigger (see the initial migration) that rejects any illegal state change, even from manual SQL.
   An integration test checks that the trigger and this table agree for every (from, to) pair.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class TaskState(StrEnum):
    CREATED = "CREATED"
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    DEAD_LETTERED = "DEAD_LETTERED"


TS = TaskState

TASK_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = {
    # Initial state; always left within the creating transaction.
    TS.CREATED: frozenset({TS.QUEUED, TS.SCHEDULED, TS.CANCELLED}),
    # Delayed task waiting for its run time.
    TS.SCHEDULED: frozenset({TS.QUEUED, TS.CANCELLED}),
    # Dispatched to Redis; a worker claims it with a lease.
    TS.QUEUED: frozenset({TS.RUNNING, TS.CANCELLED}),
    # Leased by exactly one worker attempt (fenced by lease_token).
    TS.RUNNING: frozenset(
        {TS.SUCCEEDED, TS.RETRY_SCHEDULED, TS.FAILED, TS.DEAD_LETTERED, TS.CANCEL_REQUESTED}
    ),
    # Waiting for its backoff delay to elapse.
    TS.RETRY_SCHEDULED: frozenset({TS.QUEUED, TS.CANCELLED}),
    # Cooperative cancellation in progress. A completion that commits first wins (side effects happened).
    TS.CANCEL_REQUESTED: frozenset({TS.CANCELLED, TS.SUCCEEDED}),
    TS.SUCCEEDED: frozenset(),
    # Permanent failures and cancellations can be manually re-queued.
    TS.FAILED: frozenset({TS.QUEUED}),
    TS.CANCELLED: frozenset({TS.QUEUED}),
    TS.DEAD_LETTERED: frozenset({TS.QUEUED}),
}

TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {TS.SUCCEEDED, TS.FAILED, TS.CANCELLED, TS.DEAD_LETTERED}
)
ACTIVE_TASK_STATES: frozenset[TaskState] = frozenset(set(TaskState) - TERMINAL_TASK_STATES)
#: States in which a worker holds a lease.
LEASED_TASK_STATES: frozenset[TaskState] = frozenset({TS.RUNNING, TS.CANCEL_REQUESTED})
#: States that may be cancelled immediately (no worker involvement needed).
IMMEDIATELY_CANCELLABLE_STATES: frozenset[TaskState] = frozenset(
    {TS.CREATED, TS.SCHEDULED, TS.QUEUED, TS.RETRY_SCHEDULED}
)
#: States from which an operator may manually retry a task.
MANUALLY_RETRYABLE_STATES: frozenset[TaskState] = frozenset(
    {TS.FAILED, TS.CANCELLED, TS.DEAD_LETTERED}
)
#: States waiting for a time to arrive (promoted to QUEUED by the scheduler).
TIME_WAITING_STATES: frozenset[TaskState] = frozenset({TS.SCHEDULED, TS.RETRY_SCHEDULED})


class InvalidTransitionError(ValueError):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"illegal {entity} transition {current} -> {target}")
        self.entity = entity
        self.current = current
        self.target = target


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in TASK_TRANSITIONS[current]


def assert_transition(current: TaskState, target: TaskState) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError("task", current, target)


def legal_predecessors(target: TaskState) -> frozenset[TaskState]:
    """All states from which ``target`` may legally be entered."""
    return frozenset(src for src, dsts in TASK_TRANSITIONS.items() if target in dsts)


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_TASK_STATES


# --------------------------------------------------------------------------------------- workflow runs
class WorkflowRunState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


WRS = WorkflowRunState

WORKFLOW_RUN_TRANSITIONS: Mapping[WorkflowRunState, frozenset[WorkflowRunState]] = {
    WRS.PENDING: frozenset({WRS.RUNNING, WRS.CANCELLED}),
    WRS.RUNNING: frozenset({WRS.SUCCEEDED, WRS.FAILED, WRS.CANCELLED}),
    # A failed run resumes when an operator manually retries one of its failed node tasks.
    WRS.FAILED: frozenset({WRS.RUNNING}),
    WRS.SUCCEEDED: frozenset(),
    WRS.CANCELLED: frozenset(),
}

TERMINAL_WORKFLOW_RUN_STATES: frozenset[WorkflowRunState] = frozenset(
    {WRS.SUCCEEDED, WRS.FAILED, WRS.CANCELLED}
)


def assert_workflow_run_transition(current: WorkflowRunState, target: WorkflowRunState) -> None:
    if target not in WORKFLOW_RUN_TRANSITIONS[current]:
        raise InvalidTransitionError("workflow run", current, target)


class NodeRunState(StrEnum):
    PENDING = "PENDING"  # waiting for upstream dependencies
    RUNNING = "RUNNING"  # task exists and is not terminal (queued, running or retrying)
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # an upstream node failed
    CANCELLED = "CANCELLED"


NRS = NodeRunState

NODE_RUN_TRANSITIONS: Mapping[NodeRunState, frozenset[NodeRunState]] = {
    NRS.PENDING: frozenset({NRS.RUNNING, NRS.SKIPPED, NRS.CANCELLED}),
    NRS.RUNNING: frozenset({NRS.SUCCEEDED, NRS.FAILED, NRS.CANCELLED}),
    NRS.FAILED: frozenset({NRS.RUNNING}),
    NRS.SKIPPED: frozenset({NRS.PENDING, NRS.CANCELLED}),
    NRS.SUCCEEDED: frozenset(),
    NRS.CANCELLED: frozenset(),
}

TERMINAL_NODE_RUN_STATES: frozenset[NodeRunState] = frozenset(
    {NRS.SUCCEEDED, NRS.FAILED, NRS.SKIPPED, NRS.CANCELLED}
)


def assert_node_run_transition(current: NodeRunState, target: NodeRunState) -> None:
    if target not in NODE_RUN_TRANSITIONS[current]:
        raise InvalidTransitionError("workflow node run", current, target)


# --------------------------------------------------------------------------------------- attempts/workers
class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    LOST = "LOST"  # lease expired: worker crashed, hung or was partitioned
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"  # graceful shutdown released the task; does not consume a retry


class WorkerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    OFFLINE = "OFFLINE"  # heartbeat expired


class ScheduleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class MisfirePolicy(StrEnum):
    """What to do with fire times missed while the scheduler was down."""

    RUN_ONCE = "RUN_ONCE"  # fire once for the most recent missed time, then continue
    SKIP = "SKIP"  # drop missed fire times


class OverlapPolicy(StrEnum):
    """What to do when the previous task of a schedule is still active."""

    ALLOW = "ALLOW"
    SKIP = "SKIP"
