"""Programmatic Alembic entry points (no alembic.ini needed)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Passed via attributes (not the ini option) so '%' in passwords needs no escaping.
    cfg.attributes["database_url"] = database_url
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


def make_revision(database_url: str, message: str, *, autogenerate: bool = True) -> None:
    command.revision(alembic_config(database_url), message=message, autogenerate=autogenerate)


def current(database_url: str) -> None:
    command.current(alembic_config(database_url), verbose=True)
