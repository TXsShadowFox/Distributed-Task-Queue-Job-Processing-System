import time
import uuid
from datetime import UTC, datetime

import pytest

from flowforge_shared.ids import uuid7, uuid7_timestamp_ms
from flowforge_shared.messages import DispatchMessage, DispatchReason, project_channel
from flowforge_shared.rbac import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    can_grant_role,
    can_manage_member,
    role_has_permission,
    validate_api_key_scopes,
)


# ------------------------------------------------------------------------------------------ uuid7
def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_is_strictly_monotonic_in_process() -> None:
    ids = [uuid7() for _ in range(20_000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_uuid7_timestamp_is_current() -> None:
    before = time.time_ns() // 1_000_000
    u = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= uuid7_timestamp_ms(u) <= after + 2


def test_uuid7_timestamp_rejects_other_versions() -> None:
    with pytest.raises(ValueError):
        uuid7_timestamp_ms(uuid.uuid4())


# ------------------------------------------------------------------------------------------ rbac
def test_role_hierarchy_is_cumulative() -> None:
    assert ROLE_PERMISSIONS[Role.VIEWER] < ROLE_PERMISSIONS[Role.DEVELOPER]
    assert ROLE_PERMISSIONS[Role.DEVELOPER] < ROLE_PERMISSIONS[Role.ADMIN]
    assert ROLE_PERMISSIONS[Role.ADMIN] < ROLE_PERMISSIONS[Role.OWNER]


def test_viewer_is_read_only() -> None:
    assert role_has_permission(Role.VIEWER, Permission.TASKS_READ)
    assert not role_has_permission(Role.VIEWER, Permission.TASKS_WRITE)
    assert all(p.value.endswith(":read") for p in ROLE_PERMISSIONS[Role.VIEWER])


def test_developer_cannot_manage_credentials_or_members() -> None:
    assert role_has_permission(Role.DEVELOPER, Permission.TASKS_WRITE)
    assert not role_has_permission(Role.DEVELOPER, Permission.API_KEYS_MANAGE)
    assert not role_has_permission(Role.DEVELOPER, Permission.MEMBERS_MANAGE)


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        (Role.OWNER, Role.OWNER, True),
        (Role.OWNER, Role.ADMIN, True),
        (Role.ADMIN, Role.OWNER, False),  # no self-promotion path to owner
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.VIEWER, True),
        (Role.DEVELOPER, Role.VIEWER, False),  # cannot manage members at all
        (Role.VIEWER, Role.VIEWER, False),
    ],
)
def test_can_grant_role(actor: Role, target: Role, allowed: bool) -> None:
    assert can_grant_role(actor, target) is allowed


@pytest.mark.parametrize(
    ("actor", "member", "allowed"),
    [
        (Role.OWNER, Role.OWNER, True),
        (Role.ADMIN, Role.OWNER, False),
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.DEVELOPER, True),
        (Role.DEVELOPER, Role.VIEWER, False),
    ],
)
def test_can_manage_member(actor: Role, member: Role, allowed: bool) -> None:
    assert can_manage_member(actor, member) is allowed


def test_api_key_scopes_exclude_admin_permissions() -> None:
    for p in (Permission.API_KEYS_MANAGE, Permission.MEMBERS_MANAGE, Permission.ORG_MANAGE):
        assert p not in API_KEY_SCOPES
    assert validate_api_key_scopes(["tasks:read", "tasks:write", "tasks:read"]) == [
        Permission.TASKS_READ,
        Permission.TASKS_WRITE,
    ]


@pytest.mark.parametrize("scopes", [[], ["api_keys:manage"], ["tasks:delete"], ["org:manage"]])
def test_invalid_api_key_scopes(scopes: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_api_key_scopes(scopes)


# ------------------------------------------------------------------------------------------ messages
def test_dispatch_message_roundtrip_through_redis_fields() -> None:
    msg = DispatchMessage(
        task_id=uuid7(),
        project_id=uuid.uuid4(),
        queue="default",
        priority=7,
        reason=DispatchReason.SUBMIT,
        enqueued_at=datetime(2026, 1, 1, tzinfo=UTC),
        traceparent="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    )
    raw = {k.encode(): v.encode() for k, v in msg.to_fields().items()}  # what redis-py returns
    assert DispatchMessage.from_fields(raw) == msg


def test_dispatch_message_without_traceparent() -> None:
    msg = DispatchMessage(
        task_id=uuid7(),
        project_id=uuid.uuid4(),
        queue="q",
        priority=1,
        reason=DispatchReason.RETRY,
        enqueued_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert "traceparent" not in msg.to_fields()
    assert DispatchMessage.from_fields(msg.to_fields()) == msg


def test_project_channel() -> None:
    pid = uuid.UUID(int=1)
    assert project_channel(pid) == f"ff:events:project:{pid}"
