"""Run a worker: ``python -m flowforge_worker`` (or the ``flowforge-worker`` console script).

flowforge-worker --queues default,reports --concurrency 16 --handlers mypkg.tasks:registry
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys

from prometheus_client import start_http_server

from flowforge_core.config import get_settings
from flowforge_core.logs import configure_logging, get_logger
from flowforge_core.observability.tracing import setup_tracing
from flowforge_worker.handlers import builtin_registry
from flowforge_worker.registry import TaskRegistry
from flowforge_worker.runtime import Worker

log = get_logger("flowforge_worker")


def load_registry(specs: list[str], include_builtin: bool) -> TaskRegistry:
    registry = builtin_registry() if include_builtin else TaskRegistry()
    for spec in specs:
        module_name, _, attr = spec.partition(":")
        module = importlib.import_module(module_name)
        extra = getattr(module, attr or "registry")
        if not isinstance(extra, TaskRegistry):
            raise SystemExit(f"{spec} is not a TaskRegistry")
        registry.include(extra)
    return registry


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="FlowForge worker")
    parser.add_argument("--queues", default=",".join(settings.worker_queues))
    parser.add_argument("--concurrency", type=int, default=settings.worker_concurrency)
    parser.add_argument("--name", default=settings.worker_name)
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=settings.worker_metrics_port,
        help="Prometheus port (0 disables)",
    )
    parser.add_argument(
        "--handlers",
        action="append",
        default=[],
        help="Extra handler registry, 'module:attribute' (repeatable)",
    )
    parser.add_argument(
        "--no-builtin", action="store_true", help="Do not load the built-in demo handlers"
    )
    args = parser.parse_args(argv)

    configure_logging(service="flowforge-worker", level=settings.log_level, json=settings.log_json)
    setup_tracing("flowforge-worker", settings)
    env_handlers = [
        s for s in os.environ.get("FLOWFORGE_WORKER_HANDLER_MODULES", "").split(",") if s
    ]
    registry = load_registry([*env_handlers, *args.handlers], include_builtin=not args.no_builtin)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    if args.metrics_port:
        start_http_server(args.metrics_port)
    worker = Worker(settings, registry, queues=queues, concurrency=args.concurrency, name=args.name)
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        # A second Ctrl+C on Windows can surface here; shutdown already ran or was forced.
        log.warning("worker_interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
