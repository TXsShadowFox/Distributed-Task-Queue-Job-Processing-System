"""Typed configuration loaded from environment variables (prefix ``FLOWFORGE_``) and an optional ``.env`` file.

Secrets have no defaults: the services refuse to start without ``FLOWFORGE_JWT_SECRET`` instead of silently
running with a well-known value.
"""

from __future__ import annotations

import socket
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from flowforge_shared.priority import DEFAULT_QUEUE, validate_queue_name


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ general
    environment: Literal["development", "test", "production"] = "development"
    service_name: str = "flowforge"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    # ------------------------------------------------------------------ datastores
    database_url: str = "postgresql+asyncpg://flowforge:flowforge@localhost:5432/flowforge"
    database_pool_size: int = Field(default=10, ge=1, le=200)
    database_max_overflow: int = Field(default=10, ge=0, le=200)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    database_statement_timeout_ms: int = Field(default=30_000, ge=0)
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    redis_prefix: str = Field(default="ff", pattern=r"^[a-z][a-z0-9_-]{0,15}$")

    # ------------------------------------------------------------------ security
    jwt_secret: SecretStr
    jwt_issuer: str = "flowforge"
    jwt_audience: str = "flowforge-api"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    refresh_token_ttl_seconds: int = Field(default=14 * 86_400, ge=3600)
    cookie_secure: bool = True
    cookie_samesite: Literal["strict", "lax"] = "strict"
    cookie_domain: str | None = None
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    allow_registration: bool = True
    #: A user registering with this email is made a platform admin (can drain shared workers).
    bootstrap_admin_email: str | None = None
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_seconds: int = Field(default=300, ge=1)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=1200, ge=1)
    auth_rate_limit_per_minute: int = Field(default=30, ge=1)
    max_request_bytes: int = Field(default=1024 * 1024, ge=1024)
    max_payload_bytes: int = Field(default=256 * 1024, ge=1024)
    api_key_rotation_grace_seconds: int = Field(default=86_400, ge=0)
    metrics_token: SecretStr | None = None
    trusted_proxy_count: int = Field(default=0, ge=0, le=5)

    # ------------------------------------------------------------------ worker
    worker_name: str = Field(default_factory=socket.gethostname)
    worker_queues: Annotated[list[str], NoDecode] = Field(default_factory=lambda: [DEFAULT_QUEUE])
    worker_concurrency: int = Field(default=8, ge=1, le=1000)
    worker_cpu_processes: int = Field(default=2, ge=0, le=64)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0)
    worker_shutdown_grace_seconds: float = Field(default=30.0, ge=0)
    worker_reclaim_interval_seconds: float = Field(default=15.0, gt=0)
    worker_reclaim_min_idle_ms: int = Field(default=60_000, ge=1000)
    worker_poll_block_ms: int = Field(default=1000, ge=10, le=30_000)
    worker_exit_when_drained: bool = False
    worker_metrics_port: int = Field(default=9101, ge=0, le=65535)
    lease_ttl_seconds: float = Field(default=30.0, ge=2)

    # ------------------------------------------------------------------ scheduler / control plane
    scheduler_poll_interval_seconds: float = Field(default=0.5, gt=0)
    scheduler_batch_size: int = Field(default=500, ge=1, le=10_000)
    reaper_interval_seconds: float = Field(default=5.0, gt=0)
    worker_offline_after_seconds: float = Field(default=30.0, gt=0)
    reconcile_interval_seconds: float = Field(default=30.0, gt=0)
    reconcile_grace_seconds: float = Field(default=60.0, ge=0)
    retention_interval_seconds: float = Field(default=3600.0, gt=0)
    retention_days: int = Field(default=30, ge=1)
    outbox_retention_hours: int = Field(default=24, ge=0)
    scheduler_metrics_port: int = Field(default=9102, ge=0, le=65535)

    # ------------------------------------------------------------------ observability
    otel_exporter_otlp_endpoint: str | None = None
    otel_sample_ratio: float = Field(default=1.0, ge=0, le=1)

    @field_validator("cors_origins", "worker_queues", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("worker_queues")
    @classmethod
    def _validate_queues(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one worker queue is required")
        return [validate_queue_name(q) for q in value]

    @field_validator("jwt_secret")
    @classmethod
    def _validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("FLOWFORGE_JWT_SECRET must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("cookie_secure must be true in production")
            if any(o == "*" for o in self.cors_origins):
                raise ValueError("wildcard CORS origins are not allowed in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
