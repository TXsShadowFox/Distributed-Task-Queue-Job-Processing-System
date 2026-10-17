"""Role-based access control: organization roles, permissions and API-key scopes.

Roles are ranked owner > admin > developer > viewer. Privilege-escalation rules:
* a member may only grant roles at or below their own rank;
* a member may only modify or remove members whose current rank is at or below their own;
* only owners may grant or revoke the owner role, and the last owner can never be demoted or removed
  (enforced in the membership service, which knows the owner count).
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"


ROLE_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.DEVELOPER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Permission(StrEnum):
    TASKS_READ = "tasks:read"
    TASKS_WRITE = "tasks:write"  # submit, cancel, retry, DLQ retry/archive
    WORKFLOWS_READ = "workflows:read"
    WORKFLOWS_WRITE = "workflows:write"  # create, edit, execute, cancel runs
    SCHEDULES_READ = "schedules:read"
    SCHEDULES_WRITE = "schedules:write"
    ANALYTICS_READ = "analytics:read"
    WORKERS_READ = "workers:read"
    API_KEYS_MANAGE = "api_keys:manage"
    MEMBERS_MANAGE = "members:manage"
    PROJECTS_MANAGE = "projects:manage"
    AUDIT_READ = "audit:read"
    ORG_MANAGE = "org:manage"


_READ = frozenset(
    {
        Permission.TASKS_READ,
        Permission.WORKFLOWS_READ,
        Permission.SCHEDULES_READ,
        Permission.ANALYTICS_READ,
        Permission.WORKERS_READ,
    }
)
_WRITE = frozenset({Permission.TASKS_WRITE, Permission.WORKFLOWS_WRITE, Permission.SCHEDULES_WRITE})
_ADMIN = frozenset(
    {
        Permission.API_KEYS_MANAGE,
        Permission.MEMBERS_MANAGE,
        Permission.PROJECTS_MANAGE,
        Permission.AUDIT_READ,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _READ,
    Role.DEVELOPER: _READ | _WRITE,
    Role.ADMIN: _READ | _WRITE | _ADMIN,
    Role.OWNER: _READ | _WRITE | _ADMIN | {Permission.ORG_MANAGE},
}

#: Permissions that may be granted to API keys. Keys can never manage keys, members, projects or the org,
#: so a leaked key cannot be used to mint new credentials or escalate privileges.
API_KEY_SCOPES: frozenset[Permission] = _READ | _WRITE


def role_has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def can_grant_role(actor: Role, target_role: Role) -> bool:
    if target_role is Role.OWNER:
        return actor is Role.OWNER
    return ROLE_RANK[actor] >= ROLE_RANK[target_role] and role_has_permission(
        actor, Permission.MEMBERS_MANAGE
    )


def can_manage_member(actor: Role, member_current_role: Role) -> bool:
    if not role_has_permission(actor, Permission.MEMBERS_MANAGE):
        return False
    if member_current_role is Role.OWNER:
        return actor is Role.OWNER
    return ROLE_RANK[actor] >= ROLE_RANK[member_current_role]


def validate_api_key_scopes(scopes: list[str]) -> list[Permission]:
    if not scopes:
        raise ValueError("at least one scope is required")
    result: list[Permission] = []
    for s in scopes:
        try:
            p = Permission(s)
        except ValueError:
            raise ValueError(f"unknown scope {s!r}") from None
        if p not in API_KEY_SCOPES:
            raise ValueError(f"scope {s!r} cannot be granted to API keys")
        if p not in result:
            result.append(p)
    return result
