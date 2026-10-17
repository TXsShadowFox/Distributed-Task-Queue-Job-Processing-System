from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis

from flowforge_api.ratelimit import RateLimiter
from flowforge_api.realtime import EventHub
from flowforge_core.broker import StreamBroker
from flowforge_core.config import Settings
from flowforge_core.db.uow import Database
from flowforge_core.services.task_types import TaskTypeCatalog


@dataclass(slots=True)
class AppState:
    settings: Settings
    db: Database
    redis: Redis
    broker: StreamBroker
    catalog: TaskTypeCatalog
    hub: EventHub
    limiter: RateLimiter


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.ff
    return state
