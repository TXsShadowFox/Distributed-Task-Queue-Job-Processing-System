from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select

from flowforge_api.deps import OrgContext, StateDep, UserDep, org_context
from flowforge_api.schemas.auth import ProjectOut
from flowforge_api.schemas.common import error_responses
from flowforge_api.schemas.platform import (
    AuditLogListOut,
    AuditLogOut,
    MemberAddIn,
    MemberOut,
    MemberUpdateIn,
    OrganizationOut,
    OrganizationUpdateIn,
    ProjectAccessOut,
    ProjectCreateIn,
    ProjectUpdateIn,
    RoleMatrixOut,
)
from flowforge_core.context import Actor
from flowforge_core.db.models import AuditLog, Organization, OrganizationMember, Project, User
from flowforge_core.errors import NotFound, PermissionDenied
from flowforge_core.services import identity
from flowforge_shared.rbac import API_KEY_SCOPES, ROLE_PERMISSIONS, Permission, Role

router = APIRouter(prefix="/organizations", tags=["organizations"])
projects_router = APIRouter(prefix="/projects", tags=["organizations"])

OrgMember = Annotated[OrgContext, Depends(org_context())]
OrgManage = Annotated[OrgContext, Depends(org_context(Permission.ORG_MANAGE))]
MembersManage = Annotated[OrgContext, Depends(org_context(Permission.MEMBERS_MANAGE))]
ProjectsManage = Annotated[OrgContext, Depends(org_context(Permission.PROJECTS_MANAGE))]
AuditRead = Annotated[OrgContext, Depends(org_context(Permission.AUDIT_READ))]


@router.get(
    "/roles", response_model=RoleMatrixOut, summary="Role → permission matrix and API-key scopes"
)
async def role_matrix(_: UserDep) -> RoleMatrixOut:
    return RoleMatrixOut(
        roles={r.value: sorted(p.value for p in ROLE_PERMISSIONS[r]) for r in Role},
        api_key_scopes=sorted(s.value for s in API_KEY_SCOPES),
    )


@router.get("/{org_id}", response_model=OrganizationOut, responses=error_responses(401, 404))
async def get_organization(org_id: uuid.UUID, ctx: OrgMember, state: StateDep) -> OrganizationOut:
    async with state.db.session() as session:
        org = await session.get(Organization, ctx.organization_id)
    if org is None:
        raise NotFound("organization not found")
    return OrganizationOut.model_validate(org)


@router.patch("/{org_id}", response_model=OrganizationOut, responses=error_responses(401, 403, 404))
async def update_organization(
    org_id: uuid.UUID, body: OrganizationUpdateIn, ctx: OrgManage, state: StateDep
) -> OrganizationOut:
    async with state.db.transaction() as uow:
        org = await uow.session.get(Organization, ctx.organization_id, with_for_update=True)
        if org is None:
            raise NotFound("organization not found")
        org = await identity.update_organization(
            uow, organization=org, name=body.name, actor=ctx.actor
        )
    return OrganizationOut.model_validate(org)


def _member_out(member: OrganizationMember, user: User) -> MemberOut:
    return MemberOut(
        id=member.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=Role(member.role).value,
        created_at=member.created_at,
    )


@router.get(
    "/{org_id}/members", response_model=list[MemberOut], responses=error_responses(401, 404)
)
async def list_members(org_id: uuid.UUID, ctx: OrgMember, state: StateDep) -> list[MemberOut]:
    async with state.db.session() as session:
        rows = await identity.list_members(session, ctx.organization_id)
    return [_member_out(m, u) for m, u in rows]


@router.post(
    "/{org_id}/members",
    status_code=201,
    response_model=MemberOut,
    responses=error_responses(401, 403, 404, 409),
)
async def add_member(
    org_id: uuid.UUID, body: MemberAddIn, ctx: MembersManage, state: StateDep
) -> MemberOut:
    async with state.db.transaction() as uow:
        member, user = await identity.add_member(
            uow,
            organization_id=ctx.organization_id,
            email=str(body.email),
            role=Role(body.role),
            actor_role=ctx.role,
            actor=ctx.actor,
        )
    return _member_out(member, user)


@router.patch(
    "/{org_id}/members/{member_id}",
    response_model=MemberOut,
    responses=error_responses(401, 403, 404, 409),
)
async def update_member(
    org_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdateIn,
    ctx: MembersManage,
    state: StateDep,
) -> MemberOut:
    async with state.db.transaction() as uow:
        member = await identity.change_member_role(
            uow,
            organization_id=ctx.organization_id,
            member_id=member_id,
            new_role=Role(body.role),
            actor_role=ctx.role,
            actor=ctx.actor,
        )
        rows = await identity.list_members(uow.session, ctx.organization_id)
    user = next(u for m, u in rows if m.id == member.id)
    return _member_out(member, user)


@router.delete(
    "/{org_id}/members/{member_id}", status_code=204, responses=error_responses(401, 403, 404, 409)
)
async def remove_member(
    org_id: uuid.UUID, member_id: uuid.UUID, ctx: MembersManage, state: StateDep
) -> Response:
    async with state.db.transaction() as uow:
        await identity.remove_member(
            uow,
            organization_id=ctx.organization_id,
            member_id=member_id,
            actor_role=ctx.role,
            actor=ctx.actor,
        )
    return Response(status_code=204)


@router.post(
    "/{org_id}/projects",
    status_code=201,
    response_model=ProjectOut,
    responses=error_responses(401, 403, 404),
)
async def create_project(
    org_id: uuid.UUID, body: ProjectCreateIn, ctx: ProjectsManage, state: StateDep
) -> ProjectOut:
    async with state.db.transaction() as uow:
        project = await identity.create_project(
            uow,
            organization_id=ctx.organization_id,
            name=body.name,
            description=body.description,
            actor=ctx.actor,
        )
    return ProjectOut.model_validate(project)


@router.get(
    "/{org_id}/audit-logs", response_model=AuditLogListOut, responses=error_responses(401, 403, 404)
)
async def list_audit_logs(
    org_id: uuid.UUID,
    ctx: AuditRead,
    state: StateDep,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditLogListOut:
    stmt = select(AuditLog).where(AuditLog.organization_id == ctx.organization_id)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    async with state.db.session() as session:
        rows = list(
            (await session.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).all()
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return AuditLogListOut(
        items=[AuditLogOut.model_validate(r) for r in rows],
        next_before_id=rows[-1].id if has_more and rows else None,
    )


# ------------------------------------------------------------------------------------------------ projects
@projects_router.get(
    "", response_model=list[ProjectAccessOut], summary="Projects visible to the current user"
)
async def list_projects(user: UserDep, state: StateDep) -> list[ProjectAccessOut]:
    async with state.db.session() as session:
        accesses = await identity.user_projects(session, user.id)
    return [
        ProjectAccessOut(
            id=a.project.id,
            name=a.project.name,
            slug=a.project.slug,
            description=a.project.description,
            settings=a.project.settings,
            organization_id=a.organization.id,
            organization_name=a.organization.name,
            role=a.role.value,
            created_at=a.project.created_at,
        )
        for a in accesses
    ]


@projects_router.patch(
    "/{project_id}", response_model=ProjectOut, responses=error_responses(401, 403, 404)
)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdateIn, user: UserDep, state: StateDep
) -> ProjectOut:
    async with state.db.transaction() as uow:
        access = await identity.project_access(uow.session, user.id, project_id)
        if Permission.PROJECTS_MANAGE not in ROLE_PERMISSIONS[access.role]:
            raise PermissionDenied("missing permission 'projects:manage'")
        project = await uow.session.get(Project, project_id, with_for_update=True)
        if project is None:
            raise NotFound("project not found")
        project = await identity.update_project(
            uow,
            project=project,
            name=body.name,
            description=body.description,
            settings_patch=body.settings.model_dump(exclude_unset=True) if body.settings else None,
            actor=Actor.for_user(user.id, user.email),
        )
    return ProjectOut.model_validate(project)
