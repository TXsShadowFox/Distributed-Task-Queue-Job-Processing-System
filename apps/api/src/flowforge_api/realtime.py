"""Fan-out of Redis Pub/Sub events to Server-Sent-Events clients.

One pattern subscription per API process (not one Redis connection per browser tab). Each SSE client gets a bounded
queue; a slow client that overflows it receives a ``resync`` event (and should re-fetch) instead of unbounded memory
growth. The hub reconnects with backoff if Redis drops; clients are told to resync after a reconnect because events
published during the gap were missed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from flowforge_core.logs import get_logger

log = get_logger(__name__)

QUEUE_SIZE = 1000
RESYNC = {"type": "resync"}


class Subscription:
    def __init__(self, channels: set[str]) -> None:
        self.channels = channels
        self.queue: asyncio.Queue[dict[str, Any] | bytes] = asyncio.Queue(maxsize=QUEUE_SIZE)

    def offer(self, item: dict[str, Any] | bytes) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Drop the backlog; the client re-fetches authoritative state.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(RESYNC)


class EventHub:
    def __init__(self, redis: Redis, prefix: str) -> None:
        self.redis = redis
        self.pattern = f"{prefix}:events:*"
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None
        self.connected = False

    def subscribe(self, channels: set[str]) -> Subscription:
        sub = Subscription(channels)
        for channel in channels:
            self._subs[channel].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        for channel in sub.channels:
            subs = self._subs.get(channel)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    self._subs.pop(channel, None)

    @property
    def subscriber_count(self) -> int:
        return len({s for subs in self._subs.values() for s in subs})

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        backoff = 0.5
        first = True
        while True:
            try:
                async with self.redis.pubsub(ignore_subscribe_messages=True) as pubsub:
                    await pubsub.psubscribe(self.pattern)
                    self.connected = True
                    backoff = 0.5
                    if not first:
                        self._broadcast(RESYNC)
                    first = False
                    async for message in pubsub.listen():
                        if message.get("type") != "pmessage":
                            continue
                        channel = message["channel"]
                        channel = channel.decode() if isinstance(channel, bytes) else str(channel)
                        for sub in tuple(self._subs.get(channel, ())):
                            sub.offer(message["data"])
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError) as exc:
                self.connected = False
                log.warning("event_hub_disconnected", error=str(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def _broadcast(self, item: dict[str, Any]) -> None:
        for sub in {s for subs in self._subs.values() for s in subs}:
            sub.offer(item)
