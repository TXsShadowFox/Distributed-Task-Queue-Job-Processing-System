# Deployment guide

This guide takes FlowForge from `docker compose up` on one machine to a production deployment. Everything runs from
two images: `flowforge-backend` (API, worker, scheduler, migrations and the admin CLI) and `flowforge-web` (the
dashboard).

> **Verification status.** The Compose file, Dockerfiles, nginx, Prometheus and Grafana configuration are checked by
> CI (`docker compose config`, image builds, and the Playwright suite against the Compose stack). The development
> machine used to build FlowForge has no Docker, so the Compose stack itself was not started there. Everything else
> in this guide was exercised with the native stack. See [known issues](evaluation/known-issues.md).

## 1. Single machine with Docker Compose

```bash
cp .env.example .env
# Replace every __GENERATE__ with a random value, for example: openssl rand -hex 32
# (python scripts/devstack.py init-env does this for you)
docker compose up --build -d
docker compose ps                    # all services healthy
open http://localhost:8080           # register; FLOWFORGE_BOOTSTRAP_ADMIN_EMAIL becomes platform admin
```

The `migrate` service applies database migrations and exits. The API, worker and scheduler wait for it. Optional
observability:

```bash
docker compose --profile observability up -d
# Prometheus  http://127.0.0.1:9090  (scrapes every API, worker and scheduler replica, with alert rules)
# Grafana     http://127.0.0.1:3001  (admin / GRAFANA_ADMIN_PASSWORD; the FlowForge dashboard is provisioned)
# Jaeger      http://127.0.0.1:16686 (set FLOWFORGE_OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 in .env)
```

Only nginx publishes a port (8080). PostgreSQL, Redis and the metrics endpoints stay on the internal network, and
the observability UIs bind to 127.0.0.1.

## 2. Configuration

Every `FLOWFORGE_*` variable maps to a field of `flowforge_core.config.Settings`. The defaults are production-safe;
these are the ones to decide on:

| Variable | Default | Notes |
|---|---|---|
| `FLOWFORGE_ENVIRONMENT` | `development` | `production` refuses to start with insecure cookies or wildcard CORS origins (a JWT secret of at least 32 characters is required in every environment) |
| `FLOWFORGE_DATABASE_URL` | – | `postgresql+asyncpg://…` |
| `FLOWFORGE_REDIS_URL` | – | `redis://host:6379/0` (`rediss://` for TLS) |
| `FLOWFORGE_JWT_SECRET` | – | At least 32 random characters. Changing it signs everyone out |
| `FLOWFORGE_COOKIE_SECURE` | `true` | Keep `true` behind HTTPS |
| `FLOWFORGE_CORS_ORIGINS` | localhost | Only needed if the dashboard is served from a different origin than the API |
| `FLOWFORGE_TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies in front of the API (Compose: `1`, for nginx). Controls which `X-Forwarded-For` hop is trusted for rate limiting and audit IPs |
| `FLOWFORGE_ALLOW_REGISTRATION` | `true` | Set `false` once your users exist; add people under Settings → Members |
| `FLOWFORGE_BOOTSTRAP_ADMIN_EMAIL` | – | The account that registers with this email becomes platform admin. `flowforge-admin promote EMAIL` does it later |
| `FLOWFORGE_METRICS_TOKEN` | empty | If set, `GET /metrics` requires `Authorization: Bearer <token>` |
| `FLOWFORGE_WORKER_QUEUES` | `default` | Comma-separated queues this worker consumes |
| `FLOWFORGE_WORKER_CONCURRENCY` | `8` | Execution slots per worker process |
| `FLOWFORGE_WORKER_CPU_PROCESSES` | `2` | Process-pool size for CPU-bound task types |
| `FLOWFORGE_LEASE_TTL_SECONDS` | `30` | Crash-detection time versus heartbeat tolerance |
| `FLOWFORGE_RETENTION_DAYS` | `30` | Finished tasks and runs older than this are deleted. Overridable per project |
| `FLOWFORGE_OTEL_EXPORTER_OTLP_ENDPOINT` | empty | OTLP/HTTP endpoint for traces |

## 3. HTTPS

Terminate TLS in front of nginx (a cloud load balancer, Caddy, Traefik, or nginx itself with your certificates).
Then:

1. Add `Strict-Transport-Security "max-age=31536000; includeSubDomains"` (there is a commented line in
   `infrastructure/nginx/nginx.conf`).
2. Keep `FLOWFORGE_COOKIE_SECURE=true`.
3. Set `FLOWFORGE_TRUSTED_PROXY_COUNT` to the number of proxies that add `X-Forwarded-For` (load balancer + nginx = 2).
4. Use `rediss://` and `sslmode=require` (asyncpg: `?ssl=require`) when Redis and PostgreSQL are on other hosts.

## 4. Secrets

* Never bake secrets into images. Compose reads `.env`; in Kubernetes or a cloud platform use its secret store
  and expose the values as environment variables.
* Rotating `FLOWFORGE_JWT_SECRET` invalidates all sessions; people sign in again.
* API keys are stored as SHA-256 hashes. Rotate a key from the dashboard with a grace period, then revoke the old one.
* The audit log records security-relevant actions without secrets (the logger also redacts keys such as `password`,
  `token`, `secret` and `authorization`).

## 5. Scaling

* **Workers.** `docker compose up -d --scale worker=N`, or run more replicas elsewhere. Workers coordinate only through
  PostgreSQL and Redis. Measured throughput grows with worker processes; see [benchmarks](benchmarks/results.md).
  Dedicate workers to queues with `FLOWFORGE_WORKER_QUEUES`, for example a `reports` pool apart from `default`.
* **API.** Stateless; add replicas behind nginx or a load balancer. SSE connections are long-lived, so size proxy
  timeouts and file descriptors accordingly.
* **Scheduler.** Run two replicas for availability. Every loop is safe with several replicas (`SKIP LOCKED`,
  idempotent fires).
* **PostgreSQL connections.** Each process keeps up to `FLOWFORGE_DATABASE_POOL_SIZE` connections open and opens up
  to `FLOWFORGE_DATABASE_MAX_OVERFLOW` more under load. Give each worker a pool of at least its concurrency + 2 (one
  connection per execution slot, plus the heartbeat and the log flush). A smaller pool keeps opening and closing
  overflow connections, which cost about 0.1 s each on the benchmark machine; the worker logs
  `database_pool_smaller_than_concurrency` at start-up when this applies. Workers open their pool before they take
  work, so a new replica is ready at full speed. Keep the total over all processes under PostgreSQL's
  `max_connections` (300 in the Compose file). A pooler such as PgBouncer in transaction mode must support
  prepared statements, because asyncpg uses them (PgBouncer 1.21 or later with `max_prepared_statements` set). That
  setup has not been tested here.

## 6. Backups and restore

PostgreSQL holds all durable state. Redis needs no backup: the reconciler rebuilds dispatches after a Redis loss.

```bash
# Logical backup (small and medium databases)
docker compose exec -T postgres pg_dump -U flowforge -Fc flowforge > flowforge-$(date +%F).dump
# Restore into an empty database
docker compose exec -T postgres pg_restore -U flowforge -d flowforge --clean --if-exists < flowforge-2026-10-14.dump
```

For production, use continuous archiving with point-in-time recovery (pgBackRest, WAL-G, or your provider's managed
backups), and test restores regularly. After a restore, workers re-dispatch `QUEUED` tasks automatically. Tasks that
were `RUNNING` at backup time are recovered by the reaper once their leases expire.

## 7. Upgrades and rollback

1. Back up PostgreSQL.
2. Deploy the new images. The `migrate` step (`flowforge-admin migrate`) runs before the services start.
3. Workers shut down gracefully on `SIGTERM`: they stop reading, finish or hand back running tasks within
   `FLOWFORGE_WORKER_SHUTDOWN_GRACE_SECONDS`, and nothing is lost. Compose gives them 45 s.

To roll back, deploy the previous images. If the new release included a migration, first run
`flowforge-admin downgrade <previous revision>` (`flowforge-admin current` shows the applied revision). Downgrades are
written for every migration, but a downgrade that drops a column loses that column's data, so prefer restoring the
backup taken in step 1 when in doubt.

## 8. Monitoring

* **Health.** `GET /health/live` (process up) and `GET /health/ready` (PostgreSQL and Redis reachable) for load
  balancers and orchestrators.
* **Metrics.** Prometheus format from the API (`/metrics`), workers (`:9101`) and scheduler (`:9102`): throughput,
  outcomes, execution and queue-wait histograms, stream backlog, worker slots, outbox backlog, recovery activity,
  HTTP latency by route, SSE connections, rate limiting.
* **Alerts.** `infrastructure/prometheus/alerts.yml` covers: no active workers, a growing backlog, a stuck outbox, a
  stalled scheduler loop, dead-letter spikes, lease expiry spikes, and API 5xx rate.
* **Logs.** JSON lines on stdout with `request_id`, `task_id` and `trace_id` correlation. Secrets are redacted.
* **Traces.** OpenTelemetry spans for API requests and task executions, with context propagated from submission
  through the stream to the worker.

## 9. Operations cheat-sheet

```bash
flowforge-admin migrate | current | downgrade REV
flowforge-admin promote alice@example.com          # platform admin: may drain or resume shared workers
flowforge-admin create-admin ops@example.com --name "Ops"   # password from FLOWFORGE_ADMIN_PASSWORD or a prompt
docker compose exec worker flowforge-admin current  # the CLI is in the backend image
```

Drain a worker before maintenance from the Workers page (platform admins). It stops taking work, finishes what it is
running, and hands queued entries back to other workers.
