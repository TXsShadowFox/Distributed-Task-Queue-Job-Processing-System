"""Schemas for organizations, members, projects, audit logs, API keys, workers and notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from flowforge_api.schemas.common import APIModel, StrictInput
from flowforge_api.schemas.tasks import TaskSummaryOut
from flowforge_core.db.models import ApiKey, Worker
from flowforge_shared.rbac import API_KEY_SCOPES

RoleName = Literal["owner", "admin", "developer", "viewer"]


# ------------------------------------------------------------------------------------------------ organizations
class OrganizationOut(APIModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrganizationUpdateIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: RoleName
    created_at: datetime


class MemberAddIn(StrictInput):
    email: EmailStr
    role: RoleName = "developer"


class MemberUpdateIn(StrictInput):
    role: RoleName


class ProjectSettingsIn(StrictInput):
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class ProjectCreateIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class ProjectUpdateIn(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    settings: ProjectSettingsIn | None = None


class ProjectAccessOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    settings: dict[str, Any]
    organization_id: uuid.UUID
    organization_name: str
    role: RoleName
    created_at: datetime


class AuditLogOut(APIModel):
    id: int
    project_id: uuid.UUID | None
    actor_type: str
    actor_id: str | None
    actor_label: str | None
    action: str
    resource_type: str
    resource_id: str | None
    ip: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime


class AuditLogListOut(BaseModel):
    items: list[AuditLogOut]
    next_before_id: int | None


class RoleMatrixOut(BaseModel):
    roles: dict[str, list[str]]
    api_key_scopes: list[str]


# ------------------------------------------------------------------------------------------------ API keys
class ApiKeyCreateIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=len(API_KEY_SCOPES))
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyRotateIn(StrictInput):
    grace_period_seconds: int = Field(default=86_400, ge=0, le=30 * 86_400)


ApiKeyStatus = Literal["active", "expiring", "expired", "revoked"]


class ApiKeyOut(APIModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    rotated_from_id: uuid.UUID | None
    status: ApiKeyStatus

    @classmethod
    def build(cls, key: ApiKey) -> ApiKeyOut:
        now = datetime.now(UTC)
        status: ApiKeyStatus
        if key.revoked_at is not None:
            status = "revoked"
        elif key.expires_at is not None and key.expires_at <= now:
            status = "expired"
        elif key.expires_at is not None and (key.expires_at - now).total_seconds() < 7 * 86_400:
            status = "expiring"
        else:
            status = "active"
        return cls(
            id=key.id,
            name=key.name,
            prefix=key.prefix,
            scopes=list(key.scopes),
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            expires_at=key.expires_at,
            revoked_at=key.revoked_at,
            rotated_from_id=key.rotated_from_id,
            status=status,
        )


class ApiKeySecretOut(BaseModel):
    key: ApiKeyOut
    secret: str = Field(
        description="The full API key. It is shown only once and cannot be retrieved later."
    )


# ------------------------------------------------------------------------------------------------ workers
class WorkerOut(APIModel):
    id: uuid.UUID
    name: str
    hostname: str
    pid: int
    version: str
    queues: list[str]
    task_types: list[str]
    concurrency: int
    status: str
    active_tasks: int
    tasks_succeeded: int
    tasks_failed: int
    cpu_percent: float | None
    memory_rss_bytes: int | None
    started_at: datetime
    last_heartbeat_at: datetime
    heartbeat_age_seconds: float
    drain_requested_at: datetime | None
    stopped_at: datetime | None

    @classmethod
    def build(cls, worker: Worker) -> WorkerOut:
        age = (datetime.now(UTC) - worker.last_heartbeat_at).total_seconds()
        return cls(
            id=worker.id,
            name=worker.name,
            hostname=worker.hostname,
            pid=worker.pid,
            version=worker.version,
            queues=list(worker.queues),
            task_types=list(worker.task_types),
            concurrency=worker.concurrency,
            status=worker.status,
            active_tasks=worker.active_tasks,
            tasks_succeeded=worker.tasks_succeeded,
            tasks_failed=worker.tasks_failed,
            cpu_percent=worker.cpu_percent,
            memory_rss_bytes=worker.memory_rss_bytes,
            started_at=worker.started_at,
            last_heartbeat_at=worker.last_heartbeat_at,
            heartbeat_age_seconds=round(max(age, 0.0), 1),
            drain_requested_at=worker.drain_requested_at,
            stopped_at=worker.stopped_at,
        )


class WorkerDetailOut(WorkerOut):
    project_tasks: list[TaskSummaryOut] = Field(
        default_factory=list,
        description="Tasks of the selected project currently leased by this worker",
    )


# ------------------------------------------------------------------------------------------------ notifications
class NotificationOut(BaseModel):
    id: str
    category: str
    severity: Literal["info", "warning", "error"]
    title: str
    detail: str
    link: str
    ts: datetime
    unread: bool


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int
