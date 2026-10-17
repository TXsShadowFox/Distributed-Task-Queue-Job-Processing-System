from __future__ import annotations

from typing import Any

import orjson
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from flowforge_core.config import Settings


def _json_dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode()


def create_engine(
    settings: Settings,
    *,
    application_name: str,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=pool_size if pool_size is not None else settings.database_pool_size,
        max_overflow=max_overflow if max_overflow is not None else settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=settings.database_echo,
        json_serializer=_json_dumps,
        json_deserializer=orjson.loads,
        connect_args={
            "server_settings": {
                "application_name": application_name[:63],
                "statement_timeout": str(settings.database_statement_timeout_ms),
                "timezone": "UTC",
            }
        },
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
