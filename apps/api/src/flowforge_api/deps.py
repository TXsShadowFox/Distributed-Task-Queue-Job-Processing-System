"""Authentication, tenant resolution and permission checks.

* Users authenticate with a short-lived JWT access token (``Authorization: Bearer``) and pick a project with the
  ``X-Project-ID`` header; their permissions come from their organization role.
* API keys (``Authorization: Bearer ffk_…`` or ``X-API-Key``) are bound to exactly one project; their permissions are
  their scopes.
* Resources outside the caller's tenant are reported as 404, never 403, so their existence is not revealed.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Header, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from flowforge_api.middleware import client_ip
from flowforge_api.state import AppState, get_state
from flowforge_core.context import Actor
from flowforge_core.db.models import ApiKey, Project, User
from flowforge_core.errors import AuthenticationFailed, PermissionDenied, ValidationFailed
from flowforge_core.logs import bind_context
from flowforge_core.observability import metrics
from flowforge_core.security import decode_access_token, password_version
from flowforge_core.services import api_keys, identity
from flowforge_shared.rbac import ROLE_PERMISSIONS, Permission, Role

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token, or an API key (ffk_…)")
api_key_scheme = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Project-scoped API key"
)

StateDep = Annotated[AppState, Depends(get_state)]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "api_key"]
    actor: Actor
    user: User | None = None
    api_key: ApiKey | None = None
    api_key_project: Project | None = None
    token_expires_at: datetime | None = None

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.actor.id}"

    def require_user(self) -> User:
        if self.user is None:
            raise PermissionDenied("this operation requires a user session, not an API key")
        return self.user


async def get_principal(
    request: Request,
    state: StateDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_api_key: Annotated[str | None, Depends(api_key_scheme)],
) -> Principal:
    raw = x_api_key or (credentials.credentials if credentials else None)
    if not raw:
        raise AuthenticationFailed("authentication required")
    if raw.startswith("ffk_"):
        async with state.db.session() as session:
            auth = await api_keys.authenticate_api_key(session, raw)
        if auth is None:
            metrics.AUTH_FAILURES.labels("api_key").inc()
            raise AuthenticationFailed("invalid, expired or revoked API key")
        principal = Principal(
            kind="api_key",
            actor=Actor.for_api_key(auth.key.id, auth.key.prefix),
            api_key=auth.key,
            api_key_project=auth.project,
        )
    else:
        try:
            claims = decode_access_token(raw, state.settings)
        except AuthenticationFailed:
            metrics.AUTH_FAILURES.labels("jwt").inc()
            raise
        async with state.db.session() as session:
            user = await session.get(User, claims.user_id)
        if user is None or not user.is_active:
            raise AuthenticationFailed("account not found or disabled")
        if claims.password_version != password_version(user.password_changed_at):
            raise AuthenticationFailed("session ended because the password changed; sign in again")
        principal = Principal(
            kind="user",
            actor=Actor.for_user(user.id, user.email),
            user=user,
            token_expires_at=claims.expires_at,
        )
    await state.limiter.enforce("api", principal.identity, state.settings.rate_limit_per_minute)
    bind_context(actor=principal.actor.kind, actor_id=principal.actor.id)
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_user(principal: PrincipalDep) -> User:
    return principal.require_user()


UserDep = Annotated[User, Depends(get_user)]


async def require_platform_admin(principal: PrincipalDep) -> Principal:
    user = principal.require_user()
    if not user.is_platform_admin:
        raise PermissionDenied("platform administrator role required")
    return principal


@dataclass(frozen=True, slots=True)
class ProjectContext:
    principal: Principal
    project: Project
    organization_id: uuid.UUID
    role: Role | None
    permissions: frozenset[Permission]

    @property
    def actor(self) -> Actor:
        return self.principal.actor

    @property
    def project_id(self) -> uuid.UUID:
        return self.project.id

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if permission not in self.permissions:
            raise PermissionDenied(f"missing permission '{permission.value}'")


def project_context(permission: Permission) -> Callable[..., Awaitable[ProjectContext]]:
    async def dependency(
        request: Request,
        principal: PrincipalDep,
        state: StateDep,
        x_project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
    ) -> ProjectContext:
        requested = x_project_id or request.query_params.get("project_id")
        if principal.kind == "api_key":
            if principal.api_key is None or principal.api_key_project is None:
                raise AuthenticationFailed("invalid API key principal")
            project = principal.api_key_project
            if requested and requested != str(project.id):
                raise PermissionDenied("this API key is scoped to a different project")
            ctx = ProjectContext(
                principal=principal,
                project=project,
                organization_id=project.organization_id,
                role=None,
                permissions=frozenset(Permission(s) for s in principal.api_key.scopes),
            )
        else:
            if not requested:
                raise ValidationFailed("the X-Project-ID header is required")
            try:
                project_id = uuid.UUID(requested)
            except ValueError:
                raise ValidationFailed("X-Project-ID must be a UUID") from None
            user = principal.require_user()
            async with state.db.session() as session:
                access = await identity.project_access(session, user.id, project_id)
            ctx = ProjectContext(
                principal=principal,
                project=access.project,
                organization_id=access.organization.id,
                role=access.role,
                permissions=ROLE_PERMISSIONS[access.role],
            )
        ctx.require(permission)
        bind_context(project_id=str(ctx.project.id))
        return ctx

    return dependency


@dataclass(frozen=True, slots=True)
class OrgContext:
    principal: Principal
    user: User
    organization_id: uuid.UUID
    role: Role

    @property
    def actor(self) -> Actor:
        return self.principal.actor

    def require(self, permission: Permission) -> None:
        if permission not in ROLE_PERMISSIONS[self.role]:
            raise PermissionDenied(f"missing permission '{permission.value}'")


def org_context(permission: Permission | None = None) -> Callable[..., Awaitable[OrgContext]]:
    async def dependency(org_id: uuid.UUID, principal: PrincipalDep, state: StateDep) -> OrgContext:
        user = principal.require_user()
        async with state.db.session() as session:
            role = await identity.organization_role(session, user.id, org_id)
        ctx = OrgContext(principal, user, org_id, role)
        if permission is not None:
            ctx.require(permission)
        return ctx

    return dependency


async def auth_rate_limit(request: Request, state: StateDep) -> None:
    """Per-IP limit for unauthenticated auth endpoints (login, register, refresh)."""
    ip = client_ip(request.scope, state.settings.trusted_proxy_count) or "unknown"
    await state.limiter.enforce("auth", ip, state.settings.auth_rate_limit_per_minute)
