"""Organizations, members, projects and the audit log: the happy paths behind the Settings page.

Privilege boundaries (who may grant what) are covered in test_security.py.
"""

from __future__ import annotations

import httpx

from tests.integration.conftest import ApiUser, register_user


async def login_as(client: httpx.AsyncClient, other: ApiUser, project_id: str) -> ApiUser:
    """``other``'s session, acting in ``project_id`` (a project of an organization they joined)."""
    return ApiUser(client, other.token, other.user, other.org_id, project_id)


async def test_owner_renames_organization_and_admin_cannot(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    org = (await user.get(f"/organizations/{user.org_id}")).json()
    assert org["id"] == user.org_id and org["slug"]

    renamed = await user.patch(f"/organizations/{user.org_id}", json={"name": "Acme Robotics"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Acme Robotics"
    assert renamed.json()["slug"] == org["slug"]  # the slug is stable

    admin = await register_user(client)
    added = await user.post(
        f"/organizations/{user.org_id}/members",
        json={"email": admin.user["email"], "role": "admin"},
    )
    assert added.status_code == 201
    as_admin = await login_as(client, admin, user.project_id)
    assert (
        await as_admin.patch(f"/organizations/{user.org_id}", json={"name": "Hijacked"})
    ).status_code == 403


async def test_member_lifecycle(client: httpx.AsyncClient, user: ApiUser) -> None:
    teammate = await register_user(client)
    email = teammate.user["email"]

    missing = await user.post(
        f"/organizations/{user.org_id}/members",
        json={"email": "nobody@example.com", "role": "viewer"},
    )
    assert missing.status_code == 404

    added = await user.post(
        f"/organizations/{user.org_id}/members", json={"email": email.upper(), "role": "viewer"}
    )
    assert added.status_code == 201, added.text
    member = added.json()
    assert member["email"] == email and member["role"] == "viewer"

    duplicate = await user.post(
        f"/organizations/{user.org_id}/members", json={"email": email, "role": "viewer"}
    )
    assert duplicate.status_code == 409

    # The new member sees the organization's projects with their role.
    as_teammate = await login_as(client, teammate, user.project_id)
    visible = (await as_teammate.get("/projects")).json()
    shared = next(p for p in visible if p["id"] == user.project_id)
    assert shared["role"] == "viewer"

    promoted = await user.patch(
        f"/organizations/{user.org_id}/members/{member['id']}", json={"role": "developer"}
    )
    assert promoted.status_code == 200 and promoted.json()["role"] == "developer"

    assert (
        await user.delete(f"/organizations/{user.org_id}/members/{member['id']}")
    ).status_code == 204
    emails = {m["email"] for m in (await user.get(f"/organizations/{user.org_id}/members")).json()}
    assert email not in emails
    # Access ends immediately.
    assert (await as_teammate.get("/tasks")).status_code in {403, 404}


async def test_projects_create_update_and_clear_settings(user: ApiUser) -> None:
    created = await user.post(
        f"/organizations/{user.org_id}/projects", json={"name": "Payments staging"}
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["slug"] == "payments-staging" and project["settings"] == {}

    updated = await user.patch(
        f"/projects/{project['id']}",
        json={"description": "pre-production", "settings": {"retention_days": 7}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"] == {"retention_days": 7}
    assert updated.json()["description"] == "pre-production"

    renamed = await user.patch(f"/projects/{project['id']}", json={"name": "Payments QA"})
    assert renamed.json()["settings"] == {"retention_days": 7}  # untouched when not sent

    cleared = await user.patch(
        f"/projects/{project['id']}", json={"settings": {"retention_days": None}}
    )
    assert cleared.status_code == 200 and cleared.json()["settings"] == {}

    unknown = await user.patch(
        f"/projects/{project['id']}", json={"settings": {"default_queue": "x"}}
    )
    assert unknown.status_code == 422  # settings without an effect are not accepted

    names = {p["name"] for p in (await user.get("/projects")).json()}
    assert {"Default", "Payments QA"} <= names


async def test_audit_log_pages_newest_first(user: ApiUser) -> None:
    for i in range(5):
        resp = await user.post(f"/organizations/{user.org_id}/projects", json={"name": f"p{i}"})
        assert resp.status_code == 201

    first = (await user.get(f"/organizations/{user.org_id}/audit-logs", params={"limit": 3})).json()
    assert len(first["items"]) == 3 and first["next_before_id"] is not None
    ids = [e["id"] for e in first["items"]]
    assert ids == sorted(ids, reverse=True)
    assert first["items"][0]["action"] == "project.create"
    assert first["items"][0]["actor_label"] == user.user["email"]

    second = (
        await user.get(
            f"/organizations/{user.org_id}/audit-logs",
            params={"limit": 3, "before_id": first["next_before_id"]},
        )
    ).json()
    assert second["items"] and max(e["id"] for e in second["items"]) < min(ids)


async def test_role_matrix_lists_roles_and_key_scopes(user: ApiUser) -> None:
    matrix = (await user.get("/organizations/roles")).json()
    assert set(matrix["roles"]) == {"owner", "admin", "developer", "viewer"}
    assert "org:manage" in matrix["roles"]["owner"]
    assert "org:manage" not in matrix["roles"]["admin"]
    assert "tasks:write" not in matrix["roles"]["viewer"]
    assert "api_keys:manage" not in matrix["api_key_scopes"]
    assert set(matrix["api_key_scopes"]) <= set(matrix["roles"]["owner"])
