"""Run the API: ``python -m flowforge_api`` (or the ``flowforge-api`` console script)."""

from __future__ import annotations

import os

import uvicorn

from flowforge_core.config import get_settings
from flowforge_core.logs import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(service="flowforge-api", level=settings.log_level, json=settings.log_json)
    # Loopback by default so a developer machine does not expose the API to the network; containers set 0.0.0.0.
    host = os.environ.get("FLOWFORGE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("FLOWFORGE_API_PORT", "8000"))
    uvicorn.run(
        "flowforge_api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
