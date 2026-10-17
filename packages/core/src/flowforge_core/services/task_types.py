"""Task-type catalog. Workers publish the task types they can execute (with a JSON Schema generated from their
Pydantic payload models); the API validates every submission against it. Nothing submitted through the API is ever
executed as code — only registered handlers run, and only with schema-valid payloads."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import orjson
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowforge_core.db.models import TaskType
from flowforge_core.errors import ValidationFailed

MAX_SCHEMA_ERRORS = 20


@dataclass(frozen=True, slots=True)
class TaskTypeDefinition:
    name: str
    description: str
    queue: str
    payload_schema: dict[str, Any]
    example_payload: dict[str, Any]
    default_priority: int
    default_max_retries: int
    default_timeout_seconds: int
    executor: str

    @property
    def schema_hash(self) -> str:
        canonical = orjson.dumps(
            {
                "schema": self.payload_schema,
                "queue": self.queue,
                "priority": self.default_priority,
                "retries": self.default_max_retries,
                "timeout": self.default_timeout_seconds,
                "executor": self.executor,
            },
            option=orjson.OPT_SORT_KEYS,
        )
        return hashlib.sha256(canonical).hexdigest()


async def register_task_types(session: AsyncSession, definitions: list[TaskTypeDefinition]) -> None:
    """Upsert the catalog. The most recently started worker wins if two versions disagree."""
    if not definitions:
        return
    rows = [
        {
            "name": d.name,
            "description": d.description,
            "queue": d.queue,
            "payload_schema": d.payload_schema,
            "example_payload": d.example_payload,
            "default_priority": d.default_priority,
            "default_max_retries": d.default_max_retries,
            "default_timeout_seconds": d.default_timeout_seconds,
            "executor": d.executor,
            "schema_hash": d.schema_hash,
        }
        for d in definitions
    ]
    stmt = pg_insert(TaskType).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[TaskType.name],
        set_={
            "description": stmt.excluded.description,
            "queue": stmt.excluded.queue,
            "payload_schema": stmt.excluded.payload_schema,
            "example_payload": stmt.excluded.example_payload,
            "default_priority": stmt.excluded.default_priority,
            "default_max_retries": stmt.excluded.default_max_retries,
            "default_timeout_seconds": stmt.excluded.default_timeout_seconds,
            "executor": stmt.excluded.executor,
            "schema_hash": stmt.excluded.schema_hash,
            "last_seen_at": func.now(),
        },
    )
    await session.execute(stmt)


@dataclass(slots=True)
class CatalogEntry:
    name: str
    description: str
    queue: str
    payload_schema: dict[str, Any]
    example_payload: dict[str, Any]
    default_priority: int
    default_max_retries: int
    default_timeout_seconds: int
    executor: str
    schema_hash: str
    validator: Draft202012Validator = field(repr=False)

    def validate_payload(self, payload: Any, *, location: str = "payload") -> None:
        errors = sorted(self.validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
        if not errors:
            return
        details = [
            {
                "loc": [location, *[str(p) for p in err.absolute_path]],
                "msg": err.message[:300],
            }
            for err in errors[:MAX_SCHEMA_ERRORS]
        ]
        raise ValidationFailed(
            f"payload does not match the schema of task type {self.name!r}", details=details
        )


class TaskTypeCatalog:
    """Read-through cache of the catalog. Short TTL: new worker deployments become visible within seconds."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], ttl_seconds: float = 10.0
    ) -> None:
        self._sessionmaker = sessionmaker
        self._ttl = ttl_seconds
        self._entries: dict[str, CatalogEntry] = {}
        self._loaded_at = 0.0
        self._validators: dict[str, Draft202012Validator] = {}

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def _refresh(self) -> None:
        async with self._sessionmaker() as session:
            rows = (await session.scalars(select(TaskType).order_by(TaskType.name))).all()
        entries: dict[str, CatalogEntry] = {}
        for row in rows:
            validator = self._validators.get(row.schema_hash)
            if validator is None:
                try:
                    Draft202012Validator.check_schema(row.payload_schema)
                except SchemaError:
                    continue  # a broken schema must never make every submission fail open
                validator = Draft202012Validator(row.payload_schema)
                self._validators[row.schema_hash] = validator
            entries[row.name] = CatalogEntry(
                name=row.name,
                description=row.description,
                queue=row.queue,
                payload_schema=row.payload_schema,
                example_payload=row.example_payload,
                default_priority=row.default_priority,
                default_max_retries=row.default_max_retries,
                default_timeout_seconds=row.default_timeout_seconds,
                executor=row.executor,
                schema_hash=row.schema_hash,
                validator=validator,
            )
        self._entries = entries
        self._loaded_at = time.monotonic()

    async def all(self) -> list[CatalogEntry]:
        if time.monotonic() - self._loaded_at > self._ttl:
            await self._refresh()
        return list(self._entries.values())

    async def get(self, name: str) -> CatalogEntry | None:
        if time.monotonic() - self._loaded_at > self._ttl or name not in self._entries:
            await self._refresh()
        return self._entries.get(name)

    async def require(self, name: str) -> CatalogEntry:
        entry = await self.get(name)
        if entry is None:
            raise ValidationFailed(
                f"unknown task type {name!r}",
                details=[{"loc": ["task_type"], "msg": "no worker has registered this task type"}],
            )
        return entry
