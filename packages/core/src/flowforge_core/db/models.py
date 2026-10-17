"""ORM models. All timestamps are UTC ``timestamptz``; all comparisons for leases and schedules use the database
clock (``now()``), never a worker's clock, so clock skew between hosts cannot expire leases early."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flowforge_core.db.base import Base, TimestampMixin
from flowforge_shared.ids import uuid7
from flowforge_shared.rbac import Role
from flowforge_shared.states import (
    AttemptStatus,
    MisfirePolicy,
    NodeRunState,
    OverlapPolicy,
    ScheduleStatus,
    TaskState,
    WorkerStatus,
    WorkflowRunState,
)


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


_EMPTY_JSON = text("'{}'::jsonb")


# ============================================================================== tenancy & identity
class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    password_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        CheckConstraint(_in("role", Role), name="role_valid"),
        Index("ix_organization_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)


class RefreshToken(Base):
    """Rotating refresh tokens. Reuse of a rotated token revokes the whole family (theft detection)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None]
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ApiKey(Base):
    """Only a SHA-256 hash of the key is stored; the raw key is shown exactly once at creation/rotation."""

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(24), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


# ============================================================================== task catalog & tasks
class TaskType(Base):
    """Catalog of task types, published by workers at start-up (name, defaults, JSON Schema of the payload)."""

    __tablename__ = "task_types"
    __table_args__ = (
        CheckConstraint("executor IN ('async', 'thread', 'process')", name="executor_valid"),
    )

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    queue: Mapped[str] = mapped_column(String(63))
    payload_schema: Mapped[dict[str, Any]]
    example_payload: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON)
    default_priority: Mapped[int] = mapped_column(SmallInteger)
    default_max_retries: Mapped[int] = mapped_column(Integer)
    default_timeout_seconds: Mapped[int] = mapped_column(Integer)
    executor: Mapped[str] = mapped_column(String(16))
    schema_hash: Mapped[str] = mapped_column(String(64))
    first_registered_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in("state", TaskState), name="state_valid"),
        CheckConstraint("priority BETWEEN 0 AND 9", name="priority_range"),
        CheckConstraint("max_retries BETWEEN 0 AND 100", name="max_retries_range"),
        CheckConstraint("retry_count >= 0 AND attempt_count >= 0", name="counters_non_negative"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
        CheckConstraint("timeout_seconds > 0", name="timeout_positive"),
        CheckConstraint(
            "(state IN ('RUNNING', 'CANCEL_REQUESTED')) = (lease_token IS NOT NULL)",
            name="lease_iff_leased_state",
        ),
        CheckConstraint(
            "(state IN ('SCHEDULED', 'RETRY_SCHEDULED')) = (available_at IS NOT NULL)",
            name="available_at_iff_waiting",
        ),
        Index("ix_tasks_project_created", "project_id", text("created_at DESC"), text("id DESC")),
        Index("ix_tasks_project_state_created", "project_id", "state", text("created_at DESC")),
        Index("ix_tasks_project_type_created", "project_id", "task_type", text("created_at DESC")),
        Index(
            "ix_tasks_waiting_available_at",
            "available_at",
            postgresql_where=text("state IN ('SCHEDULED', 'RETRY_SCHEDULED')"),
        ),
        Index(
            "ix_tasks_leased_expiry",
            "lease_expires_at",
            postgresql_where=text("state IN ('RUNNING', 'CANCEL_REQUESTED')"),
        ),
        Index(
            "ix_tasks_queued",
            "queue",
            "priority",
            "queued_at",
            postgresql_where=text("state = 'QUEUED'"),
        ),
        Index(
            "ix_tasks_project_finished",
            "project_id",
            "finished_at",
            postgresql_where=text("finished_at IS NOT NULL"),
        ),
        Index(
            "uq_tasks_project_idempotency_key",
            "project_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_tasks_schedule_fire_time",
            "schedule_id",
            "schedule_fire_time",
            unique=True,
            postgresql_where=text("schedule_id IS NOT NULL"),
        ),
        Index(
            "uq_tasks_workflow_node_run_id",
            "workflow_node_run_id",
            unique=True,
            postgresql_where=text("workflow_node_run_id IS NOT NULL"),
        ),
        Index("ix_tasks_workflow_run_id", "workflow_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(100))
    queue: Mapped[str] = mapped_column(String(63))
    priority: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]]
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", server_default=_EMPTY_JSON, default=dict
    )
    result: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    progress: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"), default=0)
    progress_message: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    max_retries: Mapped[int] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    backoff_base_seconds: Mapped[float] = mapped_column(Float)
    backoff_max_seconds: Mapped[float] = mapped_column(Float)
    dead_letter: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    available_at: Mapped[datetime | None]
    lease_owner: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    lease_token: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    #: Worker of the most recent attempt (kept after the lease is released, for display and analytics).
    last_worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    queued_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]
    dead_lettered_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    workflow_node_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_node_runs.id", ondelete="SET NULL")
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL")
    )
    schedule_fire_time: Mapped[datetime | None]
    traceparent: Mapped[str | None] = mapped_column(String(64))


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_number"),
        CheckConstraint(_in("status", AttemptStatus), name="status_valid"),
        Index("ix_task_attempts_worker_started", "worker_id", "started_at"),
        Index("ix_task_attempts_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    lease_token: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    queue_wait_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)


class TaskEvent(Base):
    """Append-only timeline of a task (state changes, retries, cancellations, fencing decisions)."""

    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_id_id", "task_id", "id"),
        Index("ix_task_events_project_created", "project_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str | None] = mapped_column(String(20))
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TaskLog(Base):
    __tablename__ = "task_logs"
    __table_args__ = (Index("ix_task_logs_task_id_id", "task_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    logged_at: Mapped[datetime]


# ============================================================================== workers & schedules
class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        CheckConstraint(_in("status", WorkerStatus), name="status_valid"),
        Index("ix_workers_status_heartbeat", "status", "last_heartbeat_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    hostname: Mapped[str] = mapped_column(String(255))
    pid: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(32))
    queues: Mapped[list[str]] = mapped_column(ARRAY(String(63)))
    task_types: Mapped[list[str]] = mapped_column(ARRAY(String(100)))
    concurrency: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    drain_requested_at: Mapped[datetime | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_heartbeat_at: Mapped[datetime] = mapped_column(server_default=func.now())
    stopped_at: Mapped[datetime | None]
    active_tasks: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    tasks_succeeded: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0)
    tasks_failed: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_rss_bytes: Mapped[int | None] = mapped_column(BigInteger)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", server_default=_EMPTY_JSON, default=dict
    )


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        CheckConstraint(_in("status", ScheduleStatus), name="status_valid"),
        CheckConstraint(_in("misfire_policy", MisfirePolicy), name="misfire_policy_valid"),
        CheckConstraint(_in("overlap_policy", OverlapPolicy), name="overlap_policy_valid"),
        CheckConstraint("priority BETWEEN 0 AND 9", name="priority_range"),
        Index("ix_schedules_due", "next_run_at", postgresql_where=text("status = 'ACTIVE'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    cron_expression: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]]
    queue: Mapped[str] = mapped_column(String(63))
    priority: Mapped[int] = mapped_column(SmallInteger)
    max_retries: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))
    misfire_policy: Mapped[str] = mapped_column(String(10))
    overlap_policy: Mapped[str] = mapped_column(String(10))
    next_run_at: Mapped[datetime | None]
    last_run_at: Mapped[datetime | None]
    last_task_id: Mapped[uuid.UUID | None]
    total_runs: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    skipped_runs: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


# ============================================================================== workflows
class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    latest_version: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    archived_at: Mapped[datetime | None]
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WorkflowVersion(Base):
    """Immutable snapshot of a workflow graph. Runs reference a version, so edits never affect running runs."""

    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    layout: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WorkflowNode(Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (UniqueConstraint("workflow_version_id", "node_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE")
    )
    node_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)
    queue: Mapped[str | None] = mapped_column(String(63))
    priority: Mapped[int | None] = mapped_column(SmallInteger)
    max_retries: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    position_x: Mapped[float] = mapped_column(Float, server_default=text("0"), default=0.0)
    position_y: Mapped[float] = mapped_column(Float, server_default=text("0"), default=0.0)


class WorkflowEdge(Base):
    __tablename__ = "workflow_edges"
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "source_node_id", "target_node_id"),
        CheckConstraint("source_node_id <> target_node_id", name="no_self_loop"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE")
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE")
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE")
    )


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(_in("state", WorkflowRunState), name="state_valid"),
        Index("ix_workflow_runs_project_created", "project_id", text("created_at DESC")),
        Index("ix_workflow_runs_workflow_created", "workflow_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(20))
    input: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )


class WorkflowNodeRun(TimestampMixin, Base):
    __tablename__ = "workflow_node_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "workflow_node_id"),
        CheckConstraint(_in("state", NodeRunState), name="state_valid"),
        CheckConstraint("remaining_deps >= 0", name="remaining_deps_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE")
    )
    workflow_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_nodes.id", ondelete="CASCADE")
    )
    node_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL", use_alter=True)
    )
    remaining_deps: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


# ============================================================================== infrastructure tables
class OutboxEvent(Base):
    """Transactional outbox: written in the same transaction as the state change it announces."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_unpublished", "id", postgresql_where=text("published_at IS NULL")),
        Index("ix_outbox_events_published_at", "published_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[uuid.UUID]
    stream: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None]
    publish_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_created", "organization_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(server_default=_EMPTY_JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NotificationRead(Base):
    """Per-user, per-project read marker for the notification feed."""

    __tablename__ = "notification_reads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    last_read_at: Mapped[datetime]
