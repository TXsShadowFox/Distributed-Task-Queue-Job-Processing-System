from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from flowforge_api.schemas.common import APIModel, StrictInput


class RegisterIn(StrictInput):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    organization_name: str | None = Field(default=None, min_length=1, max_length=120)


class LoginIn(StrictInput):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserOut(APIModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    preferences: dict[str, Any]
    created_at: datetime
    last_login_at: datetime | None


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_at: datetime
    user: UserOut


class ProjectOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    settings: dict[str, Any]
    created_at: datetime


class MembershipOut(BaseModel):
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    role: str
    projects: list[ProjectOut]


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


class NotificationPrefs(StrictInput):
    dead_letter: bool = True
    workflow_failed: bool = True
    worker_offline: bool = True


class PreferencesIn(StrictInput):
    theme: Literal["dark", "light", "system"] | None = None
    default_project_id: uuid.UUID | None = None
    notifications: NotificationPrefs | None = None
    sidebar_collapsed: bool | None = None


class UpdateMeIn(StrictInput):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    preferences: PreferencesIn | None = None


class ChangePasswordIn(StrictInput):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
