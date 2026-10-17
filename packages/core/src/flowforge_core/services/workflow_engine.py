"""Workflow execution: starts runs, advances the DAG as node tasks finish, resumes after manual retries, cancels.

Correctness under concurrency: every progression step locks the ``workflow_runs`` row (``SELECT … FOR UPDATE``)
first, so two parents finishing at the same moment on different workers are serialized — a child whose last
dependency just completed is started exactly once. A partial unique index on ``tasks.workflow_node_run_id`` is a
second, database-level guarantee.

Semantics:
* A node starts when all of its upstream nodes SUCCEEDED.
* When a node's task ends FAILED/DEAD_LETTERED, all its PENDING descendants become SKIPPED; independent branches
  continue. When nothing is PENDING or RUNNING any more, the run ends SUCCEEDED (all nodes succeeded) or FAILED.
* Manually retrying a failed node's task resumes a FAILED run: that node becomes RUNNING again and the descendants
  that were skipped only because of it return to PENDING.
* Cancelling a run marks it CANCELLED immediately; queued node tasks are cancelled and running ones receive a
  cooperative cancellation request.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.context import Actor
from flowforge_core.db.models import (
    Task,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, InvalidStateTransition, NotFound
from flowforge_core.services import audit
from flowforge_core.services import tasks as task_service
from flowforge_shared.dag import descendants
from flowforge_shared.messages import DispatchReason, EventType, RealtimeEvent
from flowforge_shared.states import (
    TERMINAL_WORKFLOW_RUN_STATES,
    NodeRunState,
    TaskState,
    WorkflowRunState,
    assert_node_run_transition,
    assert_workflow_run_transition,
)

NRS = NodeRunState
WRS = WorkflowRunState


def _run_event(run: WorkflowRun, **extra: Any) -> RealtimeEvent:
    return RealtimeEvent(
        type=EventType.WORKFLOW_RUN_UPDATED,
        project_id=run.project_id,
        data={"id": str(run.id), "workflow_id": str(run.workflow_id), "state": run.state, **extra},
    )


def _set_node_state(node_run: WorkflowNodeRun, target: NodeRunState) -> None:
    assert_node_run_transition(NodeRunState(node_run.state), target)
    node_run.state = target
    if target is NRS.RUNNING:
        node_run.started_at = func.now()
        node_run.finished_at = None
    elif target is NRS.PENDING:
        node_run.started_at = None
        node_run.finished_at = None
    else:
        node_run.finished_at = func.now()


def _set_run_state(run: WorkflowRun, target: WorkflowRunState, error: str | None = None) -> None:
    assert_workflow_run_transition(WorkflowRunState(run.state), target)
    run.state = target
    run.version = run.version + 1
    if target in TERMINAL_WORKFLOW_RUN_STATES:
        run.finished_at = func.now()
        run.error = error
    else:
        run.finished_at = None
        run.error = None


async def _lock_run(session: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
    run = await session.scalar(
        select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
    )
    if run is None:
        raise NotFound("workflow run not found")
    return run


async def _graph_for_run(
    session: AsyncSession, run: WorkflowRun
) -> tuple[dict[uuid.UUID, WorkflowNode], dict[str, list[str]], dict[str, list[str]]]:
    nodes = (
        await session.scalars(
            select(WorkflowNode).where(WorkflowNode.workflow_version_id == run.workflow_version_id)
        )
    ).all()
    by_id = {n.id: n for n in nodes}
    preds: dict[str, list[str]] = {n.node_key: [] for n in nodes}
    succs: dict[str, list[str]] = {n.node_key: [] for n in nodes}
    for src, dst in (
        await session.execute(
            select(WorkflowEdge.source_node_id, WorkflowEdge.target_node_id).where(
                WorkflowEdge.workflow_version_id == run.workflow_version_id
            )
        )
    ).all():
        succs[by_id[src].node_key].append(by_id[dst].node_key)
        preds[by_id[dst].node_key].append(by_id[src].node_key)
    return by_id, preds, succs


async def _node_runs(session: AsyncSession, run_id: uuid.UUID) -> dict[str, WorkflowNodeRun]:
    rows = await session.scalars(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run_id).with_for_update()
    )
    return {nr.node_key: nr for nr in rows}


async def _start_nodes(
    uow: UnitOfWork,
    run: WorkflowRun,
    node_runs: list[WorkflowNodeRun],
    nodes_by_id: dict[uuid.UUID, WorkflowNode],
    actor: Actor,
) -> None:
    if not node_runs:
        return
    specs = []
    origins = []
    for nr in node_runs:
        node = nodes_by_id[nr.workflow_node_id]
        specs.append(
            task_service.TaskSpec(
                task_type=node.task_type,
                payload=node.payload,
                queue=node.queue or "default",
                priority=node.priority if node.priority is not None else 5,
                max_retries=node.max_retries if node.max_retries is not None else 3,
                timeout_seconds=node.timeout_seconds or 300,
                metadata={"workflow_run_id": str(run.id), "node_key": nr.node_key},
            )
        )
        origins.append(
            task_service.SubmitOrigin(workflow_run_id=run.id, workflow_node_run_id=nr.id)
        )
    results = await task_service.submit_tasks(
        uow,
        project_id=run.project_id,
        specs=specs,
        actor=actor,
        reason=DispatchReason.WORKFLOW,
        origins=origins,
    )
    for nr, res in zip(node_runs, results, strict=True):
        nr.task_id = res.task.id
        _set_node_state(nr, NRS.RUNNING)


async def start_run(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    run_input: dict[str, Any],
    actor: Actor,
    version: int | None = None,
) -> WorkflowRun:
    session = uow.session
    workflow = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.project_id == project_id)
    )
    if workflow is None:
        raise NotFound("workflow not found")
    if workflow.archived_at is not None:
        raise Conflict("workflow is archived")
    wv = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version == (version or workflow.latest_version),
        )
    )
    if wv is None:
        raise NotFound("workflow version not found")
    run = WorkflowRun(
        project_id=project_id,
        workflow_id=workflow_id,
        workflow_version_id=wv.id,
        state=WRS.RUNNING,
        input=run_input,
        started_at=func.now(),
        created_by_user_id=actor.user_id,
        created_by_api_key_id=actor.api_key_id,
    )
    session.add(run)
    await session.flush()
    nodes_by_id, preds, _ = await _graph_for_run(session, run)
    node_runs: list[WorkflowNodeRun] = []
    for node in nodes_by_id.values():
        nr = WorkflowNodeRun(
            workflow_run_id=run.id,
            workflow_node_id=node.id,
            node_key=node.node_key,
            state=NRS.PENDING,
            remaining_deps=len(preds[node.node_key]),
        )
        session.add(nr)
        node_runs.append(nr)
    await session.flush()
    await _start_nodes(
        uow, run, [nr for nr in node_runs if nr.remaining_deps == 0], nodes_by_id, actor
    )
    await session.flush()
    await session.refresh(run)
    await audit.record(
        session,
        actor=actor,
        action="workflow.execute",
        resource_type="workflow_run",
        resource_id=run.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"workflow_id": str(workflow_id), "version": wv.version},
    )
    uow.emit(_run_event(run))
    return run


async def _maybe_finish_run(run: WorkflowRun, node_runs: dict[str, WorkflowNodeRun]) -> None:
    if run.state != WRS.RUNNING:
        return
    states = {NodeRunState(nr.state) for nr in node_runs.values()}
    if states & {NRS.PENDING, NRS.RUNNING}:
        return
    if states == {NRS.SUCCEEDED}:
        _set_run_state(run, WRS.SUCCEEDED)
    else:
        failed = sorted(k for k, nr in node_runs.items() if nr.state in (NRS.FAILED, NRS.CANCELLED))
        _set_run_state(run, WRS.FAILED, error=f"node(s) failed: {', '.join(failed)}")


async def on_task_finished(uow: UnitOfWork, task_row: Any, final_state: TaskState) -> None:
    """Called inside the transaction that moved a workflow node's task to a terminal state."""
    session = uow.session
    run = await _lock_run(session, task_row.workflow_run_id)
    node_runs = await _node_runs(session, run.id)
    node_run = next(
        (nr for nr in node_runs.values() if nr.id == task_row.workflow_node_run_id), None
    )
    if node_run is None or node_run.state != NRS.RUNNING:
        return  # already reconciled (e.g. run cancelled and node closed out)
    nodes_by_id, _, succs = await _graph_for_run(session, run)

    if final_state is TaskState.SUCCEEDED:
        _set_node_state(node_run, NRS.SUCCEEDED)
        ready: list[WorkflowNodeRun] = []
        for child_key in succs[node_run.node_key]:
            child = node_runs[child_key]
            child.remaining_deps = max(0, child.remaining_deps - 1)
            if (
                child.remaining_deps == 0
                and child.state == NRS.PENDING
                and run.state == WRS.RUNNING
            ):
                ready.append(child)
        await _start_nodes(uow, run, ready, nodes_by_id, Actor("system", "workflow-engine"))
    else:
        _set_node_state(
            node_run, NRS.CANCELLED if final_state is TaskState.CANCELLED else NRS.FAILED
        )
        for key in descendants(succs, node_run.node_key):
            if node_runs[key].state == NRS.PENDING:
                _set_node_state(node_runs[key], NRS.SKIPPED)

    await _maybe_finish_run(run, node_runs)
    await session.flush()
    uow.emit(_run_event(run, node={"key": node_run.node_key, "state": node_run.state}))


async def before_manual_retry(uow: UnitOfWork, task: Task) -> None:
    """Resume the run when an operator retries the task of a failed node."""
    session = uow.session
    if task.workflow_run_id is None:
        raise NotFound("task does not belong to a workflow run")
    run = await _lock_run(session, task.workflow_run_id)
    if run.state in (WRS.SUCCEEDED, WRS.CANCELLED):
        raise InvalidStateTransition(f"workflow run is {run.state}; start a new run instead")
    node_runs = await _node_runs(session, run.id)
    node_run = next((nr for nr in node_runs.values() if nr.id == task.workflow_node_run_id), None)
    if node_run is None:
        raise NotFound("workflow node run not found")
    if node_run.state != NRS.FAILED:
        raise InvalidStateTransition(
            f"node {node_run.node_key} is {node_run.state} and cannot be retried"
        )
    _, _, succs = await _graph_for_run(session, run)
    # Nodes still blocked by *other* failed/cancelled nodes stay skipped.
    still_blocked: set[str] = set()
    for key, nr in node_runs.items():
        if key != node_run.node_key and nr.state in (NRS.FAILED, NRS.CANCELLED):
            still_blocked |= descendants(succs, key)
    for key in descendants(succs, node_run.node_key):
        if node_runs[key].state == NRS.SKIPPED and key not in still_blocked:
            _set_node_state(node_runs[key], NRS.PENDING)
    _set_node_state(node_run, NRS.RUNNING)
    if run.state == WRS.FAILED:
        _set_run_state(run, WRS.RUNNING)
    await session.flush()
    uow.emit(_run_event(run, node={"key": node_run.node_key, "state": node_run.state}))


async def cancel_run(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    actor: Actor,
) -> WorkflowRun:
    session = uow.session
    run = await _lock_run(session, run_id)
    if run.project_id != project_id:
        raise NotFound("workflow run not found")
    if run.state == WRS.CANCELLED:
        return run
    if run.state in TERMINAL_WORKFLOW_RUN_STATES:
        raise InvalidStateTransition(f"workflow run is already {run.state}")
    _set_run_state(run, WRS.CANCELLED, error=f"cancelled by {actor.label or actor.kind}")
    run.cancel_requested_at = func.now()
    node_runs = await _node_runs(session, run.id)
    for nr in node_runs.values():
        if nr.state == NRS.PENDING:
            _set_node_state(nr, NRS.CANCELLED)
    await session.flush()
    for nr in node_runs.values():
        if nr.state == NRS.RUNNING and nr.task_id is not None:
            state = await session.scalar(select(Task.state).where(Task.id == nr.task_id))
            if state is not None and TaskState(state) not in (
                TaskState.SUCCEEDED,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.DEAD_LETTERED,
                TaskState.CANCEL_REQUESTED,
            ):
                await task_service.cancel_task(
                    uow,
                    project_id=project_id,
                    task_id=nr.task_id,
                    actor=actor,
                    organization_id=organization_id,
                )
    await session.flush()
    await session.refresh(run)
    await audit.record(
        session,
        actor=actor,
        action="workflow_run.cancel",
        resource_type="workflow_run",
        resource_id=run.id,
        organization_id=organization_id,
        project_id=project_id,
    )
    uow.emit(_run_event(run))
    return run


async def workflow_context(session: AsyncSession, task_id: uuid.UUID) -> dict[str, Any] | None:
    """Run input and the results of the direct upstream nodes, for a task that belongs to a workflow run."""
    row = (
        await session.execute(
            select(
                WorkflowNodeRun.workflow_run_id,
                WorkflowNodeRun.workflow_node_id,
                WorkflowNodeRun.node_key,
            )
            .join(Task, Task.workflow_node_run_id == WorkflowNodeRun.id)
            .where(Task.id == task_id)
        )
    ).one_or_none()
    if row is None:
        return None
    run_input = await session.scalar(
        select(WorkflowRun.input).where(WorkflowRun.id == row.workflow_run_id)
    )
    upstream_nodes = select(WorkflowEdge.source_node_id).where(
        WorkflowEdge.target_node_id == row.workflow_node_id
    )
    results = (
        await session.execute(
            select(WorkflowNodeRun.node_key, Task.result)
            .join(Task, Task.id == WorkflowNodeRun.task_id)
            .where(
                WorkflowNodeRun.workflow_run_id == row.workflow_run_id,
                WorkflowNodeRun.workflow_node_id.in_(upstream_nodes),
            )
        )
    ).all()
    return {
        "run_id": str(row.workflow_run_id),
        "node_key": row.node_key,
        "input": run_input or {},
        "upstream": {r.node_key: r.result for r in results},
    }
