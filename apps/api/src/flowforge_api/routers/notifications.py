from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from flowforge_api.deps import ProjectContext, StateDep, project_context
from flowforge_api.schemas.common import OkOut
from flowforge_api.schemas.platform import NotificationListOut, NotificationOut
from flowforge_core.services import notifications
from flowforge_shared.rbac import Permission

router = APIRouter(prefix="/notifications", tags=["organizations"])

ProjectRead = Annotated[ProjectContext, Depends(project_context(Permission.TASKS_READ))]


@router.get(
    "", response_model=NotificationListOut, summary="Recent notable events for the selected project"
)
async def list_notifications(ctx: ProjectRead, state: StateDep) -> NotificationListOut:
    user = ctx.principal.require_user()
    async with state.db.session() as session:
        data = await notifications.feed(
            session, user_id=user.id, project_id=ctx.project_id, preferences=user.preferences
        )
    return NotificationListOut(
        items=[NotificationOut(**i) for i in data["items"]], unread_count=data["unread_count"]
    )


@router.post("/read", response_model=OkOut, summary="Mark all notifications of the project as read")
async def mark_read(ctx: ProjectRead, state: StateDep) -> OkOut:
    user = ctx.principal.require_user()
    async with state.db.transaction() as uow:
        await notifications.mark_all_read(uow, user_id=user.id, project_id=ctx.project_id)
    return OkOut()
