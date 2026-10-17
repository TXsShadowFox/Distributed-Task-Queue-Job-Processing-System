# 0012. A native development stack that does not need Docker

**Status:** Accepted

## Context

The primary development machine has no Docker, and Windows Smart App Control blocks unsigned Redis binaries. The
platform still had to be built and tested against real PostgreSQL and Redis, not mocks.

## Decision

`scripts/devstack.py` runs a portable PostgreSQL natively and Redis inside a minimal WSL distribution. It generates
`.env` with random secrets and creates the development and test databases. Tests can also use CI service containers
or Testcontainers. Docker Compose remains the deployment path.

## Consequences

* Every test in this repository runs against real services.
* The Compose stack is validated by CI (config check, image builds, end-to-end tests against Compose), not on the
  development machine. See [known issues](../evaluation/known-issues.md).
