from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from flowforge_core.config import Settings


def create_redis(settings: Settings, *, client_name: str) -> Redis:
    """Binary-safe client (decode_responses=False) with bounded retries on transient connection errors.

    No socket read timeout is set: XREADGROUP BLOCK calls legitimately wait up to ``worker_poll_block_ms``.
    """
    return Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        client_name=client_name[:60],
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry=Retry(ExponentialBackoff(cap=2.0, base=0.05), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
