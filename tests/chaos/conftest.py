"""Chaos tests: real processes killed with SIGKILL and real network partitions (see proxy.py).

They are slower than the integration suite and start subprocesses, so they are opt-in:
    FLOWFORGE_RUN_CHAOS=1 pytest tests/chaos        (or: make chaos)
They reuse the integration fixtures (test database, Redis DB 1, in-process API client).
"""

from __future__ import annotations

import os

import pytest

# Fixture functions are registered by importing them into this conftest.
from tests.integration.conftest import (  # noqa: F401
    catalog,
    clean,
    client,
    control_plane,
    db,
    redis,
    schema,
    settings,
    user,
    worker_factory,
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run = os.environ.get("FLOWFORGE_RUN_CHAOS") == "1"
    skip = pytest.mark.skip(
        reason="chaos tests are opt-in: set FLOWFORGE_RUN_CHAOS=1 (or run `make chaos`)"
    )
    for item in items:
        if "tests/chaos" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.chaos)
            if not run:
                item.add_marker(skip)
