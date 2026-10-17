"""Task handler registry.

Only registered handlers ever run. Each handler declares a Pydantic payload model; its JSON Schema is published to the
task-type catalog so the API can reject invalid payloads before they are queued, and the worker validates again
before execution (defence in depth against a stale catalog).

Executors:
* ``async``   — ``async def handler(ctx, payload)`` on the event loop (I/O-bound work).
* ``thread``  — ``def handler(ctx, payload)`` in a thread pool (blocking libraries); ``ctx`` is a thread-safe view.
* ``process`` — ``def handler(payload)`` in a process pool (CPU-bound work; must be a module-level function).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from flowforge_core.services.task_types import TaskTypeDefinition
from flowforge_shared.priority import (
    DEFAULT_PRIORITY,
    DEFAULT_QUEUE,
    validate_priority,
    validate_queue_name,
)

Executor = Literal["async", "thread", "process"]
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    name: str
    func: Callable[..., Any]
    payload_model: type[BaseModel]
    description: str
    queue: str
    priority: int
    max_retries: int
    timeout_seconds: int
    executor: Executor
    example: dict[str, Any] = field(default_factory=dict)

    def catalog_entry(self) -> TaskTypeDefinition:
        return TaskTypeDefinition(
            name=self.name,
            description=self.description,
            queue=self.queue,
            payload_schema=self.payload_model.model_json_schema(),
            example_payload=self.example,
            default_priority=self.priority,
            default_max_retries=self.max_retries,
            default_timeout_seconds=self.timeout_seconds,
            executor=self.executor,
        )


class TaskRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, TaskDefinition] = {}

    def task(
        self,
        name: str,
        *,
        payload: type[BaseModel],
        description: str = "",
        queue: str = DEFAULT_QUEUE,
        priority: int = DEFAULT_PRIORITY,
        max_retries: int = 3,
        timeout: int = 300,
        executor: Executor = "async",
        example: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"invalid task name {name!r}")
        validate_queue_name(queue)
        validate_priority(priority)
        if not 0 <= max_retries <= 100:
            raise ValueError("max_retries must be 0-100")
        if not 1 <= timeout <= 86_400:
            raise ValueError("timeout must be 1-86400 seconds")

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            is_async = inspect.iscoroutinefunction(func)
            if executor == "async" and not is_async:
                raise TypeError(f"{name}: executor='async' requires an 'async def' handler")
            if executor != "async" and is_async:
                raise TypeError(f"{name}: executor={executor!r} requires a regular 'def' handler")
            if executor == "process" and func.__qualname__ != func.__name__:
                raise TypeError(
                    f"{name}: process handlers must be module-level functions (picklable)"
                )
            if name in self._definitions:
                raise ValueError(f"task {name!r} is already registered")
            sample = example if example is not None else {}
            payload.model_validate(sample)  # the documented example must itself be valid
            self._definitions[name] = TaskDefinition(
                name=name,
                func=func,
                payload_model=payload,
                description=description or (inspect.getdoc(func) or "").split("\n\n")[0],
                queue=queue,
                priority=priority,
                max_retries=max_retries,
                timeout_seconds=timeout,
                executor=executor,
                example=sample,
            )
            return func

        return decorator

    def include(self, other: TaskRegistry) -> None:
        for definition in other.definitions():
            if definition.name in self._definitions:
                raise ValueError(f"task {definition.name!r} is already registered")
            self._definitions[definition.name] = definition

    def get(self, name: str) -> TaskDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> list[TaskDefinition]:
        return sorted(self._definitions.values(), key=lambda d: d.name)

    def names(self) -> list[str]:
        return sorted(self._definitions)

    def for_queues(self, queues: list[str]) -> list[TaskDefinition]:
        return [d for d in self.definitions() if d.queue in queues]
