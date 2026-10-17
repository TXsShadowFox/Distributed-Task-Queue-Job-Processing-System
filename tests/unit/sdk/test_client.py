"""SDK behaviour against a mocked transport: retries, idempotency, error mapping, pagination, waiting."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from flowforge_sdk import (
    AsyncFlowForgeClient,
    ConflictError,
    FlowForgeClient,
    NotFoundError,
    RateLimitError,
    ServerError,
    TaskWaitTimeout,
    TransportError,
    ValidationError,
)

TASK = {
    "id": "0195f3c2-0000-7000-8000-000000000001",
    "task_type": "generate_report",
    "state": "QUEUED",
    "queue": "reports",
    "priority": 5,
    "attempt_count": 0,
    "retry_count": 0,
    "max_retries": 3,
    "payload": {"report_id": 42},
    "created_at": "2026-10-11T08:00:00Z",
}


def error(status: int, code: str, message: str, **extra: Any) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": message, "details": None, "request_id": "req-1"}},
        **extra,
    )


def client(handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> FlowForgeClient:
    return FlowForgeClient(
        "http://ff.test",
        "ffk_test",
        transport=httpx.MockTransport(handler),
        backoff_base=0.001,
        backoff_max=0.01,
        **kw,
    )


def test_submit_sends_auth_idempotency_key_and_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=TASK)

    job = client(handler).submit("generate_report", {"report_id": 42}, priority=5, max_retries=3)
    assert job.id == TASK["id"] and job.payload == {"report_id": 42} and not job.is_terminal
    req = seen[0]
    assert req.url.path == "/api/v1/tasks"
    assert req.headers["authorization"] == "Bearer ffk_test"
    assert req.headers["idempotency-key"].startswith("sdk-")
    body = json.loads(req.content)
    assert body == {
        "task_type": "generate_report",
        "payload": {"report_id": 42},
        "dead_letter": True,
        "priority": 5,
        "max_retries": 3,
    }


def test_submit_retries_with_the_same_idempotency_key() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return (
            error(503, "service_unavailable", "down")
            if len(keys) < 3
            else httpx.Response(201, json=TASK)
        )

    assert client(handler).submit("generate_report", {}).id == TASK["id"]
    assert len(keys) == 3 and len(set(keys)) == 1  # retried safely: one logical request


def test_explicit_idempotency_key_is_used() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"] == "order-7"
        return httpx.Response(200, json=TASK)

    client(handler).submit("generate_report", {}, idempotency_key="order-7")


def test_rate_limit_honours_retry_after_then_raises() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return error(429, "rate_limited", "slow down", headers={"Retry-After": "0"})

    with pytest.raises(RateLimitError) as exc:
        client(handler, max_retries=2).get("x")
    assert calls == 3 and exc.value.retry_after == 0 and exc.value.request_id == "req-1"


def test_non_idempotent_posts_are_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return error(503, "service_unavailable", "down")

    with pytest.raises(ServerError):
        client(handler).cancel("t1")
    assert calls == 1


def test_transport_errors_are_retried_then_raised() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransportError):
        client(handler, max_retries=2).get("t1")
    assert calls == 3


@pytest.mark.parametrize(
    ("status", "code", "exc_type"),
    [
        (404, "not_found", NotFoundError),
        (409, "conflict", ConflictError),
        (422, "validation_failed", ValidationError),
    ],
)
def test_errors_are_mapped_to_typed_exceptions(
    status: int, code: str, exc_type: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return error(status, code, "nope")

    with pytest.raises(exc_type) as exc:
        client(handler).get("t1")
    assert "req-1" in str(exc.value)


def test_iter_tasks_follows_cursors() -> None:
    pages = {
        None: {"items": [TASK, {**TASK, "id": "2"}], "next_cursor": "c1", "has_more": True},
        "c1": {"items": [{**TASK, "id": "3"}], "next_cursor": None, "has_more": False},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list("state") == ["QUEUED", "RUNNING"]
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    ids = [t.id for t in client(handler).iter_tasks(state=["QUEUED", "RUNNING"], limit=2)]
    assert ids == [TASK["id"], "2", "3"]


def test_wait_polls_until_terminal_and_times_out() -> None:
    states = iter(["QUEUED", "RUNNING", "SUCCEEDED"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**TASK, "state": next(states), "result": {"ok": True}})

    done = client(handler).wait("t1", poll_interval=0.001)
    assert done.succeeded and done.result == {"ok": True}

    def stuck(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TASK)

    with pytest.raises(TaskWaitTimeout):
        client(stuck).wait("t1", timeout=0.05, poll_interval=0.01)


def test_api_key_is_required() -> None:
    with pytest.raises(ValueError, match="api_key"):
        FlowForgeClient("http://ff.test", "")


async def test_async_client_submit_and_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return error(502, "bad_gateway", "x") if attempts == 1 else httpx.Response(201, json=TASK)

    async with AsyncFlowForgeClient(
        "http://ff.test",
        "ffk_test",
        transport=httpx.MockTransport(handler),
        backoff_base=0.001,
        backoff_max=0.01,
    ) as c:
        job = await c.submit("generate_report", {"report_id": 1})
    assert job.id == TASK["id"] and attempts == 2
