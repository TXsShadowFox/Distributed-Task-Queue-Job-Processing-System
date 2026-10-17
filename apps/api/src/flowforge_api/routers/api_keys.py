from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.platform import (
    ApiKeyCreateIn,
    ApiKeyOut,
    ApiKeyRotateIn,
    ApiKeySecretOut,
)
from flowforge_core.services import api_keys as key_service
from flowforge_shared.rbac import Permission

router = APIRouter(prefix="/api-keys", tags=["api keys"])

KeysManage = Annotated[ProjectContext, Depends(project_context(Permission.API_KEYS_MANAGE))]


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(ctx: KeysManage, state: StateDep) -> list[ApiKeyOut]:
    async with state.db.session() as session:
        keys = await key_service.list_api_keys(session, ctx.project_id)
    return [ApiKeyOut.build(k) for k in keys]


@router.post(
    "",
    status_code=201,
    response_model=ApiKeySecretOut,
    responses=error_responses(401, 403, 422),
    summary="Create a scoped API key (the secret is returned only in this response)",
)
async def create_key(body: ApiKeyCreateIn, ctx: KeysManage, state: StateDep) -> ApiKeySecretOut:
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    )
    async with state.db.transaction() as uow:
        created = await key_service.create_api_key(
            uow,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            name=body.name,
            scopes=body.scopes,
            expires_at=expires_at,
            actor=ctx.actor,
        )
    return ApiKeySecretOut(key=ApiKeyOut.build(created.key), secret=created.raw)


@router.post(
    "/{key_id}/rotate",
    status_code=201,
    response_model=ApiKeySecretOut,
    responses=error_responses(401, 403, 404, 409),
    summary="Issue a replacement key; the old key stays valid for the grace period",
)
async def rotate_key(
    key_id: uuid.UUID, ctx: KeysManage, state: StateDep, body: ApiKeyRotateIn | None = None
) -> ApiKeySecretOut:
    grace = (body or ApiKeyRotateIn()).grace_period_seconds
    async with state.db.transaction() as uow:
        created = await key_service.rotate_api_key(
            uow,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            key_id=key_id,
            grace_seconds=grace,
            actor=ctx.actor,
        )
    return ApiKeySecretOut(key=ApiKeyOut.build(created.key), secret=created.raw)


@router.delete("/{key_id}", response_model=ApiKeyOut, responses=error_responses(401, 403, 404))
async def revoke_key(key_id: uuid.UUID, ctx: KeysManage, state: StateDep) -> ApiKeyOut:
    async with state.db.transaction() as uow:
        key = await key_service.revoke_api_key(
            uow,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            key_id=key_id,
            actor=ctx.actor,
        )
    return ApiKeyOut.build(key)
