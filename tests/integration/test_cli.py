"""flowforge-admin: platform-admin management and migrations against the real test database.

``cli.run`` calls ``asyncio.run``, so each invocation runs in a worker thread with its own event loop.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from flowforge_core import cli
from flowforge_core.config import Settings
from flowforge_core.db.models import AuditLog, User
from flowforge_core.db.uow import Database

from .conftest import ApiUser, register_user


async def run_cli(settings: Settings, *argv: str) -> int:
    return await asyncio.to_thread(cli.run, list(argv), settings)


async def is_platform_admin(user: ApiUser) -> bool:
    me = await user.get("/auth/me")
    assert me.status_code == 200, me.text
    return bool(me.json()["user"]["is_platform_admin"])


async def test_promote_and_demote(
    settings: Settings, db: Database, client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    user = await register_user(client, email="operator@example.com")
    assert not await is_platform_admin(user)

    assert await run_cli(settings, "promote", "Operator@Example.com") == 0
    assert await is_platform_admin(user)
    assert "is now a platform admin" in capsys.readouterr().out

    assert await run_cli(settings, "promote", "operator@example.com") == 0
    assert "nothing to change" in capsys.readouterr().out

    assert await run_cli(settings, "demote", "operator@example.com") == 0
    assert not await is_platform_admin(user)

    async with db.session() as session:
        actions = (
            await session.scalars(
                select(AuditLog.action)
                .where(
                    AuditLog.resource_id == user.user["id"],
                    AuditLog.action.like("user.platform_admin%"),
                )
                .order_by(AuditLog.id)
            )
        ).all()
    assert actions == ["user.platform_admin_granted", "user.platform_admin_revoked"]


async def test_promote_unknown_user_fails(
    settings: Settings, client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli(settings, "promote", "ghost@example.com") == 1
    assert "no user with email ghost@example.com" in capsys.readouterr().err


async def test_create_admin_reads_password_from_environment(
    settings: Settings, db: Database, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, "a-long-admin-password")
    assert await run_cli(settings, "create-admin", "ops@example.com", "--name", "Ops Team") == 0

    login = await client.post(
        "/api/v1/auth/login", json={"email": "ops@example.com", "password": "a-long-admin-password"}
    )
    assert login.status_code == 200, login.text
    async with db.session() as session:
        row = await session.scalar(select(User).where(User.email == "ops@example.com"))
    assert row is not None and row.is_platform_admin and row.full_name == "Ops Team"


async def test_create_admin_validates_input(
    settings: Settings,
    db: Database,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(cli.PASSWORD_ENV, "short")
    assert await run_cli(settings, "create-admin", "weak@example.com", "--name", "Weak") == 1
    assert "password must be at least" in capsys.readouterr().err

    # Without the variable and without a terminal there is no safe way to read a password.
    monkeypatch.delenv(cli.PASSWORD_ENV)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert await run_cli(settings, "create-admin", "weak@example.com", "--name", "Weak") == 1
    assert cli.PASSWORD_ENV in capsys.readouterr().err

    async with db.session() as session:
        assert await session.scalar(select(User).where(User.email == "weak@example.com")) is None


async def test_create_admin_refuses_existing_email(
    settings: Settings,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await register_user(client, email="taken@example.com")
    monkeypatch.setenv(cli.PASSWORD_ENV, "a-long-admin-password")
    assert await run_cli(settings, "create-admin", "taken@example.com", "--name", "Dup") == 1
    assert "already exists" in capsys.readouterr().err


async def test_migrate_is_idempotent_at_head(
    settings: Settings, schema: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_cli(settings, "migrate") == 0
    assert "database migrated to head" in capsys.readouterr().out
    assert await run_cli(settings, "current") == 0


def test_usage_errors_exit_with_status_2(settings: Settings) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.run(["promote"], settings)
    assert exc.value.code == 2


async def test_set_password_resets_and_signs_out_everywhere(
    settings: Settings,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user = await register_user(client, email="forgetful@example.com")
    monkeypatch.setenv(cli.PASSWORD_ENV, "a-brand-new-password")
    assert await run_cli(settings, "set-password", "forgetful@example.com") == 0
    assert "signed out" in capsys.readouterr().out

    old = await client.post(
        "/api/v1/auth/login",
        json={"email": "forgetful@example.com", "password": "correct-horse-battery"},
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login",
        json={"email": "forgetful@example.com", "password": "a-brand-new-password"},
    )
    assert new.status_code == 200
    # Access tokens issued before the reset stop working.
    assert (await user.get("/auth/me")).status_code == 401

    monkeypatch.setenv(cli.PASSWORD_ENV, "short")
    assert await run_cli(settings, "set-password", "forgetful@example.com") == 1
    monkeypatch.setenv(cli.PASSWORD_ENV, "a-brand-new-password")
    assert await run_cli(settings, "set-password", "nobody@example.com") == 1
