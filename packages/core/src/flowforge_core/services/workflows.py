"""Workflow definitions. Every graph save creates a new immutable version; runs pin the version they started with."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.context import Actor
from flowforge_core.db.models import Workflow, WorkflowEdge, WorkflowNode, WorkflowVersion
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, NotFound, ValidationFailed
from flowforge_core.services import audit
from flowforge_core.services.task_types import TaskTypeCatalog
from flowforge_shared.dag import analyze_dag
from flowforge_shared.priority import MAX_PRIORITY, MIN_PRIORITY, validate_queue_name

MAX_TIMEOUT_SECONDS = 24 * 3600


@dataclass(slots=True)
class NodeInput:
    key: str
    name: str
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    queue: str | None = None
    priority: int | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None
    position_x: float = 0.0
    position_y: float = 0.0


@dataclass(slots=True)
class GraphInput:
    nodes: list[NodeInput]
    edges: list[tuple[str, str]]
    layout: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphView:
    version: WorkflowVersion
    nodes: list[WorkflowNode]
    edges: list[tuple[str, str]]  # (source_key, target_key)


async def validate_graph(catalog: TaskTypeCatalog, graph: GraphInput) -> list[dict[str, Any]]:
    """All problems with a graph (structure, unknown task types, invalid payloads/settings). Empty = valid."""
    analysis = analyze_dag([n.key for n in graph.nodes], graph.edges)
    errors: list[dict[str, Any]] = [
        {"code": e.code.value, "message": e.message, "nodes": list(e.nodes)}
        for e in analysis.errors
    ]
    for node in graph.nodes:
        if not node.name.strip() or len(node.name) > 120:
            errors.append(
                {
                    "code": "INVALID_NAME",
                    "message": "node name must be 1-120 characters",
                    "nodes": [node.key],
                }
            )
        entry = await catalog.get(node.task_type)
        if entry is None:
            errors.append(
                {
                    "code": "UNKNOWN_TASK_TYPE",
                    "message": f"unknown task type {node.task_type!r}",
                    "nodes": [node.key],
                }
            )
        else:
            try:
                entry.validate_payload(node.payload, location=f"nodes.{node.key}.payload")
            except ValidationFailed as exc:
                errors.append(
                    {
                        "code": "INVALID_PAYLOAD",
                        "message": exc.message,
                        "nodes": [node.key],
                        "details": exc.details,
                    }
                )
        if node.queue is not None:
            try:
                validate_queue_name(node.queue)
            except ValueError as exc:
                errors.append({"code": "INVALID_QUEUE", "message": str(exc), "nodes": [node.key]})
        if node.priority is not None and not MIN_PRIORITY <= node.priority <= MAX_PRIORITY:
            errors.append(
                {"code": "INVALID_PRIORITY", "message": "priority must be 0-9", "nodes": [node.key]}
            )
        if node.max_retries is not None and not 0 <= node.max_retries <= 100:
            errors.append(
                {
                    "code": "INVALID_RETRIES",
                    "message": "max_retries must be 0-100",
                    "nodes": [node.key],
                }
            )
        if (
            node.timeout_seconds is not None
            and not 1 <= node.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            errors.append(
                {
                    "code": "INVALID_TIMEOUT",
                    "message": f"timeout must be 1-{MAX_TIMEOUT_SECONDS}s",
                    "nodes": [node.key],
                }
            )
    return errors


async def _write_version(
    session: AsyncSession,
    catalog: TaskTypeCatalog,
    workflow: Workflow,
    version_number: int,
    graph: GraphInput,
    actor: Actor,
) -> WorkflowVersion:
    errors = await validate_graph(catalog, graph)
    if errors:
        raise ValidationFailed("workflow graph is invalid", details=errors)
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=version_number,
        layout=graph.layout,
        created_by_user_id=actor.user_id,
    )
    session.add(version)
    await session.flush()
    node_ids: dict[str, uuid.UUID] = {}
    for n in graph.nodes:
        entry = await catalog.require(n.task_type)
        node = WorkflowNode(
            workflow_version_id=version.id,
            node_key=n.key,
            name=n.name.strip(),
            task_type=n.task_type,
            payload=n.payload,
            # Defaults are resolved at save time so execution never depends on the catalog later.
            queue=n.queue or entry.queue,
            priority=n.priority if n.priority is not None else entry.default_priority,
            max_retries=n.max_retries if n.max_retries is not None else entry.default_max_retries,
            timeout_seconds=n.timeout_seconds or entry.default_timeout_seconds,
            position_x=n.position_x,
            position_y=n.position_y,
        )
        session.add(node)
        await session.flush()
        node_ids[n.key] = node.id
    for src, dst in graph.edges:
        session.add(
            WorkflowEdge(
                workflow_version_id=version.id,
                source_node_id=node_ids[src],
                target_node_id=node_ids[dst],
            )
        )
    await session.flush()
    return version


async def create_workflow(
    uow: UnitOfWork,
    *,
    catalog: TaskTypeCatalog,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    name: str,
    description: str | None,
    graph: GraphInput,
    actor: Actor,
) -> Workflow:
    session = uow.session
    exists = await session.scalar(
        select(Workflow.id).where(Workflow.project_id == project_id, Workflow.name == name)
    )
    if exists is not None:
        raise Conflict(f"a workflow named {name!r} already exists in this project")
    workflow = Workflow(
        project_id=project_id,
        name=name,
        description=description,
        latest_version=1,
        created_by_user_id=actor.user_id,
    )
    session.add(workflow)
    try:
        await session.flush()
    except IntegrityError:
        raise Conflict(f"a workflow named {name!r} already exists in this project") from None
    await _write_version(session, catalog, workflow, 1, graph, actor)
    await audit.record(
        session,
        actor=actor,
        action="workflow.create",
        resource_type="workflow",
        resource_id=workflow.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"name": name, "nodes": len(graph.nodes)},
    )
    return workflow


async def get_workflow(
    session: AsyncSession, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow:
    workflow = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.project_id == project_id)
    )
    if workflow is None:
        raise NotFound("workflow not found")
    return workflow


async def save_new_version(
    uow: UnitOfWork,
    *,
    catalog: TaskTypeCatalog,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    graph: GraphInput,
    actor: Actor,
    expected_version: int | None,
    name: str | None = None,
    description: str | None = None,
) -> Workflow:
    """Optimistic concurrency: ``expected_version`` must equal the latest version the editor loaded."""
    session = uow.session
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.project_id == project_id)
        .with_for_update()
    )
    if workflow is None:
        raise NotFound("workflow not found")
    if workflow.archived_at is not None:
        raise Conflict("workflow is archived")
    if expected_version is not None and expected_version != workflow.latest_version:
        raise Conflict(
            "workflow was modified by someone else; reload to see the latest version",
            details={"latest_version": workflow.latest_version},
        )
    new_version = workflow.latest_version + 1
    await _write_version(session, catalog, workflow, new_version, graph, actor)
    workflow.latest_version = new_version
    if name is not None:
        workflow.name = name
    if description is not None:
        workflow.description = description
    workflow.updated_at = func.now()
    try:
        await session.flush()
    except IntegrityError:
        raise Conflict("a workflow with this name already exists") from None
    await session.refresh(workflow)
    await audit.record(
        session,
        actor=actor,
        action="workflow.update",
        resource_type="workflow",
        resource_id=workflow.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"version": new_version},
    )
    return workflow


async def archive_workflow(
    uow: UnitOfWork,
    *,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    actor: Actor,
) -> Workflow:
    workflow = await get_workflow(uow.session, project_id, workflow_id)
    if workflow.archived_at is None:
        workflow.archived_at = func.now()
        await uow.session.flush()
        await uow.session.refresh(workflow)
        await audit.record(
            uow.session,
            actor=actor,
            action="workflow.archive",
            resource_type="workflow",
            resource_id=workflow.id,
            organization_id=organization_id,
            project_id=project_id,
        )
    return workflow


async def load_graph(
    session: AsyncSession, workflow_id: uuid.UUID, version: int | None = None
) -> GraphView:
    stmt = select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id)
    stmt = (
        stmt.where(WorkflowVersion.version == version)
        if version is not None
        else stmt.order_by(WorkflowVersion.version.desc()).limit(1)
    )
    wv = await session.scalar(stmt)
    if wv is None:
        raise NotFound("workflow version not found")
    return await load_graph_by_version(session, wv)


async def load_graph_by_version(session: AsyncSession, wv: WorkflowVersion) -> GraphView:
    nodes = list(
        (
            await session.scalars(
                select(WorkflowNode)
                .where(WorkflowNode.workflow_version_id == wv.id)
                .order_by(WorkflowNode.node_key)
            )
        ).all()
    )
    key_by_id = {n.id: n.node_key for n in nodes}
    edge_rows = (
        await session.execute(
            select(WorkflowEdge.source_node_id, WorkflowEdge.target_node_id).where(
                WorkflowEdge.workflow_version_id == wv.id
            )
        )
    ).all()
    edges = [(key_by_id[r.source_node_id], key_by_id[r.target_node_id]) for r in edge_rows]
    return GraphView(version=wv, nodes=nodes, edges=edges)


async def list_versions(session: AsyncSession, workflow_id: uuid.UUID) -> list[WorkflowVersion]:
    return list(
        (
            await session.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow_id)
                .order_by(WorkflowVersion.version.desc())
            )
        ).all()
    )
