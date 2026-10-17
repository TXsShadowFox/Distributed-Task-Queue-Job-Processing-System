import itertools

import pytest

from flowforge_shared.states import (
    ACTIVE_TASK_STATES,
    IMMEDIATELY_CANCELLABLE_STATES,
    LEASED_TASK_STATES,
    MANUALLY_RETRYABLE_STATES,
    NODE_RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_TASK_STATES,
    WORKFLOW_RUN_TRANSITIONS,
    InvalidTransitionError,
    NodeRunState,
    TaskState,
    WorkflowRunState,
    assert_node_run_transition,
    assert_transition,
    assert_workflow_run_transition,
    can_transition,
    is_terminal,
    legal_predecessors,
)

TS = TaskState

HAPPY_PATH = [TS.CREATED, TS.QUEUED, TS.RUNNING, TS.SUCCEEDED]


def test_every_state_has_a_transition_entry() -> None:
    assert set(TASK_TRANSITIONS) == set(TaskState)
    assert set(WORKFLOW_RUN_TRANSITIONS) == set(WorkflowRunState)
    assert set(NODE_RUN_TRANSITIONS) == set(NodeRunState)


def test_happy_path_is_legal() -> None:
    for current, target in itertools.pairwise(HAPPY_PATH):
        assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TS.CREATED, TS.RUNNING),  # must be queued first
        (TS.QUEUED, TS.SUCCEEDED),  # must run first
        (TS.SUCCEEDED, TS.QUEUED),  # successful tasks are never re-run
        (TS.SUCCEEDED, TS.FAILED),
        (TS.RUNNING, TS.QUEUED),  # releases go through RETRY_SCHEDULED
        (TS.CANCELLED, TS.RUNNING),
        (TS.DEAD_LETTERED, TS.RUNNING),
        (TS.CANCEL_REQUESTED, TS.RETRY_SCHEDULED),  # cancellation is never undone by a retry
        (TS.CANCEL_REQUESTED, TS.DEAD_LETTERED),
        (TS.SCHEDULED, TS.RUNNING),
        (TS.RETRY_SCHEDULED, TS.RUNNING),
    ],
)
def test_illegal_transitions_raise(current: TaskState, target: TaskState) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError, match=f"{current} -> {target}"):
        assert_transition(current, target)


def test_no_self_transitions() -> None:
    for state in TaskState:
        assert not can_transition(state, state)


def test_succeeded_is_absorbing() -> None:
    assert TASK_TRANSITIONS[TS.SUCCEEDED] == frozenset()


def test_terminal_and_active_partition_all_states() -> None:
    assert set(TaskState) == TERMINAL_TASK_STATES | ACTIVE_TASK_STATES
    assert not TERMINAL_TASK_STATES & ACTIVE_TASK_STATES
    assert all(is_terminal(s) for s in TERMINAL_TASK_STATES)


def test_terminal_states_only_lead_back_to_queue() -> None:
    for state in TERMINAL_TASK_STATES:
        assert TASK_TRANSITIONS[state] <= {TS.QUEUED}


def test_manual_retry_states_can_requeue() -> None:
    for state in MANUALLY_RETRYABLE_STATES:
        assert can_transition(state, TS.QUEUED)


def test_immediately_cancellable_states() -> None:
    for state in IMMEDIATELY_CANCELLABLE_STATES:
        assert can_transition(state, TS.CANCELLED)
    for state in LEASED_TASK_STATES:
        assert state not in IMMEDIATELY_CANCELLABLE_STATES
    assert can_transition(TS.RUNNING, TS.CANCEL_REQUESTED)


def test_completion_wins_over_cancel_request() -> None:
    # A handler that finishes before observing the cancel request records its success.
    assert can_transition(TS.CANCEL_REQUESTED, TS.SUCCEEDED)
    assert can_transition(TS.CANCEL_REQUESTED, TS.CANCELLED)


def test_legal_predecessors_match_table() -> None:
    for target in TaskState:
        expected = {s for s, dsts in TASK_TRANSITIONS.items() if target in dsts}
        assert legal_predecessors(target) == expected
    assert legal_predecessors(TS.QUEUED) == {
        TS.CREATED,
        TS.SCHEDULED,
        TS.RETRY_SCHEDULED,
        TS.FAILED,
        TS.CANCELLED,
        TS.DEAD_LETTERED,
    }
    assert legal_predecessors(TS.CREATED) == frozenset()


def test_every_non_initial_state_is_reachable_from_created() -> None:
    reachable = {TS.CREATED}
    frontier = [TS.CREATED]
    while frontier:
        state = frontier.pop()
        for nxt in TASK_TRANSITIONS[state]:
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert reachable == set(TaskState)


def test_workflow_run_transitions() -> None:
    assert_workflow_run_transition(WorkflowRunState.PENDING, WorkflowRunState.RUNNING)
    assert_workflow_run_transition(WorkflowRunState.FAILED, WorkflowRunState.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_workflow_run_transition(WorkflowRunState.SUCCEEDED, WorkflowRunState.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_workflow_run_transition(WorkflowRunState.CANCELLED, WorkflowRunState.RUNNING)


def test_node_run_transitions() -> None:
    assert_node_run_transition(NodeRunState.PENDING, NodeRunState.RUNNING)
    assert_node_run_transition(NodeRunState.SKIPPED, NodeRunState.PENDING)
    with pytest.raises(InvalidTransitionError):
        assert_node_run_transition(NodeRunState.SUCCEEDED, NodeRunState.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_node_run_transition(NodeRunState.PENDING, NodeRunState.SUCCEEDED)
