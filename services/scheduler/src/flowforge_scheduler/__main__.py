"""Run the control plane: ``python -m flowforge_scheduler`` (or the ``flowforge-scheduler`` console script)."""

from __future__ import annotations

import asyncio
import os
import signal

from prometheus_client import start_http_server

from flowforge_core.config import get_settings
from flowforge_core.logs import configure_logging
from flowforge_core.observability.tracing import setup_tracing
from flowforge_scheduler.service import ControlPlane


async def _run() -> None:
    settings = get_settings()
    plane = ControlPlane(settings)
    loop = asyncio.get_running_loop()
    if os.name == "nt":
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(plane.request_stop))
        signal.signal(signal.SIGBREAK, lambda *_: loop.call_soon_threadsafe(plane.request_stop))
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, plane.request_stop)
    await plane.run()


def main() -> None:
    settings = get_settings()
    configure_logging(
        service="flowforge-scheduler", level=settings.log_level, json=settings.log_json
    )
    setup_tracing("flowforge-scheduler", settings)
    if settings.scheduler_metrics_port:
        start_http_server(settings.scheduler_metrics_port)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
