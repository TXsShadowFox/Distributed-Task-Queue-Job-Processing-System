"""FlowForge API clients (synchronous and asyncio).

Reliability features:
* Bounded retries with exponential backoff + jitter on network errors, 429 (honouring ``Retry-After``) and
  502/503/504.
* POST requests are retried only when they are idempotent. ``submit`` therefore sends an ``Idempotency-Key``: the
  caller's, or one generated once per call and reused across that call's retries, so a lost response never creates a
  duplicate task.
* Explicit connect/read timeouts; typed exceptions carrying the server's ``request_id``.
"""

from __future__ import annotations

import asyncio
import builtins
import random
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import datetime
from typing import Any

import httpx

from flowforge_sdk import __version__
from flowforge_sdk.errors import FlowForgeError, TaskWaitTimeout, TransportError, error_for
from flowforge_sdk.models import BatchItem, Task, TaskPage

RETRY_STATUSES = frozenset({429, 502, 503, 504})
DEFAULT_TIMEOUT = 10.0


def _submit_body(
    task_type: str,
    payload: Mapping[str, Any] | None,
    *,
    queue: str | None,
    priority: int | None,
    max_retries: int | None,
    timeout_seconds: int | None,
    delay_seconds: float | None,
    run_at: datetime | None,
    metadata: Mapping[str, Any] | None,
    dead_letter: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task_type": task_type,
        "payload": dict(payload or {}),
        "dead_letter": dead_letter,
    }
    optional = {
        "queue": queue,
        "priority": priority,
        "max_retries": max_retries,
        "timeout_seconds": timeout_seconds,
        "delay_seconds": delay_seconds,
        "run_at": run_at.isoformat() if run_at else None,
        "metadata": dict(metadata) if metadata else None,
    }
    body.update({k: v for k, v in optional.items() if v is not None})
    return body


def _list_params(
    state: str | list[str] | None,
    task_type: str | None,
    queue: str | None,
    limit: int,
    cursor: str | None,
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("limit", str(limit))]
    states = [state] if isinstance(state, str) else list(state or [])
    params += [("state", s) for s in states]
    if task_type:
        params.append(("task_type", task_type))
    if queue:
        params.append(("queue", queue))
    if cursor:
        params.append(("cursor", cursor))
    return params


def _page(data: dict[str, Any]) -> TaskPage:
    return TaskPage(
        items=[Task.from_json(t) for t in data["items"]],
        next_cursor=data.get("next_cursor"),
        has_more=bool(data.get("has_more")),
        total=data.get("total"),
    )


class _Base:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._base_url = base_url.rstrip("/") + "/api/v1"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"flowforge-sdk-python/{__version__}",
            "Accept": "application/json",
        }
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))
        self.max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

    def _delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                return min(float(retry_after), self._backoff_max)
        cap: float = min(self._backoff_max, self._backoff_base * float(2**attempt))
        return cap / 2 + random.uniform(0, cap / 2)  # nosec B311 - retry jitter, not security

    @staticmethod
    def _retryable(method: str, headers: Mapping[str, str]) -> bool:
        return method in ("GET", "HEAD", "PUT", "DELETE") or "Idempotency-Key" in headers

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
        except ValueError:
            body = None
        retry_after = response.headers.get("retry-after")
        raise error_for(
            response.status_code,
            body,
            float(retry_after)
            if retry_after and retry_after.replace(".", "", 1).isdigit()
            else None,
        )


class FlowForgeClient(_Base):
    """Synchronous client.

    >>> client = FlowForgeClient(base_url="http://localhost:8000", api_key="ffk_...")
    >>> job = client.submit(task_type="generate_report", payload={"report_id": 42}, priority=5, max_retries=3)
    >>> client.wait(job.id).state
    'SUCCEEDED'
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        **kwargs: float,
    ) -> None:
        super().__init__(base_url, api_key, timeout=timeout, max_retries=max_retries, **kwargs)
        self._http = httpx.Client(timeout=self._timeout, headers=self._headers, transport=transport)

    def __enter__(self) -> FlowForgeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kw: Any
    ) -> Any:
        headers = headers or {}
        last_error: FlowForgeError | None = None
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._http.request(method, self._base_url + path, headers=headers, **kw)
            except httpx.TransportError as exc:
                last_error = TransportError(f"{type(exc).__name__}: {exc}")
            else:
                if response.status_code not in RETRY_STATUSES:
                    self._raise_for(response)
                    return response.json() if response.content else None
                try:
                    self._raise_for(response)
                except FlowForgeError as exc:
                    last_error = exc
            if attempt >= self.max_retries or not self._retryable(method, headers):
                break
            time.sleep(self._delay(attempt, response))
        raise last_error or FlowForgeError(f"{method} {path} failed")

    # ------------------------------------------------------------------------------------------------ tasks
    def submit(
        self,
        task_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        timeout_seconds: int | None = None,
        delay_seconds: float | None = None,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        dead_letter: bool = True,
    ) -> Task:
        body = _submit_body(
            task_type,
            payload,
            queue=queue,
            priority=priority,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            delay_seconds=delay_seconds,
            run_at=run_at,
            metadata=metadata,
            dead_letter=dead_letter,
        )
        key = idempotency_key or f"sdk-{uuid.uuid4()}"
        return Task.from_json(
            self._request("POST", "/tasks", json=body, headers={"Idempotency-Key": key})
        )

    def submit_batch(self, tasks: builtins.list[Mapping[str, Any]]) -> builtins.list[BatchItem]:
        """Submit up to 1,000 tasks atomically. Items may carry their own ``idempotency_key`` for safe retries."""
        data = self._request("POST", "/tasks/batch", json={"tasks": [dict(t) for t in tasks]})
        return [BatchItem(**item) for item in data["items"]]

    def get(self, task_id: str) -> Task:
        return Task.from_json(self._request("GET", f"/tasks/{task_id}"))

    def list(
        self,
        *,
        state: str | builtins.list[str] | None = None,
        task_type: str | None = None,
        queue: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TaskPage:
        return _page(
            self._request(
                "GET", "/tasks", params=_list_params(state, task_type, queue, limit, cursor)
            )
        )

    def iter_tasks(self, **filters: Any) -> Iterator[Task]:
        """Iterate every matching task, following pagination cursors."""
        cursor = None
        while True:
            page = self.list(cursor=cursor, **filters)
            yield from page.items
            if not page.has_more or not page.next_cursor:
                return
            cursor = page.next_cursor

    def cancel(self, task_id: str) -> Task:
        return Task.from_json(self._request("POST", f"/tasks/{task_id}/cancel"))

    def retry(self, task_id: str) -> Task:
        return Task.from_json(self._request("POST", f"/tasks/{task_id}/retry"))

    def attempts(self, task_id: str) -> builtins.list[dict[str, Any]]:
        result: builtins.list[dict[str, Any]] = self._request("GET", f"/tasks/{task_id}/attempts")
        return result

    def logs(self, task_id: str, *, after_id: int = 0) -> builtins.list[dict[str, Any]]:
        items: builtins.list[dict[str, Any]] = self._request(
            "GET", f"/tasks/{task_id}/logs", params={"after_id": after_id}
        )["items"]
        return items

    def wait(self, task_id: str, *, timeout: float = 60.0, poll_interval: float = 0.5) -> Task:
        """Poll until the task is terminal. Raises ``TaskWaitTimeout`` (the task itself keeps running)."""
        deadline = time.monotonic() + timeout
        while True:
            task = self.get(task_id)
            if task.is_terminal:
                return task
            if time.monotonic() >= deadline:
                raise TaskWaitTimeout(f"task {task_id} still {task.state} after {timeout}s")
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def task_types(self) -> builtins.list[dict[str, Any]]:
        result: builtins.list[dict[str, Any]] = self._request("GET", "/task-types")
        return result

    # ------------------------------------------------------------------------------------------------ workflows
    def execute_workflow(
        self,
        workflow_id: str,
        input: Mapping[str, Any] | None = None,
        *,
        version: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"input": dict(input or {})}
        if version is not None:
            body["version"] = version
        result: dict[str, Any] = self._request(
            "POST", f"/workflows/{workflow_id}/execute", json=body
        )
        return result

    def get_workflow_run(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", f"/workflow-runs/{run_id}")
        return result


class AsyncFlowForgeClient(_Base):
    """asyncio client with the same API as ``FlowForgeClient`` (methods are coroutines)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: float,
    ) -> None:
        super().__init__(base_url, api_key, timeout=timeout, max_retries=max_retries, **kwargs)
        self._http = httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers, transport=transport
        )

    async def __aenter__(self) -> AsyncFlowForgeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kw: Any
    ) -> Any:
        headers = headers or {}
        last_error: FlowForgeError | None = None
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self._http.request(
                    method, self._base_url + path, headers=headers, **kw
                )
            except httpx.TransportError as exc:
                last_error = TransportError(f"{type(exc).__name__}: {exc}")
            else:
                if response.status_code not in RETRY_STATUSES:
                    self._raise_for(response)
                    return response.json() if response.content else None
                try:
                    self._raise_for(response)
                except FlowForgeError as exc:
                    last_error = exc
            if attempt >= self.max_retries or not self._retryable(method, headers):
                break
            await asyncio.sleep(self._delay(attempt, response))
        raise last_error or FlowForgeError(f"{method} {path} failed")

    async def submit(
        self,
        task_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        queue: str | None = None,
        priority: int | None = None,
        max_retries: int | None = None,
        timeout_seconds: int | None = None,
        delay_seconds: float | None = None,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        dead_letter: bool = True,
    ) -> Task:
        body = _submit_body(
            task_type,
            payload,
            queue=queue,
            priority=priority,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            delay_seconds=delay_seconds,
            run_at=run_at,
            metadata=metadata,
            dead_letter=dead_letter,
        )
        key = idempotency_key or f"sdk-{uuid.uuid4()}"
        return Task.from_json(
            await self._request("POST", "/tasks", json=body, headers={"Idempotency-Key": key})
        )

    async def submit_batch(
        self, tasks: builtins.list[Mapping[str, Any]]
    ) -> builtins.list[BatchItem]:
        data = await self._request("POST", "/tasks/batch", json={"tasks": [dict(t) for t in tasks]})
        return [BatchItem(**item) for item in data["items"]]

    async def get(self, task_id: str) -> Task:
        return Task.from_json(await self._request("GET", f"/tasks/{task_id}"))

    async def list(
        self,
        *,
        state: str | builtins.list[str] | None = None,
        task_type: str | None = None,
        queue: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TaskPage:
        params = _list_params(state, task_type, queue, limit, cursor)
        return _page(await self._request("GET", "/tasks", params=params))

    async def iter_tasks(self, **filters: Any) -> AsyncIterator[Task]:
        cursor = None
        while True:
            page = await self.list(cursor=cursor, **filters)
            for task in page.items:
                yield task
            if not page.has_more or not page.next_cursor:
                return
            cursor = page.next_cursor

    async def cancel(self, task_id: str) -> Task:
        return Task.from_json(await self._request("POST", f"/tasks/{task_id}/cancel"))

    async def retry(self, task_id: str) -> Task:
        return Task.from_json(await self._request("POST", f"/tasks/{task_id}/retry"))

    async def logs(self, task_id: str, *, after_id: int = 0) -> builtins.list[dict[str, Any]]:
        data = await self._request("GET", f"/tasks/{task_id}/logs", params={"after_id": after_id})
        items: builtins.list[dict[str, Any]] = data["items"]
        return items

    async def wait(
        self,
        task_id: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
    ) -> Task:
        deadline = time.monotonic() + timeout
        while True:
            task = await self.get(task_id)
            if task.is_terminal:
                return task
            if time.monotonic() >= deadline:
                raise TaskWaitTimeout(f"task {task_id} still {task.state} after {timeout}s")
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    async def execute_workflow(
        self,
        workflow_id: str,
        input: Mapping[str, Any] | None = None,
        *,
        version: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"input": dict(input or {})}
        if version is not None:
            body["version"] = version
        result: dict[str, Any] = await self._request(
            "POST", f"/workflows/{workflow_id}/execute", json=body
        )
        return result
