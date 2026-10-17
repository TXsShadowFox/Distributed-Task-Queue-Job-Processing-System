"""Structured JSON logging (structlog) with correlation context and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import orjson
import structlog
from structlog.typing import EventDict, WrappedLogger

_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|authorization|api[_-]?key|cookie|set-cookie|credential)",
    re.IGNORECASE,
)
# Raw FlowForge API keys and bearer/JWT-looking strings are masked even inside free-text messages.
_SECRET_PATTERNS = [
    re.compile(r"ffk_[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
]
REDACTED = "[REDACTED]"


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def redact_mapping(data: MutableMapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(key, str) and _SENSITIVE_KEYS.search(key):
            out[key] = REDACTED
        else:
            out[key] = _redact_value(value)
    return out


def _redact_processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    return redact_mapping(event_dict)


def _orjson_dumps(obj: Any, default: Any = None) -> str:
    return orjson.dumps(obj, default=default or str).decode()


def configure_logging(*, service: str, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog and route stdlib logging (uvicorn, sqlalchemy, …) through the same pipeline."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_service(service),
        _redact_processor,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer(serializer=_orjson_dumps)
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    # Windows consoles default to a legacy code page; structured logs are UTF-8 (otherwise lines with non-ASCII
    # characters would fail to write and be lost).
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure) and (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        reconfigure(encoding="utf-8", errors="backslashreplace")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level)))
    logging.getLogger("uvicorn.error").propagate = True


def _add_service(service: str) -> Any:
    def processor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return processor


def get_logger(name: str | None = None) -> Any:
    return structlog.stdlib.get_logger(name)


bind_context = structlog.contextvars.bind_contextvars
clear_context = structlog.contextvars.clear_contextvars
unbind_context = structlog.contextvars.unbind_contextvars
bound_context = structlog.contextvars.bound_contextvars
