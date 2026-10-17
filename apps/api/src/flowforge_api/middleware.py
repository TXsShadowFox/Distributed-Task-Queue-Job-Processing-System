"""Pure-ASGI middleware (no BaseHTTPMiddleware overhead, streaming-safe for SSE)."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import orjson
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flowforge_core.context import RequestInfo, current_request
from flowforge_core.logs import bind_context, clear_context, get_logger
from flowforge_core.observability import metrics

log = get_logger("flowforge_api.access")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def client_ip(scope: Scope, trusted_proxy_count: int) -> str | None:
    """Honour X-Forwarded-For only for the configured number of trusted proxy hops (spoofing protection)."""
    if trusted_proxy_count > 0:
        xff = _header(scope, b"x-forwarded-for")
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if len(hops) >= trusted_proxy_count:
                return hops[-trusted_proxy_count]
    client = scope.get("client")
    return str(client[0]) if client else None


_PATH_PARAM = re.compile(r"{([^}:]+)(:[^}]+)?}")


def route_template(scope: Scope) -> str:
    """Full route template (e.g. ``/api/v1/tasks/{task_id}``) for metric labels — never the raw path, which would
    explode label cardinality. FastAPI keeps nested routers unflattened, so the matched route's path is relative to
    its router; the router prefix is recovered by matching the concrete suffix against the request path."""
    raw_template = getattr(scope.get("route"), "path", None)
    if not raw_template:
        return "unmatched"
    template = str(raw_template)
    params = scope.get("path_params") or {}
    concrete = _PATH_PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), template)
    path: str = scope.get("path", "")
    if concrete and path.endswith(concrete):
        return path[: len(path) - len(concrete)] + template
    return template


class RequestContextMiddleware:
    """Request id, client info, structured access log and latency metrics (labelled by route *template*)."""

    def __init__(self, app: ASGIApp, *, trusted_proxy_count: int = 0) -> None:
        self.app = app
        self.trusted_proxy_count = trusted_proxy_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id")
        request_id = (
            incoming if incoming and _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        )
        info = RequestInfo(
            request_id=request_id,
            ip=client_ip(scope, self.trusted_proxy_count),
            user_agent=(_header(scope, b"user-agent") or "")[:255] or None,
        )
        token = current_request.set(info)
        clear_context()
        bind_context(request_id=request_id)
        start = time.perf_counter()
        status_code = 500
        metrics.HTTP_IN_FLIGHT.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            metrics.HTTP_IN_FLIGHT.dec()
            elapsed = time.perf_counter() - start
            template = route_template(scope)
            if template not in ("/metrics", "/health/live", "/health/ready"):
                metrics.HTTP_REQUEST_SECONDS.labels(
                    scope["method"], template, f"{status_code // 100}xx"
                ).observe(elapsed)
                log.info(
                    "http_request",
                    method=scope["method"],
                    path=template,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 2),
                    ip=info.ip,
                )
            current_request.reset(token)
            clear_context()


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_docs = path.startswith(("/docs", "/redoc"))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                ]
                if not is_docs:
                    headers.append(
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
                    )
                if self.hsts:
                    headers.append(
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Rejects bodies above ``max_bytes`` — by Content-Length up front, and while streaming for chunked uploads."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(send, self.max_bytes)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _too_large(send, self.max_bytes)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send: Send, limit: int) -> None:
    body: dict[str, Any] = {
        "error": {
            "code": "payload_too_large",
            "message": f"request body exceeds {limit} bytes",
            "details": None,
            "request_id": None,
        }
    }
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": orjson.dumps(body)})
