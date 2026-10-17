from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from flowforge_api.schemas.common import APIModel, StrictInput
from flowforge_shared.priority import QUEUE_NAME_PATTERN

Misfire = Literal["RUN_ONCE", "SKIP"]
Overlap = Literal["ALLOW", "SKIP"]


class ScheduleIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    cron_expression: str = Field(min_length=1, max_length=120, examples=["0 9 * * 1-5"])
    timezone: str = Field(default="UTC", min_length=1, max_length=64, examples=["Asia/Kolkata"])
    task_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    queue: str | None = Field(default=None, pattern=QUEUE_NAME_PATTERN)
    priority: int | None = Field(default=None, ge=0, le=9)
    max_retries: int | None = Field(default=None, ge=0, le=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    misfire_policy: Misfire = "RUN_ONCE"
    overlap_policy: Overlap = "ALLOW"
    paused: bool = False


class SchedulePatch(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    task_type: str | None = Field(default=None, min_length=1, max_length=100)
    payload: dict[str, Any] | None = None
    queue: str | None = Field(default=None, pattern=QUEUE_NAME_PATTERN)
    priority: int | None = Field(default=None, ge=0, le=9)
    max_retries: int | None = Field(default=None, ge=0, le=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    misfire_policy: Misfire | None = None
    overlap_policy: Overlap | None = None
    paused: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)


class ScheduleOut(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    cron_expression: str
    cron_description: str
    timezone: str
    task_type: str
    payload: dict[str, Any]
    queue: str
    priority: int
    max_retries: int
    timeout_seconds: int
    status: str
    misfire_policy: str
    overlap_policy: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_task_id: uuid.UUID | None
    total_runs: int
    skipped_runs: int
    version: int
    created_at: datetime
    updated_at: datetime


class PreviewIn(StrictInput):
    cron_expression: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    count: int = Field(default=5, ge=1, le=50)


class FireTimeOut(BaseModel):
    utc: datetime
    local: str
    utc_offset: str


class PreviewOut(BaseModel):
    cron_expression: str
    description: str
    timezone: str
    next_runs: list[FireTimeOut]
    dst_note: str | None
