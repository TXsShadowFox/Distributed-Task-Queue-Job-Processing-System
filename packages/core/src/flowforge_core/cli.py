"""``flowforge-admin``: operational commands that need direct database access.

    flowforge-admin migrate [--revision REV]      apply schema migrations (default: head)
    flowforge-admin downgrade REV                 roll the schema back to REV
    flowforge-admin current                       show the applied revision
    flowforge-admin makemigration "message"       autogenerate a migration from model changes (development)
    flowforge-admin promote EMAIL                 grant platform-admin rights (operate shared workers)
    flowforge-admin demote EMAIL                  revoke them
    flowforge-admin create-admin EMAIL --name N   create a platform admin; the password is read from
                                                  FLOWFORGE_ADMIN_PASSWORD or prompted for (never an argument,
                                                  so it does not end up in shell history or `ps` output)
    flowforge-admin set-password EMAIL            reset a user's password (same password sources) and sign
                                                  out all of their sessions

Settings (database URL and friends) come from FLOWFORGE_* environment variables or ``.env``, as for every service.
Exit status: 0 on success, 1 when the command failed, 2 on invalid usage.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from flowforge_core.config import Settings, get_settings
from flowforge_core.context import Actor
from flowforge_core.db import migrate
from flowforge_core.db.models import RefreshToken, User
from flowforge_core.db.session import create_engine, create_sessionmaker
from flowforge_core.db.uow import Database
from flowforge_core.errors import FlowForgeError
from flowforge_core.security import hash_password, validate_password_policy
from flowforge_core.services import audit, identity

CLI_ACTOR = Actor("system", "flowforge-admin", "flowforge-admin")
PASSWORD_ENV = "FLOWFORGE_ADMIN_PASSWORD"  # noqa: S105  # nosec B105 - an environment variable name


class CommandError(Exception):
    """A failure to report to the operator without a traceback."""


def _out(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _err(message: str) -> None:
    sys.stderr.write(f"error: {message}\n")


async def _with_database[T](settings: Settings, work: Callable[[Database], Awaitable[T]]) -> T:
    engine = create_engine(
        settings, application_name="flowforge-admin", pool_size=1, max_overflow=0
    )
    try:
        return await work(Database(engine, create_sessionmaker(engine)))
    finally:
        await engine.dispose()


async def set_platform_admin(db: Database, email: str, *, admin: bool) -> bool:
    """Grant or revoke platform-admin rights. Returns whether anything changed."""
    normalized = identity.normalize_email(email)
    async with db.transaction() as uow:
        user = await uow.session.scalar(
            select(User).where(User.email == normalized).with_for_update()
        )
        if user is None:
            raise CommandError(f"no user with email {normalized}")
        if user.is_platform_admin == admin:
            return False
        user.is_platform_admin = admin
        await audit.record(
            uow.session,
            actor=CLI_ACTOR,
            action="user.platform_admin_granted" if admin else "user.platform_admin_revoked",
            resource_type="user",
            resource_id=user.id,
            details={"email": normalized},
        )
    return True


async def create_admin(
    db: Database,
    settings: Settings,
    *,
    email: str,
    password: str,
    full_name: str,
    organization: str | None,
) -> identity.Registration:
    async with db.transaction() as uow:
        registration = await identity.register_user(
            uow,
            email=email,
            password=password,
            full_name=full_name,
            organization_name=organization,
            settings=settings,
        )
        registration.user.is_platform_admin = True
        await audit.record(
            uow.session,
            actor=CLI_ACTOR,
            action="user.platform_admin_granted",
            resource_type="user",
            resource_id=registration.user.id,
            organization_id=registration.organization.id,
            details={"email": registration.user.email, "created": True},
        )
    return registration


async def set_password(db: Database, email: str, password: str) -> None:
    """Operator password reset (FlowForge sends no email): new hash, every session revoked, audited."""
    normalized = identity.normalize_email(email)
    validate_password_policy(password, email=normalized)
    password_hash = await asyncio.to_thread(hash_password, password)
    async with db.transaction() as uow:
        user = await uow.session.scalar(
            select(User).where(User.email == normalized).with_for_update()
        )
        if user is None:
            raise CommandError(f"no user with email {normalized}")
        user.password_hash = password_hash
        user.password_changed_at = datetime.now(UTC)
        await uow.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        await audit.record(
            uow.session,
            actor=CLI_ACTOR,
            action="user.password_reset",
            resource_type="user",
            resource_id=user.id,
            details={"email": normalized},
        )


def _read_password() -> str:
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        raise CommandError(f"set {PASSWORD_ENV} or run interactively to enter a password")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise CommandError("passwords do not match")
    return first


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowforge-admin",
        description="FlowForge operational commands.",
        epilog="Settings come from FLOWFORGE_* environment variables or .env.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("migrate", help="apply schema migrations")
    p.add_argument("--revision", default="head", help="target revision (default: head)")
    p = sub.add_parser("downgrade", help="roll the schema back")
    p.add_argument("revision", help="target revision, e.g. -1 or a revision id")
    sub.add_parser("current", help="show the applied schema revision")
    p = sub.add_parser("makemigration", help="autogenerate a migration (development)")
    p.add_argument("message", help="short description, used in the file name")
    p.add_argument(
        "--empty", action="store_true", help="create an empty migration instead of autogenerating"
    )

    for name, help_text in (
        ("promote", "grant platform-admin rights"),
        ("demote", "revoke platform-admin rights"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("email")

    p = sub.add_parser(
        "create-admin", help=f"create a platform admin (password from {PASSWORD_ENV} or a prompt)"
    )
    p.add_argument("email")
    p.add_argument("--name", required=True, help="full name")
    p.add_argument("--organization", help='organization name (default: "<name>\'s organization")')

    p = sub.add_parser(
        "set-password", help=f"reset a user's password (from {PASSWORD_ENV} or a prompt)"
    )
    p.add_argument("email")
    return parser


def run(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = settings or get_settings()
    try:
        match args.command:
            case "migrate":
                migrate.upgrade(settings.database_url, args.revision)
                _out(f"database migrated to {args.revision}")
            case "downgrade":
                migrate.downgrade(settings.database_url, args.revision)
                _out(f"database downgraded to {args.revision}")
            case "current":
                migrate.current(settings.database_url)
            case "makemigration":
                migrate.make_revision(
                    settings.database_url, args.message, autogenerate=not args.empty
                )
            case "promote" | "demote":
                admin = args.command == "promote"
                changed = asyncio.run(
                    _with_database(
                        settings, lambda db: set_platform_admin(db, args.email, admin=admin)
                    )
                )
                state = "is now" if admin else "is no longer"
                _out(
                    f"{args.email} {state} a platform admin"
                    if changed
                    else f"{args.email}: nothing to change"
                )
            case "create-admin":
                password = _read_password()
                registration = asyncio.run(
                    _with_database(
                        settings,
                        lambda db: create_admin(
                            db,
                            settings,
                            email=args.email,
                            password=password,
                            full_name=args.name,
                            organization=args.organization,
                        ),
                    )
                )
                _out(
                    f"created platform admin {registration.user.email} "
                    f"(organization {registration.organization.name!r}, project {registration.project.name!r})"
                )
            case "set-password":
                password = _read_password()
                asyncio.run(
                    _with_database(settings, lambda db: set_password(db, args.email, password))
                )
                _out(f"password of {args.email} reset; all of their sessions were signed out")
            case _:  # pragma: no cover - argparse rejects unknown commands
                return 2
    except (CommandError, FlowForgeError) as exc:
        _err(str(exc))
        return 1
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
