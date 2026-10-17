"""Project-scoped API keys: create (secret shown once), rotate (old key gets a grace period), revoke, authenticate."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.context import Actor
from flowforge_core.db.models import ApiKey, Project
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, NotFound, ValidationFailed
from flowforge_core.security import api_key_hash, generate_api_key, looks_like_api_key
from flowforge_core.services import audit
from flowforge_shared.rbac import validate_api_key_scopes

LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class CreatedKey:
    key: ApiKey
    raw: str  # returned to the client exactly once, never stored


async def create_api_key(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
    actor: Actor,
    rotated_from_id: uuid.UUID | None = None,
) -> CreatedKey:
    try:
        validated = validate_api_key_scopes(scopes)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from None
    if expires_at is not None and expires_at <= datetime.now(UTC):
        raise ValidationFailed("expires_at must be in the future")
    generated = generate_api_key()
    key = ApiKey(
        organization_id=organization_id,
        project_id=project_id,
        name=name.strip(),
        prefix=generated.prefix,
        key_hash=generated.key_hash,
        scopes=[s.value for s in validated],
        created_by_user_id=actor.user_id,
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
    )
    uow.session.add(key)
    await uow.session.flush()
    await audit.record(
        uow.session,
        actor=actor,
        action="api_key.rotate" if rotated_from_id else "api_key.create",
        resource_type="api_key",
        resource_id=key.id,
        organization_id=organization_id,
        project_id=project_id,
        details={"name": key.name, "prefix": key.prefix, "scopes": key.scopes},
    )
    return CreatedKey(key, generated.raw)


async def list_api_keys(session: AsyncSession, project_id: uuid.UUID) -> list[ApiKey]:
    return list(
        (
            await session.scalars(
                select(ApiKey)
                .where(ApiKey.project_id == project_id)
                .order_by(ApiKey.created_at.desc())
            )
        ).all()
    )


async def _get_key(session: AsyncSession, project_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id).with_for_update()
    )
    if key is None:
        raise NotFound("API key not found")
    return key


async def rotate_api_key(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    grace_seconds: int,
    actor: Actor,
) -> CreatedKey:
    """Issue a replacement with the same name and scopes; the old key keeps working for ``grace_seconds`` so
    deployments can switch over without downtime (0 = revoke immediately)."""
    old = await _get_key(uow.session, project_id, key_id)
    now = datetime.now(UTC)
    if old.revoked_at is not None or (old.expires_at is not None and old.expires_at <= now):
        raise Conflict("cannot rotate a revoked or expired key")
    created = await create_api_key(
        uow,
        organization_id=organization_id,
        project_id=project_id,
        name=old.name,
        scopes=list(old.scopes),
        expires_at=old.expires_at,
        actor=actor,
        rotated_from_id=old.id,
    )
    if grace_seconds <= 0:
        old.revoked_at = now
    else:
        cutoff = now + timedelta(seconds=grace_seconds)
        old.expires_at = min(old.expires_at, cutoff) if old.expires_at else cutoff
    await uow.session.flush()
    return created


async def revoke_api_key(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    actor: Actor,
) -> ApiKey:
    key = await _get_key(uow.session, project_id, key_id)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await uow.session.flush()
        await audit.record(
            uow.session,
            actor=actor,
            action="api_key.revoke",
            resource_type="api_key",
            resource_id=key.id,
            organization_id=organization_id,
            project_id=project_id,
            details={"prefix": key.prefix},
        )
    return key


@dataclass(frozen=True, slots=True)
class AuthenticatedKey:
    key: ApiKey
    project: Project


async def authenticate_api_key(session: AsyncSession, raw: str) -> AuthenticatedKey | None:
    if not looks_like_api_key(raw):
        return None
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(ApiKey, Project)
            .join(Project, Project.id == ApiKey.project_id)
            .where(
                ApiKey.key_hash == api_key_hash(raw),
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    key, project = row[0], row[1]
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_WRITE_INTERVAL:
        await session.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await session.commit()
    return AuthenticatedKey(key, project)
