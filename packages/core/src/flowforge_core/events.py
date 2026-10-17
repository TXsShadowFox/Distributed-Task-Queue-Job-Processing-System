"""Best-effort realtime notifications over Redis Pub/Sub (published after the database commit)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError

from flowforge_core.logs import get_logger
from flowforge_core.observability import metrics
from flowforge_shared.messages import RealtimeEvent, fleet_channel, project_channel

log = get_logger(__name__)


def worker_control_channel(worker_id: str, prefix: str = "ff") -> str:
    return f"{prefix}:control:worker:{worker_id}"


class EventPublisher:
    def __init__(self, redis: Redis, prefix: str = "ff") -> None:
        self.redis = redis
        self.prefix = prefix

    def channel_for(self, event: RealtimeEvent) -> str:
        if event.project_id is None:
            return fleet_channel(self.prefix)
        return project_channel(event.project_id, self.prefix)

    async def publish(
        self,
        events: Sequence[RealtimeEvent],
        control: Sequence[tuple[str, dict[str, Any]]] = (),
    ) -> None:
        """Never raises: realtime delivery is an optimization, clients re-fetch authoritative state.

        Failures are logged and counted rather than silently dropped.
        """
        if not events and not control:
            return
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for event in events:
                    pipe.publish(self.channel_for(event), event.model_dump_json())
                for channel, message in control:
                    pipe.publish(channel, orjson.dumps(message))
                await pipe.execute()
        except RedisError as exc:
            metrics.EVENT_PUBLISH_FAILURES.inc(len(events) + len(control))
            log.warning("realtime_publish_failed", error=str(exc), events=len(events))
