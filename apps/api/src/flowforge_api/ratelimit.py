"""Distributed rate limiting: sliding-window approximation in Redis (one Lua script, atomic).

``weighted = previous_window_count * (1 - elapsed_fraction) + current_window_count`` smooths the 2x burst a plain
fixed window allows at window boundaries. If Redis is unavailable the limiter fails *open* (logged and counted):
availability of the API is preferred, and brute-force protection for logins does not depend on it (account lockout
is enforced in PostgreSQL).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from flowforge_core.errors import RateLimited
from flowforge_core.logs import get_logger
from flowforge_core.observability import metrics

log = get_logger(__name__)

_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local previous = tonumber(redis.call('GET', KEYS[2]) or '0')
local weighted = previous * (1 - tonumber(ARGV[3])) + current
if weighted >= tonumber(ARGV[1]) then
  return {0, current}
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
end
return {1, current}
"""


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after: int


class RateLimiter:
    def __init__(
        self, redis: Redis, *, prefix: str, enabled: bool = True, window_seconds: int = 60
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.enabled = enabled
        self.window = window_seconds
        self._script = redis.register_script(_SCRIPT)

    async def check(self, scope: str, identity: str, limit: int) -> RateDecision:
        if not self.enabled:
            return RateDecision(True, 0)
        now = time.time()
        index = int(now // self.window)
        elapsed = (now % self.window) / self.window
        keys = [
            f"{self.prefix}:rl:{scope}:{identity}:{index}",
            f"{self.prefix}:rl:{scope}:{identity}:{index - 1}",
        ]
        try:
            allowed, _ = await self._script(keys=keys, args=[limit, self.window, f"{elapsed:.6f}"])
        except RedisError as exc:
            metrics.RATE_LIMITED.labels("limiter_unavailable").inc()
            log.warning("rate_limiter_unavailable", error=str(exc))
            return RateDecision(True, 0)
        if int(allowed) == 1:
            return RateDecision(True, 0)
        metrics.RATE_LIMITED.labels(scope).inc()
        return RateDecision(False, max(1, math.ceil((1 - elapsed) * self.window)))

    async def enforce(self, scope: str, identity: str, limit: int) -> None:
        decision = await self.check(scope, identity, limit)
        if not decision.allowed:
            raise RateLimited("too many requests; slow down", retry_after=decision.retry_after)
