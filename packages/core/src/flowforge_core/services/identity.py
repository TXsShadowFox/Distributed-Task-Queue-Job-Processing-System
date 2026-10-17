"""Users, sessions (refresh tokens), organizations, memberships and projects."""

from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from flowforge_core.config import Settings
from flowforge_core.context import Actor
from flowforge_core.db.models import (
    Organization,
    OrganizationMember,
    Project,
    RefreshToken,
    User,
)
from flowforge_core.db.uow import UnitOfWork
from flowforge_core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from flowforge_core.security import (
    create_access_token,
    hash_password,
    new_refresh_token,
    password_needs_rehash,
    sha256_hex,
    validate_password_policy,
    verify_password,
)
from flowforge_core.services import audit
from flowforge_shared.rbac import Role, can_grant_role, can_manage_member

REFRESH_REUSE_GRACE_SECONDS = 10
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def slugify(value: str, fallback: str = "org") -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")[:48]
    return slug or fallback


# ------------------------------------------------------------------------------------------------ registration
@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    organization: Organization
    project: Project


async def register_user(
    uow: UnitOfWork,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str | None,
    settings: Settings,
) -> Registration:
    session = uow.session
    email = normalize_email(email)
    validate_password_policy(password, email=email)
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("an account with this email already exists")
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(
        email=email,
        password_hash=password_hash,
        full_name=full_name.strip(),
        is_platform_admin=bool(
            settings.bootstrap_admin_email
            and email == normalize_email(settings.bootstrap_admin_email)
        ),
    )
    session.add(user)
    org_name = (organization_name or f"{full_name.strip()}'s organization").strip()[:120]
    org = Organization(name=org_name, slug=await _unique_org_slug(session, slugify(org_name)))
    session.add(org)
    await session.flush()
    session.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=Role.OWNER))
    project = Project(
        organization_id=org.id, name="Default", slug="default", description="Default project"
    )
    session.add(project)
    await session.flush()
    actor = Actor.for_user(user.id, email)
    await audit.record(
        session,
        actor=actor,
        action="user.register",
        resource_type="user",
        resource_id=user.id,
        organization_id=org.id,
        details={"platform_admin": user.is_platform_admin},
    )
    return Registration(user, org, project)


async def _unique_org_slug(session: AsyncSession, base: str) -> str:
    slug = base
    while (
        await session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None
    ):
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


# ------------------------------------------------------------------------------------------------ login
@dataclass(frozen=True, slots=True)
class LoginOutcome:
    user: User | None
    locked: bool = False


async def authenticate(
    uow: UnitOfWork, *, email: str, password: str, settings: Settings
) -> LoginOutcome:
    """Never raises for bad credentials: the failure counter must be committed, so the caller raises afterwards."""
    session = uow.session
    user = await session.scalar(
        select(User).where(User.email == normalize_email(email)).with_for_update()
    )
    now = datetime.now(UTC)
    if user is None:
        await asyncio.to_thread(
            verify_password, None, password
        )  # uniform timing for unknown emails
        return LoginOutcome(None)
    if user.locked_until is not None and user.locked_until > now:
        return LoginOutcome(None, locked=True)
    ok = await asyncio.to_thread(verify_password, user.password_hash, password)
    if not ok or not user.is_active:
        user.failed_login_attempts += 1
        locked = False
        if user.failed_login_attempts >= settings.login_max_failures:
            user.locked_until = now + timedelta(seconds=settings.login_lockout_seconds)
            user.failed_login_attempts = 0
            locked = True
            await audit.record(
                session,
                actor=Actor.for_user(user.id, user.email),
                action="user.locked",
                resource_type="user",
                resource_id=user.id,
            )
        return LoginOutcome(None, locked=locked)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
    await audit.record(
        session,
        actor=Actor.for_user(user.id, user.email),
        action="user.login",
        resource_type="user",
        resource_id=user.id,
    )
    return LoginOutcome(user)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    refresh_token_id: uuid.UUID


async def issue_session(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    user_agent: str | None,
    ip: str | None,
    family_id: uuid.UUID | None = None,
) -> IssuedSession:
    access, access_exp = create_access_token(
        user.id, settings, password_changed_at=user.password_changed_at
    )
    raw, token_hash = new_refresh_token()
    refresh_exp = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    row = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        family_id=family_id or uuid.uuid4(),
        expires_at=refresh_exp,
        user_agent=(user_agent or "")[:255] or None,
        ip=ip,
    )
    session.add(row)
    await session.flush()
    return IssuedSession(access, access_exp, raw, refresh_exp, row.id)


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    user: User | None
    issued: IssuedSession | None
    reuse_detected: bool = False


async def rotate_refresh_token(
    uow: UnitOfWork, *, raw_token: str, settings: Settings, user_agent: str | None, ip: str | None
) -> RefreshOutcome:
    """Rotate a refresh token. Presenting an already-rotated token (outside a short multi-tab grace window) is
    treated as token theft: the whole token family is revoked."""
    session = uow.session
    row = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(raw_token))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None or row.expires_at <= now:
        return RefreshOutcome(None, None)
    if row.revoked_at is not None:
        recently_rotated = (
            row.replaced_by_id is not None
            and (now - row.revoked_at).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS
        )
        if not recently_rotated:
            if row.replaced_by_id is not None:
                await session.execute(
                    update(RefreshToken)
                    .where(
                        RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None)
                    )
                    .values(revoked_at=func.now())
                )
                user = await session.get(User, row.user_id)
                await audit.record(
                    session,
                    actor=Actor.for_user(row.user_id, user.email if user else "?"),
                    action="session.refresh_reuse_detected",
                    resource_type="session",
                    resource_id=row.family_id,
                )
                return RefreshOutcome(None, None, reuse_detected=True)
            return RefreshOutcome(None, None)
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return RefreshOutcome(None, None)
    issued = await issue_session(
        session, user=user, settings=settings, user_agent=user_agent, ip=ip, family_id=row.family_id
    )
    if row.revoked_at is None:
        row.revoked_at = now
        row.replaced_by_id = issued.refresh_token_id
    return RefreshOutcome(user, issued)


async def revoke_session(uow: UnitOfWork, *, raw_token: str) -> None:
    row = await uow.session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw_token))
    )
    if row is not None:
        await uow.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )


async def change_password(
    uow: UnitOfWork, *, user: User, current_password: str, new_password: str
) -> None:
    if not await asyncio.to_thread(verify_password, user.password_hash, current_password):
        raise ValidationFailed("current password is incorrect")
    validate_password_policy(new_password, email=user.email)
    if await asyncio.to_thread(verify_password, user.password_hash, new_password):
        raise ValidationFailed("new password must differ from the current password")
    user.password_hash = await asyncio.to_thread(hash_password, new_password)
    user.password_changed_at = datetime.now(UTC)
    # Sign out every other session.
    await uow.session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await audit.record(
        uow.session,
        actor=Actor.for_user(user.id, user.email),
        action="user.password_change",
        resource_type="user",
        resource_id=user.id,
    )


# ------------------------------------------------------------------------------------------------ tenancy
@dataclass(frozen=True, slots=True)
class ProjectAccess:
    project: Project
    organization: Organization
    role: Role


async def project_access(
    session: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectAccess:
    """Resolve the caller's role for a project. Non-members get NotFound, never a hint that the project exists."""
    row = (
        await session.execute(
            select(Project, Organization, OrganizationMember.role)
            .join(Organization, Organization.id == Project.organization_id)
            .join(
                OrganizationMember,
                (OrganizationMember.organization_id == Organization.id)
                & (OrganizationMember.user_id == user_id),
            )
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("project not found")
    return ProjectAccess(row[0], row[1], Role(row[2]))


async def organization_role(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> Role:
    role = await session.scalar(
        select(OrganizationMember.role).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        )
    )
    if role is None:
        raise NotFound("organization not found")
    return Role(role)


async def user_projects(session: AsyncSession, user_id: uuid.UUID) -> list[ProjectAccess]:
    rows = (
        await session.execute(
            select(Project, Organization, OrganizationMember.role)
            .join(Organization, Organization.id == Project.organization_id)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.name, Project.name)
        )
    ).all()
    return [ProjectAccess(r[0], r[1], Role(r[2])) for r in rows]


async def create_project(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    name: str,
    description: str | None,
    actor: Actor,
) -> Project:
    session = uow.session
    base = slugify(name, "project")
    slug = base
    while await session.scalar(
        select(Project.id).where(Project.organization_id == organization_id, Project.slug == slug)
    ):
        slug = f"{base}-{secrets.token_hex(2)}"
    project = Project(
        organization_id=organization_id, name=name.strip(), slug=slug, description=description
    )
    session.add(project)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="project.create",
        resource_type="project",
        resource_id=project.id,
        organization_id=organization_id,
        project_id=project.id,
        details={"name": name},
    )
    return project


async def update_project(
    uow: UnitOfWork,
    *,
    project: Project,
    name: str | None,
    description: str | None,
    settings_patch: dict[str, object] | None,
    actor: Actor,
) -> Project:
    if name is not None:
        project.name = name.strip()
    if description is not None:
        project.description = description
    if settings_patch:
        # Keys sent as null are removed, so a setting can return to the platform default.
        merged = {**project.settings, **settings_patch}
        project.settings = {key: value for key, value in merged.items() if value is not None}
    await uow.session.flush()
    await uow.session.refresh(project)
    await audit.record(
        uow.session,
        actor=actor,
        action="project.update",
        resource_type="project",
        resource_id=project.id,
        organization_id=project.organization_id,
        project_id=project.id,
        details={"name": name, "settings": settings_patch or {}},
    )
    return project


async def update_organization(
    uow: UnitOfWork, *, organization: Organization, name: str, actor: Actor
) -> Organization:
    organization.name = name.strip()
    await uow.session.flush()
    await uow.session.refresh(organization)
    await audit.record(
        uow.session,
        actor=actor,
        action="organization.update",
        resource_type="organization",
        resource_id=organization.id,
        organization_id=organization.id,
        details={"name": name},
    )
    return organization


async def list_members(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[tuple[OrganizationMember, User]]:
    rows = (
        await session.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(OrganizationMember.created_at)
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


async def _owner_count(session: AsyncSession, organization_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == Role.OWNER,
            )
        )
        or 0
    )


async def add_member(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: Role,
    actor_role: Role,
    actor: Actor,
) -> tuple[OrganizationMember, User]:
    """Adds an existing FlowForge user (email invitations are out of scope; see docs/evaluation/known-issues.md)."""
    if not can_grant_role(actor_role, role):
        raise PermissionDenied(f"your role cannot grant {role.value}")
    session = uow.session
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise NotFound("no FlowForge user with this email; ask them to register first")
    existing = await session.scalar(
        select(OrganizationMember.id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user.id,
        )
    )
    if existing is not None:
        raise Conflict("user is already a member of this organization")
    member = OrganizationMember(organization_id=organization_id, user_id=user.id, role=role)
    session.add(member)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="member.add",
        resource_type="member",
        resource_id=member.id,
        organization_id=organization_id,
        details={"email": user.email, "role": role.value},
    )
    return member, user


async def change_member_role(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    member_id: uuid.UUID,
    new_role: Role,
    actor_role: Role,
    actor: Actor,
) -> OrganizationMember:
    session = uow.session
    member = await session.scalar(
        select(OrganizationMember)
        .where(
            OrganizationMember.id == member_id,
            OrganizationMember.organization_id == organization_id,
        )
        .with_for_update()
    )
    if member is None:
        raise NotFound("member not found")
    current = Role(member.role)
    if not can_manage_member(actor_role, current) or not can_grant_role(actor_role, new_role):
        raise PermissionDenied("insufficient role to make this change")
    if (
        current is Role.OWNER
        and new_role is not Role.OWNER
        and await _owner_count(session, organization_id) <= 1
    ):
        raise Conflict("an organization must keep at least one owner")
    member.role = new_role
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="member.role_change",
        resource_type="member",
        resource_id=member.id,
        organization_id=organization_id,
        details={"from": current.value, "to": new_role.value},
    )
    return member


async def remove_member(
    uow: UnitOfWork,
    *,
    organization_id: uuid.UUID,
    member_id: uuid.UUID,
    actor_role: Role,
    actor: Actor,
) -> None:
    session = uow.session
    member = await session.scalar(
        select(OrganizationMember)
        .where(
            OrganizationMember.id == member_id,
            OrganizationMember.organization_id == organization_id,
        )
        .with_for_update()
    )
    if member is None:
        raise NotFound("member not found")
    current = Role(member.role)
    if not can_manage_member(actor_role, current):
        raise PermissionDenied("insufficient role to remove this member")
    if current is Role.OWNER and await _owner_count(session, organization_id) <= 1:
        raise Conflict("an organization must keep at least one owner")
    await session.delete(member)
    await audit.record(
        session,
        actor=actor,
        action="member.remove",
        resource_type="member",
        resource_id=member_id,
        organization_id=organization_id,
        details={"role": current.value},
    )
