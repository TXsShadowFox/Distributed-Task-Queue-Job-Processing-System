from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowforge_core.events import EventPublisher
from flowforge_core.logs import get_logger
from flowforge_shared.messages import RealtimeEvent

log = get_logger(__name__)


class UnitOfWork:
    """One database transaction plus the side effects that may only happen after it commits:
    realtime events, worker control messages and metric increments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events: list[RealtimeEvent] = []
        self.control: list[tuple[str, dict[str, Any]]] = []
        self._after_commit: list[Callable[[], None]] = []

    def emit(self, event: RealtimeEvent) -> None:
        self.events.append(event)

    def send_control(self, channel: str, message: dict[str, Any]) -> None:
        self.control.append((channel, message))

    def after_commit(self, fn: Callable[[], None]) -> None:
        self._after_commit.append(fn)

    def run_after_commit(self) -> None:
        for fn in self._after_commit:
            fn()


class Database:
    def __init__(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        publisher: EventPublisher | None = None,
    ) -> None:
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.publisher = publisher

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[UnitOfWork]:
        async with self.sessionmaker() as session:
            uow = UnitOfWork(session)
            async with session.begin():
                yield uow
        # Only reached when the transaction committed.
        uow.run_after_commit()
        if self.publisher is not None:
            await self.publisher.publish(uow.events, uow.control)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
