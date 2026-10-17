from __future__ import annotations

import uuid
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.workflows import (
    EdgeOut,
    ExecuteIn,
    GraphIn,
    GraphOut,
    NodeOut,
    NodeRunOut,
    Position,
    RunDetailOut,
    RunListOut,
    RunSummaryOut,
    ValidateOut,
    ValidationErrorOut,
    VersionOut,
    WorkflowCreateIn,
    WorkflowDetailOut,
    WorkflowOut,
    WorkflowUpdateIn,
)
from flowforge_core.db.models import (
    Task,
    Workflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from flowforge_core.errors import NotFound
from flowforge_core.services import workflow_engine, workflows
from flowforge_shared.dag import analyze_dag
from flowforge_shared.rbac import Permission

router = APIRouter(prefix="/workflows", tags=["workflows"])
runs_router = APIRouter(prefix="/workflow-runs", tags=["workflows"])

WfRead = Annotated[ProjectContext, Depends(project_context(Permission.WORKFLOWS_READ))]
WfWrite = Annotated[ProjectContext, Depends(project_context(Permission.WORKFLOWS_WRITE))]


def _graph_out(view: workflows.GraphView) -> GraphOut:
    return GraphOut(
        version=view.version.version,
        nodes=[
            NodeOut(
                key=n.node_key,
                name=n.name,
                task_type=n.task_type,
                payload=n.payload,
                queue=n.queue,
                priority=n.priority,
                max_retries=n.max_retries,
                timeout_seconds=n.timeout_seconds,
                position=Position(x=n.position_x, y=n.position_y),
            )
            for n in view.nodes
        ],
        edges=[EdgeOut(source=s, target=t) for s, t in view.edges],
        layout=view.version.layout,
    )


async def _node_counts(
    session: AsyncSession, run_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    if not run_ids:
        return {}
    rows = (
        await session.execute(
            select(WorkflowNodeRun.workflow_run_id, WorkflowNodeRun.state, func.count())
            .where(WorkflowNodeRun.workflow_run_id.in_(run_ids))
            .group_by(WorkflowNodeRun.workflow_run_id, WorkflowNodeRun.state)
        )
    ).all()
    out: dict[uuid.UUID, dict[str, int]] = {}
    for run_id, state, n in rows:
        out.setdefault(run_id, {})[state] = int(n)
    return out


def _run_summary(
    run: WorkflowRun, name: str | None, version: int | None, counts: dict[str, int]
) -> RunSummaryOut:
    return RunSummaryOut(
        id=run.id,
        workflow_id=run.workflow_id,
        workflow_name=name,
        version=version,
        state=run.state,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        node_counts=counts,
    )


async def _workflow_out(session: AsyncSession, wf: Workflow) -> WorkflowOut:
    latest = (
        await session.execute(
            select(WorkflowRun, WorkflowVersion.version)
            .join(WorkflowVersion, WorkflowVersion.id == WorkflowRun.workflow_version_id)
            .where(WorkflowRun.workflow_id == wf.id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
        )
    ).one_or_none()
    node_count = await session.scalar(
        select(func.count(WorkflowNode.id))
        .join(WorkflowVersion, WorkflowVersion.id == WorkflowNode.workflow_version_id)
        .where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.latest_version)
    )
    last_run = None
    if latest is not None:
        latest_run, run_version = latest
        counts = await _node_counts(session, [latest_run.id])
        last_run = _run_summary(latest_run, wf.name, run_version, counts.get(latest_run.id, {}))
    return WorkflowOut(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        latest_version=wf.latest_version,
        archived_at=wf.archived_at,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        node_count=int(node_count or 0),
        last_run=last_run,
    )


async def _detail(
    session: AsyncSession, wf: Workflow, version: int | None = None
) -> WorkflowDetailOut:
    base = await _workflow_out(session, wf)
    view = await workflows.load_graph(session, wf.id, version)
    versions = await workflows.list_versions(session, wf.id)
    return WorkflowDetailOut(
        **base.model_dump(),
        graph=_graph_out(view),
        versions=[
            VersionOut(
                version=v.version, created_at=v.created_at, created_by_user_id=v.created_by_user_id
            )
            for v in versions
        ],
    )


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    ctx: WfRead, state: StateDep, include_archived: bool = False
) -> list[WorkflowOut]:
    stmt = select(Workflow).where(Workflow.project_id == ctx.project_id)
    if not include_archived:
        stmt = stmt.where(Workflow.archived_at.is_(None))
    async with state.db.session() as session:
        rows = (await session.scalars(stmt.order_by(Workflow.updated_at.desc()))).all()
        return [await _workflow_out(session, wf) for wf in rows]


@router.post("/validate", response_model=ValidateOut, summary="Validate a graph without saving it")
async def validate_workflow(body: GraphIn, _: WfRead, state: StateDep) -> ValidateOut:
    graph = body.to_input()
    errors = await workflows.validate_graph(state.catalog, graph)
    order: list[str] = []
    if not errors:
        order = list(analyze_dag([n.key for n in graph.nodes], graph.edges).order)
    return ValidateOut(
        valid=not errors, errors=[ValidationErrorOut(**e) for e in errors], order=order
    )


@router.post(
    "",
    status_code=201,
    response_model=WorkflowDetailOut,
    responses=error_responses(401, 403, 409, 422),
)
async def create_workflow(
    body: WorkflowCreateIn, ctx: WfWrite, state: StateDep
) -> WorkflowDetailOut:
    async with state.db.transaction() as uow:
        wf = await workflows.create_workflow(
            uow,
            catalog=state.catalog,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            name=body.name,
            description=body.description,
            graph=body.graph.to_input(),
            actor=ctx.actor,
        )
        return await _detail(uow.session, wf)


@router.get(
    "/{workflow_id}", response_model=WorkflowDetailOut, responses=error_responses(401, 403, 404)
)
async def get_workflow(
    workflow_id: uuid.UUID,
    ctx: WfRead,
    state: StateDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> WorkflowDetailOut:
    async with state.db.session() as session:
        wf = await workflows.get_workflow(session, ctx.project_id, workflow_id)
        return await _detail(session, wf, version)


@router.put(
    "/{workflow_id}",
    response_model=WorkflowDetailOut,
    responses=error_responses(401, 403, 404, 409, 422),
    summary="Save a new version of the workflow graph",
)
async def update_workflow(
    workflow_id: uuid.UUID, body: WorkflowUpdateIn, ctx: WfWrite, state: StateDep
) -> WorkflowDetailOut:
    async with state.db.transaction() as uow:
        wf = await workflows.save_new_version(
            uow,
            catalog=state.catalog,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            workflow_id=workflow_id,
            graph=body.graph.to_input(),
            actor=ctx.actor,
            expected_version=body.expected_version,
            name=body.name,
            description=body.description,
        )
        return await _detail(uow.session, wf)


@router.delete(
    "/{workflow_id}", response_model=WorkflowOut, responses=error_responses(401, 403, 404)
)
async def archive_workflow(workflow_id: uuid.UUID, ctx: WfWrite, state: StateDep) -> WorkflowOut:
    async with state.db.transaction() as uow:
        wf = await workflows.archive_workflow(
            uow,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            workflow_id=workflow_id,
            actor=ctx.actor,
        )
        return await _workflow_out(uow.session, wf)


@router.post(
    "/{workflow_id}/execute",
    status_code=201,
    response_model=RunDetailOut,
    responses=error_responses(401, 403, 404, 409),
)
async def execute_workflow(
    workflow_id: uuid.UUID, body: ExecuteIn, ctx: WfWrite, state: StateDep
) -> RunDetailOut:
    async with state.db.transaction() as uow:
        run = await workflow_engine.start_run(
            uow,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            workflow_id=workflow_id,
            run_input=body.input,
            actor=ctx.actor,
            version=body.version,
        )
    async with state.db.session() as session:
        return await _run_detail(session, run.id, ctx.project_id)


@router.get(
    "/{workflow_id}/runs", response_model=RunListOut, responses=error_responses(401, 403, 404)
)
async def list_workflow_runs(
    workflow_id: uuid.UUID,
    ctx: WfRead,
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunListOut:
    async with state.db.session() as session:
        wf = await workflows.get_workflow(session, ctx.project_id, workflow_id)
        return await _list_runs(session, ctx.project_id, limit, workflow_id=wf.id)


async def _list_runs(
    session: AsyncSession,
    project_id: uuid.UUID,
    limit: int,
    *,
    workflow_id: uuid.UUID | None = None,
    state_filter: str | None = None,
) -> RunListOut:
    stmt = (
        select(WorkflowRun, Workflow.name, WorkflowVersion.version)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .join(WorkflowVersion, WorkflowVersion.id == WorkflowRun.workflow_version_id)
        .where(WorkflowRun.project_id == project_id)
    )
    if workflow_id is not None:
        stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
    if state_filter:
        stmt = stmt.where(WorkflowRun.state == state_filter)
    rows = (await session.execute(stmt.order_by(WorkflowRun.created_at.desc()).limit(limit))).all()
    counts = await _node_counts(session, [r[0].id for r in rows])
    return RunListOut(items=[_run_summary(r[0], r[1], r[2], counts.get(r[0].id, {})) for r in rows])


async def _run_detail(
    session: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID
) -> RunDetailOut:
    row = (
        await session.execute(
            select(WorkflowRun, Workflow.name, WorkflowVersion)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .join(WorkflowVersion, WorkflowVersion.id == WorkflowRun.workflow_version_id)
            .where(WorkflowRun.id == run_id, WorkflowRun.project_id == project_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("workflow run not found")
    run, name, wv = row
    view = await workflows.load_graph_by_version(session, wv)
    nodes_by_id = {n.id: n for n in view.nodes}
    node_runs = (
        await session.execute(
            select(WorkflowNodeRun, Task.state, Task.attempt_count)
            .outerjoin(Task, Task.id == WorkflowNodeRun.task_id)
            .where(WorkflowNodeRun.workflow_run_id == run.id)
        )
    ).all()
    nodes = []
    for nr, task_state, attempts in node_runs:
        node = nodes_by_id[nr.workflow_node_id]
        nodes.append(
            NodeRunOut(
                key=nr.node_key,
                name=node.name,
                task_type=node.task_type,
                state=nr.state,
                task_id=nr.task_id,
                task_state=task_state,
                attempt_count=attempts,
                started_at=nr.started_at,
                finished_at=nr.finished_at,
                position=Position(x=node.position_x, y=node.position_y),
            )
        )
    nodes.sort(key=lambda n: n.key)
    counts = dict(Counter(n.state for n in nodes))
    summary = _run_summary(run, name, wv.version, counts)
    return RunDetailOut(
        **summary.model_dump(),
        input=run.input,
        nodes=nodes,
        edges=[EdgeOut(source=s, target=t) for s, t in view.edges],
    )


@runs_router.get("", response_model=RunListOut)
async def list_runs(
    ctx: WfRead,
    state: StateDep,
    state_filter: Annotated[str | None, Query(alias="state", max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunListOut:
    async with state.db.session() as session:
        return await _list_runs(session, ctx.project_id, limit, state_filter=state_filter)


@runs_router.get("/{run_id}", response_model=RunDetailOut, responses=error_responses(401, 403, 404))
async def get_run(run_id: uuid.UUID, ctx: WfRead, state: StateDep) -> RunDetailOut:
    async with state.db.session() as session:
        return await _run_detail(session, run_id, ctx.project_id)


@runs_router.post(
    "/{run_id}/cancel", response_model=RunDetailOut, responses=error_responses(401, 403, 404, 409)
)
async def cancel_run(run_id: uuid.UUID, ctx: WfWrite, state: StateDep) -> RunDetailOut:
    async with state.db.transaction() as uow:
        await workflow_engine.cancel_run(
            uow,
            project_id=ctx.project_id,
            organization_id=ctx.organization_id,
            run_id=run_id,
            actor=ctx.actor,
        )
    async with state.db.session() as session:
        return await _run_detail(session, run_id, ctx.project_id)
