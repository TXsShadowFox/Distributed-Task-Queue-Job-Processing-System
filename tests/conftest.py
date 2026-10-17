"""Test configuration shared by every suite.

Environment for integration tests (resolved once, before any FlowForge module reads settings):

1. ``FLOWFORGE_TEST_DATABASE_URL`` / ``FLOWFORGE_TEST_REDIS_URL`` if set (CI service containers);
2. otherwise the native dev stack from ``.env`` (``scripts/devstack.py up``), using the ``flowforge_test`` database and
   Redis logical DB 1;
3. otherwise Testcontainers (requires Docker);
4. otherwise integration tests are skipped with an explanatory message.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _from_dotenv() -> tuple[str | None, str | None]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None, None
    from dotenv import dotenv_values

    values = dotenv_values(env_file)
    db = values.get("FLOWFORGE_DATABASE_URL")
    redis = values.get("FLOWFORGE_REDIS_URL")
    if db:
        base, _, _ = db.rpartition("/")
        db = f"{base}/flowforge_test"
    if redis:
        base, _, _ = redis.rpartition("/")
        redis = f"{base}/1"
    return db, redis


TEST_DATABASE_URL = os.environ.get("FLOWFORGE_TEST_DATABASE_URL")
TEST_REDIS_URL = os.environ.get("FLOWFORGE_TEST_REDIS_URL")
if not TEST_DATABASE_URL or not TEST_REDIS_URL:
    dotenv_db, dotenv_redis = _from_dotenv()
    TEST_DATABASE_URL = TEST_DATABASE_URL or dotenv_db
    TEST_REDIS_URL = TEST_REDIS_URL or dotenv_redis

os.environ.update(
    {
        "FLOWFORGE_ENVIRONMENT": "test",
        "FLOWFORGE_JWT_SECRET": secrets.token_urlsafe(48),
        "FLOWFORGE_REDIS_PREFIX": "fftest",
        "FLOWFORGE_COOKIE_SECURE": "false",
        "FLOWFORGE_LOG_JSON": "false",
        "FLOWFORGE_LOG_LEVEL": "WARNING",
        "FLOWFORGE_RATE_LIMIT_PER_MINUTE": "100000",
        "FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE": "100000",
        "FLOWFORGE_BOOTSTRAP_ADMIN_EMAIL": "admin@example.com",
        "FLOWFORGE_DATABASE_POOL_SIZE": "10",
        "FLOWFORGE_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.5",
        "FLOWFORGE_LEASE_TTL_SECONDS": "3",
        "FLOWFORGE_WORKER_RECLAIM_MIN_IDLE_MS": "2000",
        "FLOWFORGE_WORKER_RECLAIM_INTERVAL_SECONDS": "0.5",
        "FLOWFORGE_WORKER_POLL_BLOCK_MS": "200",
        "FLOWFORGE_WORKER_SHUTDOWN_GRACE_SECONDS": "5",
        "FLOWFORGE_SCHEDULER_POLL_INTERVAL_SECONDS": "0.1",
        "FLOWFORGE_REAPER_INTERVAL_SECONDS": "0.5",
        "FLOWFORGE_WORKER_OFFLINE_AFTER_SECONDS": "3",
        "FLOWFORGE_RECONCILE_INTERVAL_SECONDS": "1",
        "FLOWFORGE_RECONCILE_GRACE_SECONDS": "1",
        "FLOWFORGE_WORKER_METRICS_PORT": "0",
        "FLOWFORGE_SCHEDULER_METRICS_PORT": "0",
        "FLOWFORGE_WORKER_QUEUES": "default,reports",
    }
)
if TEST_DATABASE_URL and TEST_REDIS_URL:
    os.environ["FLOWFORGE_DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["FLOWFORGE_REDIS_URL"] = TEST_REDIS_URL
    os.environ["FLOWFORGE_TEST_SERVICES_RESOLVED"] = "1"
