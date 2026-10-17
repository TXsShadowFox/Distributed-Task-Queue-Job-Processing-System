"""Built-in handlers. Add your own by creating a ``TaskRegistry`` in any importable module and passing
``--handlers your.module:registry`` (or ``FLOWFORGE_WORKER_HANDLER_MODULES``) to the worker."""

from __future__ import annotations

from flowforge_worker.handlers import business, demo
from flowforge_worker.registry import TaskRegistry


def builtin_registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.include(demo.registry)
    registry.include(business.registry)
    return registry
