"""Security: authentication, tenant isolation, RBAC and privilege escalation, API-key scopes/rotation/revocation,
session rotation and reuse detection, lockout, rate limiting, request limits, headers, CORS and audit logging."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import select, text

from flowforge_api.app import create_app
from flowforge_core.config import Settings
from flowforge_core.db.models import AuditLog, RefreshToken
from flowforge_core.db.uow import Database
from tests.integration.conftest import ApiUser, register_user

pytestmark = pytest.mark.integration

CSRF = {"X-Requested-With": "flowforge"}


async def add_member(owner: ApiUser, email: str, role: str) -> dict[str, Any]:
    resp = await owner.post(
        f"/organizations/{owner.org_id}/members", json={"email": email, "role": role}
    )
    assert resp.status_code == 201, resp.text
    member: dict[str, Any] = resp.json()
    return member


async def member_session(client: httpx.AsyncClient, owner: ApiUser, role: str) -> ApiUser:
    """A user who belongs to their own org *and* is added to ``owner``'s org with ``role``."""
    other = await register_user(client)
    await add_member(owner, other.user["email"], role)
    return ApiUser(
        client=client,
        token=other.token,
        user=other.user,
        org_id=owner.org_id,
        project_id=owner.project_id,
    )


# ------------------------------------------------------------------------------------------------ authentication
async def test_requests_without_or_with_bad_credentials_are_rejected(
    client: httpx.AsyncClient, user: ApiUser, settings: Settings
) -> None:
    assert (await client.get("/api/v1/tasks")).status_code == 401
    bad = await client.get(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer not.a.jwt", "X-Project-ID": user.project_id},
    )
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "authentication_failed"

    forged = jwt.encode(
        {"sub": user.user["id"], "exp": 9999999999},
        "wrong-secret-wrong-secret-wrong-secret",
        "HS256",
    )
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    ).status_code == 401

    none_alg = jwt.encode({"sub": user.user["id"]}, key="", algorithm="none")
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {none_alg}"})
    ).status_code == 401


async def test_expired_access_token_is_rejected(
    client: httpx.AsyncClient, user: ApiUser, settings: Settings
) -> None:
    claims = jwt.decode(user.token, options={"verify_signature": False})
    claims["exp"] = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
    expired = jwt.encode(claims, settings.jwt_secret.get_secret_value(), "HS256")
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401 and "expired" in resp.json()["error"]["message"]


async def test_login_lockout_after_repeated_failures(
    client: httpx.AsyncClient, user: ApiUser, settings: Settings
) -> None:
    email = user.user["email"]
    for _ in range(settings.login_max_failures):
        r = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"}
        )
        assert r.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )
    assert locked.status_code == 401  # correct password, but the account is locked
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}
    )
    assert (
        unknown.json()["error"]["message"] == locked.json()["error"]["message"]
    )  # no account enumeration


async def test_refresh_rotation_and_reuse_detection(
    client: httpx.AsyncClient, user: ApiUser, db: Database
) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": user.user["email"], "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    original = client.cookies.get("ff_refresh")
    assert original

    no_csrf = await client.post("/api/v1/auth/refresh")
    assert no_csrf.status_code == 403

    rotated = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert rotated.status_code == 200
    assert client.cookies.get("ff_refresh") != original

    # Replay the old token after the multi-tab grace window: treated as theft, the whole family is revoked.
    async with db.session() as session:
        await session.execute(
            text(
                "UPDATE refresh_tokens SET revoked_at = now() - interval '1 minute' WHERE replaced_by_id IS NOT NULL"
            )
        )
        await session.commit()
    current = client.cookies.get("ff_refresh")
    client.cookies.set("ff_refresh", original, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert replay.status_code == 401
    client.cookies.set("ff_refresh", current or "", path="/api/v1/auth")
    after = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert after.status_code == 401  # the legitimate (latest) token died with its family
    from flowforge_core.security import sha256_hex

    async with db.session() as session:
        family = await session.scalar(
            select(RefreshToken.family_id).where(RefreshToken.token_hash == sha256_hex(original))
        )
        alive_in_family = (
            await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.family_id == family, RefreshToken.revoked_at.is_(None)
                )
            )
        ).all()
        other_sessions = (
            await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == uuid.UUID(user.user["id"]),
                    RefreshToken.family_id != family,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).all()
    assert family is not None and alive_in_family == []
    # Only the compromised session family is revoked; the user's other device (registration session) survives.
    assert len(other_sessions) == 1


async def test_logout_revokes_the_session(client: httpx.AsyncClient, user: ApiUser) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": user.user["email"], "password": "correct-horse-battery"},
    )
    assert (await client.post("/api/v1/auth/logout", headers=CSRF)).status_code == 200
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_password_change_invalidates_existing_tokens(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    old_token = user.token
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {old_token}"},
        json={
            "current_password": "correct-horse-battery",
            "new_password": "an-entirely-new-passphrase",
        },
    )
    assert resp.status_code == 200, resp.text
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    ).status_code == 401
    relog = await client.post(
        "/api/v1/auth/login",
        json={"email": user.user["email"], "password": "an-entirely-new-passphrase"},
    )
    assert relog.status_code == 200


async def test_weak_passwords_and_duplicate_accounts_are_rejected(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    weak = await client.post(
        "/api/v1/auth/register",
        json={"email": "w@example.com", "password": "aaaaaaaaaaaa", "full_name": "W"},
    )
    assert weak.status_code == 422
    dup = await client.post(
        "/api/v1/auth/register",
        json={"email": user.user["email"], "password": "another-good-pass", "full_name": "D"},
    )
    assert dup.status_code == 409


# ------------------------------------------------------------------------------------------------ tenant isolation
async def test_cross_tenant_access_is_indistinguishable_from_not_found(
    client: httpx.AsyncClient, catalog: None
) -> None:
    alice = await register_user(client, org="Alice Inc")
    bob = await register_user(client, org="Bob LLC")
    task = (await alice.post("/tasks", json={"task_type": "test.echo", "payload": {}})).json()
    wf = (
        await alice.post(
            "/workflows",
            json={
                "name": "w",
                "graph": {
                    "nodes": [{"key": "a", "name": "A", "task_type": "test.echo"}],
                    "edges": [],
                },
            },
        )
    ).json()
    sched = (
        await alice.post(
            "/schedules",
            json={"name": "s", "cron_expression": "* * * * *", "task_type": "test.echo"},
        )
    ).json()

    # Bob in his own project cannot see Alice's resources (404, never 403)
    for path in (f"/tasks/{task['id']}", f"/workflows/{wf['id']}", f"/schedules/{sched['id']}"):
        assert (await bob.get(path)).status_code == 404, path
    assert (await bob.post(f"/tasks/{task['id']}/cancel")).status_code == 404
    assert (await bob.post(f"/dead-letter/{task['id']}/retry")).status_code == 404
    assert (await bob.get("/tasks")).json()["items"] == []
    # ...and cannot select Alice's project at all
    assert (await bob.get("/tasks", project_id=alice.project_id)).status_code == 404
    assert (await bob.get(f"/organizations/{alice.org_id}/members")).status_code == 404


# ------------------------------------------------------------------------------------------------ RBAC & escalation
async def test_viewer_is_read_only(client: httpx.AsyncClient, user: ApiUser, catalog: None) -> None:
    viewer = await member_session(client, user, "viewer")
    assert (await viewer.get("/tasks")).status_code == 200
    denied = await viewer.post("/tasks", json={"task_type": "test.echo", "payload": {}})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "permission_denied"
    assert (
        await viewer.post(
            "/schedules",
            json={"name": "x", "cron_expression": "* * * * *", "task_type": "test.echo"},
        )
    ).status_code == 403
    assert (await viewer.get("/api-keys")).status_code == 403


async def test_developer_cannot_manage_keys_or_members(
    client: httpx.AsyncClient, user: ApiUser, catalog: None
) -> None:
    dev = await member_session(client, user, "developer")
    assert (
        await dev.post("/tasks", json={"task_type": "test.echo", "payload": {}})
    ).status_code == 201
    assert (
        await dev.post("/api-keys", json={"name": "k", "scopes": ["tasks:read"]})
    ).status_code == 403
    assert (
        await dev.post(
            f"/organizations/{user.org_id}/members",
            json={"email": "x@example.com", "role": "viewer"},
        )
    ).status_code == 403
    assert (await dev.get(f"/organizations/{user.org_id}/audit-logs")).status_code == 403


async def test_admin_cannot_escalate_to_owner_or_touch_owners(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    admin = await member_session(client, user, "admin")
    members = (await admin.get(f"/organizations/{user.org_id}/members")).json()
    owner_member = next(m for m in members if m["role"] == "owner")
    admin_member = next(m for m in members if m["role"] == "admin")

    assert (
        await admin.patch(
            f"/organizations/{user.org_id}/members/{admin_member['id']}", json={"role": "owner"}
        )
    ).status_code == 403
    assert (
        await admin.patch(
            f"/organizations/{user.org_id}/members/{owner_member['id']}", json={"role": "viewer"}
        )
    ).status_code == 403
    assert (
        await admin.delete(f"/organizations/{user.org_id}/members/{owner_member['id']}")
    ).status_code == 403
    newcomer = await register_user(client)
    assert (
        await admin.post(
            f"/organizations/{user.org_id}/members",
            json={"email": newcomer.user["email"], "role": "owner"},
        )
    ).status_code == 403
    assert (
        await admin.post(
            f"/organizations/{user.org_id}/members",
            json={"email": newcomer.user["email"], "role": "developer"},
        )
    ).status_code == 201


async def test_last_owner_cannot_be_removed_or_demoted(user: ApiUser) -> None:
    owner_member = next(
        m
        for m in (await user.get(f"/organizations/{user.org_id}/members")).json()
        if m["role"] == "owner"
    )
    assert (
        await user.patch(
            f"/organizations/{user.org_id}/members/{owner_member['id']}", json={"role": "admin"}
        )
    ).status_code == 409
    assert (
        await user.delete(f"/organizations/{user.org_id}/members/{owner_member['id']}")
    ).status_code == 409


# ------------------------------------------------------------------------------------------------ API keys
async def _key(user: ApiUser, scopes: list[str], **extra: Any) -> tuple[dict[str, Any], str]:
    resp = await user.post("/api-keys", json={"name": "ci", "scopes": scopes, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"], resp.json()["secret"]


async def test_api_key_scopes_are_enforced(
    client: httpx.AsyncClient, user: ApiUser, catalog: None
) -> None:
    meta, secret = await _key(user, ["tasks:read"])
    assert secret.startswith("ffk_") and meta["prefix"] in secret and "secret" not in meta
    headers = {"Authorization": f"Bearer {secret}"}
    assert (await client.get("/api/v1/tasks", headers=headers)).status_code == 200
    denied = await client.post(
        "/api/v1/tasks", headers=headers, json={"task_type": "test.echo", "payload": {}}
    )
    assert denied.status_code == 403
    # keys can never manage credentials or the organization
    assert (await client.get("/api/v1/api-keys", headers=headers)).status_code == 403
    assert (
        await client.get(f"/api/v1/organizations/{user.org_id}/members", headers=headers)
    ).status_code == 403
    # and are bound to their project
    other = (
        await user.post(f"/organizations/{user.org_id}/projects", json={"name": "Other"})
    ).json()
    assert (
        await client.get("/api/v1/tasks", headers={**headers, "X-Project-ID": other["id"]})
    ).status_code == 403

    _, writer = await _key(user, ["tasks:write", "tasks:read"])
    ok = await client.post(
        "/api/v1/tasks",
        headers={"X-API-Key": writer},
        json={"task_type": "test.echo", "payload": {}},
    )
    assert ok.status_code == 201


async def test_admin_only_scopes_cannot_be_granted_to_keys(user: ApiUser) -> None:
    resp = await user.post("/api-keys", json={"name": "bad", "scopes": ["api_keys:manage"]})
    assert resp.status_code == 422


async def test_revoked_and_expired_keys_stop_working(
    client: httpx.AsyncClient, user: ApiUser, db: Database
) -> None:
    meta, secret = await _key(user, ["tasks:read"])
    assert (await client.get("/api/v1/tasks", headers={"X-API-Key": secret})).status_code == 200
    revoked = await user.delete(f"/api-keys/{meta['id']}")
    assert revoked.json()["status"] == "revoked"
    assert (await client.get("/api/v1/tasks", headers={"X-API-Key": secret})).status_code == 401

    meta2, secret2 = await _key(user, ["tasks:read"], expires_in_days=1)
    async with db.session() as session:
        await session.execute(
            text("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE id = :id"),
            {"id": uuid.UUID(meta2["id"])},
        )
        await session.commit()
    assert (await client.get("/api/v1/tasks", headers={"X-API-Key": secret2})).status_code == 401


async def test_rotation_with_and_without_grace_period(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    meta, old = await _key(user, ["tasks:read"])
    rotated = (
        await user.post(f"/api-keys/{meta['id']}/rotate", json={"grace_period_seconds": 3600})
    ).json()
    new = rotated["secret"]
    assert rotated["key"]["rotated_from_id"] == meta["id"] and rotated["key"]["scopes"] == [
        "tasks:read"
    ]
    for key in (old, new):
        assert (await client.get("/api/v1/tasks", headers={"X-API-Key": key})).status_code == 200
    keys = {k["id"]: k for k in (await user.get("/api-keys")).json()}
    assert keys[meta["id"]]["status"] == "expiring"

    immediate = (
        await user.post(
            f"/api-keys/{rotated['key']['id']}/rotate", json={"grace_period_seconds": 0}
        )
    ).json()
    assert (await client.get("/api/v1/tasks", headers={"X-API-Key": new})).status_code == 401
    assert (
        await client.get("/api/v1/tasks", headers={"X-API-Key": immediate["secret"]})
    ).status_code == 200


# ------------------------------------------------------------------------------------------------ hardening
async def test_rate_limit_returns_429_with_retry_after(settings: Settings, clean: None) -> None:
    limited = settings.model_copy(update={"auth_rate_limit_per_minute": 3})
    app = create_app(limited)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        codes = [
            (
                await c.post(
                    "/api/v1/auth/login", json={"email": "a@b.co", "password": "whatever-123"}
                )
            ).status_code
            for _ in range(5)
        ]
        last = await c.post(
            "/api/v1/auth/login", json={"email": "a@b.co", "password": "whatever-123"}
        )
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]
    assert last.status_code == 429 and int(last.headers["retry-after"]) >= 1


async def test_oversized_bodies_are_rejected(
    client: httpx.AsyncClient, user: ApiUser, catalog: None
) -> None:
    huge = {"task_type": "test.echo", "payload": {"value": "x" * (2 * 1024 * 1024)}}
    assert (await user.post("/tasks", json=huge)).status_code == 413
    big_payload = {"task_type": "test.echo", "payload": {"value": "x" * (300 * 1024)}}
    resp = await user.post("/tasks", json=big_payload)
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "payload_too_large"


async def test_security_headers_and_cors(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-request-id"]

    allowed = await client.options(
        "/api/v1/tasks",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,x-project-id",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    blocked = await client.options(
        "/api/v1/tasks",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in blocked.headers


async def test_sensitive_actions_are_audited_without_secrets(
    user: ApiUser, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    _, secret = await _key(user, ["tasks:read"])
    logs = (await user.get(f"/organizations/{user.org_id}/audit-logs")).json()["items"]
    actions = [entry["action"] for entry in logs]
    assert "api_key.create" in actions and "user.register" in actions
    entry = next(e for e in logs if e["action"] == "api_key.create")
    assert entry["actor_label"] == user.user["email"] and entry["request_id"]
    async with db.session() as session:
        stored = (await session.scalars(select(AuditLog))).all()
    assert all(secret not in str(a.details) for a in stored)
    out = capsys.readouterr()
    assert secret not in out.out and secret not in out.err


async def test_metrics_token_is_enforced_when_configured(settings: Settings, clean: None) -> None:
    from pydantic import SecretStr

    app = create_app(settings.model_copy(update={"metrics_token": SecretStr("scrape-me-please")}))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        assert (await c.get("/metrics")).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer scrape-me-please"})
    assert ok.status_code == 200 and "flowforge_http_request_duration_seconds" in ok.text


async def test_preferences_merge_clear_and_stay_in_tenant(
    client: httpx.AsyncClient, user: ApiUser
) -> None:
    async def prefs(body: dict[str, object]) -> httpx.Response:
        return await user.patch("/auth/me", json={"preferences": body})

    assert (
        await prefs({"theme": "light", "default_project_id": user.project_id})
    ).status_code == 200
    resp = await prefs({"sidebar_collapsed": True})  # other keys are kept
    assert resp.json()["user"]["preferences"] == {
        "theme": "light",
        "default_project_id": user.project_id,
        "sidebar_collapsed": True,
    }
    cleared = await prefs({"default_project_id": None})  # an explicit null clears one key
    assert cleared.json()["user"]["preferences"] == {"theme": "light", "sidebar_collapsed": True}

    # A project of another tenant cannot become the default (and its existence is not revealed).
    other = await register_user(client)
    assert (await prefs({"default_project_id": other.project_id})).status_code == 404
