from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.context import Actor, request_info
from flowforge_core.db.models import AuditLog
from flowforge_core.logs import redact_mapping


async def record(
    session: AsyncSession,
    *,
    actor: Actor,
    action: str,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry in the caller's transaction (so it commits or rolls back with the action)."""
    req = request_info()
    session.add(
        AuditLog(
            organization_id=organization_id,
            project_id=project_id,
            actor_type=actor.kind,
            actor_id=actor.id,
            actor_label=actor.label,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip=req.ip,
            user_agent=(req.user_agent or "")[:255] or None,
            request_id=req.request_id,
            details=redact_mapping(details or {}),
        )
    )
