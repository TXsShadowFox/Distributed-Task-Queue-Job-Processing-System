# syntax=docker/dockerfile:1.7
# One image for every Python process (API, worker, scheduler, migrations, admin CLI); compose picks the command.
#   docker build -f infrastructure/docker/backend.Dockerfile -t flowforge-backend .

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

# ------------------------------------------------------------------------------------------------ build
FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first (cached until the lockfile changes), then the workspace packages themselves.
COPY pyproject.toml uv.lock ./
COPY packages/shared-schemas/pyproject.toml packages/shared-schemas/
COPY packages/core/pyproject.toml packages/core/
COPY packages/python-sdk/pyproject.toml packages/python-sdk/
COPY apps/api/pyproject.toml apps/api/
COPY services/worker/pyproject.toml services/worker/
COPY services/scheduler/pyproject.toml services/scheduler/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --all-packages

COPY packages packages
COPY apps/api apps/api
COPY services services
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --all-packages

# ------------------------------------------------------------------------------------------------ runtime
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="flowforge-backend" \
      org.opencontainers.image.description="FlowForge API, worker, scheduler and admin CLI"

RUN groupadd --system --gid 10001 flowforge \
    && useradd --system --uid 10001 --gid flowforge --no-create-home --shell /usr/sbin/nologin flowforge

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLOWFORGE_LOG_JSON=true \
    FLOWFORGE_API_HOST=0.0.0.0

USER flowforge
WORKDIR /home/flowforge
EXPOSE 8000 9101 9102

# No secrets are baked in: database, Redis and JWT settings come from the environment at run time.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('FLOWFORGE_API_PORT', '8000')}/health/live\", timeout=2)"]
CMD ["flowforge-api"]
