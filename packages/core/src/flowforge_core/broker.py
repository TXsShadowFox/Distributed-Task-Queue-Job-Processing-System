"""Redis Streams dispatch channel.

Delivery contract: a stream entry is acknowledged (XACK + XDEL) only after the worker has durably claimed the task in
PostgreSQL. If a worker dies between XREADGROUP and the claim, the entry stays in the consumer group's pending
entries list (PEL) and another worker reclaims it with XAUTOCLAIM once it has been idle for ``min_idle_ms``.
Redis therefore provides at-least-once *hand-off*; PostgreSQL leases provide at-least-once *execution*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from flowforge_shared.priority import PriorityBand, stream_key

CONSUMER_GROUP = "ff-workers"


@dataclass(frozen=True, slots=True)
class StreamMessage:
    stream: str
    message_id: str
    fields: dict[bytes, bytes]


@dataclass(frozen=True, slots=True)
class StreamStats:
    stream: str
    length: int  # entries not yet deleted = undelivered + delivered-but-unacked
    pending: int  # delivered but not yet acknowledged


def _s(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class StreamBroker:
    def __init__(self, redis: Redis, prefix: str = "ff") -> None:
        self.redis = redis
        self.prefix = prefix
        self._known_groups: set[str] = set()

    def stream_for(self, queue: str, band: PriorityBand) -> str:
        return stream_key(queue, band, self.prefix)

    def streams_for_queues(self, queues: Iterable[str]) -> list[str]:
        return [self.stream_for(q, b) for q in queues for b in PriorityBand]

    async def ensure_group(self, stream: str) -> None:
        try:
            # Start at id 0 so entries written before the group existed are still delivered.
            await self.redis.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._known_groups.add(stream)

    async def ensure_groups(self, streams: Iterable[str]) -> None:
        for stream in streams:
            await self.ensure_group(stream)

    async def publish(self, entries: Sequence[tuple[str, dict[str, str]]]) -> list[str]:
        """XADD a batch atomically per entry (pipeline, not MULTI). Returns the new entry ids."""
        if not entries:
            return []
        async with self.redis.pipeline(transaction=False) as pipe:
            for stream, fields in entries:
                pipe.xadd(stream, fields)  # type: ignore[arg-type]
            ids = await pipe.execute()
        return [_s(i) for i in ids]

    async def read(
        self, consumer: str, streams_in_order: Sequence[str], count: int, block_ms: int
    ) -> list[StreamMessage]:
        """Read up to ``count`` new entries, trying streams in the given (priority) order.

        A cheap non-blocking pass honours the priority order; only if every stream is empty do we block on all of
        them at once, so a new high-priority entry wakes the worker immediately.
        """
        messages: list[StreamMessage] = []
        for stream in streams_in_order:
            remaining = count - len(messages)
            if remaining <= 0:
                break
            messages.extend(await self._read_group({stream: ">"}, consumer, remaining, None))
        if messages:
            return messages
        return await self._read_group(
            dict.fromkeys(streams_in_order, ">"), consumer, count, block_ms
        )

    async def _read_group(
        self, streams: dict[str, str], consumer: str, count: int, block_ms: int | None
    ) -> list[StreamMessage]:
        stream_ids: dict[Any, Any] = dict(streams)
        try:
            response = await self.redis.xreadgroup(
                CONSUMER_GROUP, consumer, stream_ids, count=count, block=block_ms
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                # Stream or group vanished (e.g. Redis restarted without persistence): recreate and retry later.
                await self.ensure_groups(streams.keys())
                return []
            raise
        result: list[StreamMessage] = []
        for stream_name, entries in cast(list[Any], response or []):
            for message_id, fields in entries:
                if fields is None:  # entry deleted while pending
                    continue
                result.append(StreamMessage(_s(stream_name), _s(message_id), fields))
        return result

    async def ack(self, stream: str, message_id: str) -> None:
        """Acknowledge and delete. With a single consumer group, an acknowledged entry is never needed again."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(stream, CONSUMER_GROUP, message_id)
            pipe.xdel(stream, message_id)
            await pipe.execute()

    async def requeue(self, messages: Sequence[StreamMessage]) -> None:
        """Hand delivered-but-unclaimed entries back to the group immediately (Redis Streams has no NACK).

        Each entry is re-added at the tail and the original acknowledged and deleted, atomically (MULTI), so the
        entry is neither lost nor duplicated. Used when a worker starts draining or stopping between XREADGROUP
        returning and claiming; otherwise the entries would sit in its PEL until another worker reclaims them.
        """
        if not messages:
            return
        async with self.redis.pipeline(transaction=True) as pipe:
            for m in messages:
                pipe.xadd(m.stream, m.fields)  # type: ignore[arg-type]
                pipe.xack(m.stream, CONSUMER_GROUP, m.message_id)
                pipe.xdel(m.stream, m.message_id)
            await pipe.execute()

    async def autoclaim(
        self, stream: str, consumer: str, min_idle_ms: int, count: int, start_id: str = "0-0"
    ) -> tuple[str, list[StreamMessage]]:
        """Claim entries idle for at least ``min_idle_ms`` (their consumer is presumed dead)."""
        try:
            response = await self.redis.xautoclaim(
                stream, CONSUMER_GROUP, consumer, min_idle_ms, start_id=start_id, count=count
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                await self.ensure_group(stream)
                return "0-0", []
            raise
        parts = response
        next_id = _s(parts[0])
        claimed = [
            StreamMessage(stream, _s(mid), fields) for mid, fields in parts[1] if fields is not None
        ]
        return next_id, claimed

    async def stats(self, streams: Iterable[str]) -> list[StreamStats]:
        stream_list = list(streams)
        async with self.redis.pipeline(transaction=False) as pipe:
            for stream in stream_list:
                pipe.xlen(stream)
                pipe.xpending(stream, CONSUMER_GROUP)
            raw = await pipe.execute(raise_on_error=False)
        out: list[StreamStats] = []
        for i, stream in enumerate(stream_list):
            length = raw[2 * i]
            pending = raw[2 * i + 1]
            length_value = length if isinstance(length, int) else 0
            pending_value = int(pending.get("pending", 0)) if isinstance(pending, dict) else 0
            out.append(StreamStats(stream, length_value, pending_value))
        return out

    async def remove_consumer(self, stream: str, consumer: str) -> int:
        try:
            return int(await self.redis.xgroup_delconsumer(stream, CONSUMER_GROUP, consumer))
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                return 0
            raise
