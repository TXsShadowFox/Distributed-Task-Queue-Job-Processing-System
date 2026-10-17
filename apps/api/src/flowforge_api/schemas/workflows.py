from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from flowforge_api.schemas.common import StrictInput
from flowforge_core.services.workflows import GraphInput, NodeInput
from flowforge_shared.dag import NODE_KEY_PATTERN
from flowforge_shared.priority import QUEUE_NAME_PATTERN


class Position(StrictInput):
    x: float = 0.0
    y: float = 0.0


class NodeIn(StrictInput):
    key: str = Field(pattern=NODE_KEY_PATTERN, examples=["extract"])
    name: str = Field(min_length=1, max_length=120)
    task_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str | None = Field(default=None, pattern=QUEUE_NAME_PATTERN)
    priority: int | None = Field(default=None, ge=0, le=9)
    max_retries: int | None = Field(default=None, ge=0, le=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    position: Position = Field(default_factory=Position)


class EdgeIn(StrictInput):
    source: str = Field(pattern=NODE_KEY_PATTERN)
    target: str = Field(pattern=NODE_KEY_PATTERN)


class GraphIn(StrictInput):
    nodes: list[NodeIn] = Field(min_length=1, max_length=200)
    edges: list[EdgeIn] = Field(default_factory=list, max_length=1000)
    layout: dict[str, Any] = Field(default_factory=dict)

    def to_input(self) -> GraphInput:
        return GraphInput(
            nodes=[
                NodeInput(
                    key=n.key,
                    name=n.name,
                    task_type=n.task_type,
                    payload=n.payload,
                    queue=n.queue,
                    priority=n.priority,
                    max_retries=n.max_retries,
                    timeout_seconds=n.timeout_seconds,
                    position_x=n.position.x,
                    position_y=n.position.y,
                )
                for n in self.nodes
            ],
            edges=[(e.source, e.target) for e in self.edges],
            layout=self.layout,
        )


class WorkflowCreateIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn


class WorkflowUpdateIn(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn
    expected_version: int | None = Field(
        default=None, ge=1, description="Latest version the editor loaded (optimistic concurrency)"
    )


class NodeOut(BaseModel):
    key: str
    name: str
    task_type: str
    payload: dict[str, Any]
    queue: str | None
    priority: int | None
    max_retries: int | None
    timeout_seconds: int | None
    position: Position


class EdgeOut(BaseModel):
    source: str
    target: str


class GraphOut(BaseModel):
    version: int
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    layout: dict[str, Any]


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str | None = None
    version: int | None = None
    state: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    node_counts: dict[str, int] = Field(default_factory=dict)


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    latest_version: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    node_count: int | None = None
    last_run: RunSummaryOut | None = None


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    created_by_user_id: uuid.UUID | None


class WorkflowDetailOut(WorkflowOut):
    graph: GraphOut
    versions: list[VersionOut]


class ValidationErrorOut(BaseModel):
    code: str
    message: str
    nodes: list[str] = Field(default_factory=list)
    details: Any = None


class ValidateOut(BaseModel):
    valid: bool
    errors: list[ValidationErrorOut]
    order: list[str]


class ExecuteIn(StrictInput):
    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class NodeRunOut(BaseModel):
    key: str
    name: str
    task_type: str
    state: str
    task_id: uuid.UUID | None
    task_state: str | None
    attempt_count: int | None
    started_at: datetime | None
    finished_at: datetime | None
    position: Position


class RunDetailOut(RunSummaryOut):
    input: dict[str, Any]
    nodes: list[NodeRunOut]
    edges: list[EdgeOut]


class RunListOut(BaseModel):
    items: list[RunSummaryOut]
