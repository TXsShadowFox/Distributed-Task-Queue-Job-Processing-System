# Known issues and limitations

Each entry says what is affected, why, and what to do about it. Severity: **high** (data loss, security exposure or
broken core flow), **medium** (degraded behaviour with a workaround), **low** (cosmetic or narrow).

## Verification gaps

| Issue | Severity | Details |
|---|---|---|
| Docker Compose stack not started locally | medium | The development machine has no Docker. `docker-compose.yml`, both Dockerfiles, nginx, Prometheus and Grafana were written and checked for syntax (YAML/JSON parsing), and CI builds the images and runs the Playwright suite against Compose. Until that CI run has passed, treat the container path as **implemented but not verified**. The native stack (`scripts/devstack.py`) runs the same code and is what every test here used. |
| CI workflow not executed | medium | `.github/workflows/ci.yml` has not run, because the repository has not been pushed. It passes `actionlint` 1.7.12 with no findings (shellcheck was not installed, so the inline `run:` scripts were not linted). Every command it runs was run locally, except `docker` and the Linux-specific `apt` installs done by `playwright install --with-deps`. |
| Workload and load benchmarks incomplete | medium | The no-op matrix is complete, but the I/O-bound and CPU-bound matrices, the full crash-recovery runs and the Locust load test have not run in the final configuration. One attempt was spoiled by the laptop's standby, and it then ran on battery. Smoke-scale runs of the CPU-bound workload and of recovery exist; see the [performance report](performance-report.md). Run `make bench-workloads` and the load test on mains power, about 20 minutes. |
| `make` targets not run here | low | The Windows machine has no `make`. Each target wraps a command that was run directly (see [test results](test-results.md)). |
| Benchmarks from one Windows laptop | medium | Python's asyncio on Windows (Proactor event loop), PostgreSQL on Windows and Redis inside WSL are all slower than a Linux server. The numbers in [benchmarks](../benchmarks/results.md) are real but represent this machine, not production hardware. PostgreSQL here opens new connections one at a time, about 0.11 s each, which makes pool sizing matter more than usual. Per-task CPU also varied between sessions on the same machine (about 7 to 14 ms), which the [performance report](performance-report.md) records. |

## Product limitations

| Issue | Severity | Details |
|---|---|---|
| At-least-once execution | by design | A handler can run more than once after a crash or partition. Handlers must be idempotent (they receive `ctx.idempotency_key` and `ctx.attempt`). See [ADR 0004](../decisions/0004-at-least-once-delivery.md). |
| Worker throughput per process | medium | About 140 no-op tasks/s per worker process on the benchmark machine (75–120 in a slower session), bounded by Python CPU: about 7 ms per task (12–14 ms in that session), mostly SQLAlchemy and asyncio overhead. Scale with more worker processes. Moving the claim and complete hot path to raw asyncpg statements is the next optimisation to evaluate. |
| Entries that cannot be claimed are retried indefinitely | low (deliberate) | If claiming a task fails with a *non-transient* error (a bug in a release, or an unexpected database error), the entry stays pending and is retried every `worker_reclaim_min_idle_ms` (60 s) until the cause is fixed. No task is lost, failed or run twice. A delivery-count limit that parks such entries was rejected: one bad release would park or fail every task. Instead each failure increments `flowforge_worker_handler_crashes_total`, logs `task_supervisor_crashed`, and fires the critical `FlowForgeWorkerHandlerCrashes` alert. The cost is log noise and one wasted reclaim per entry per minute while it lasts. |
| Partial Redis data loss | low | The reconciler re-dispatches `QUEUED` tasks once their queue band's stream is empty. If Redis lost only some entries, those tasks wait until the band drains (see [reliability](../architecture/reliability.md)). |
| No email | low | No email is sent: no invitations, password reset or alert mail. Members must register before they are added; admins reset passwords with `flowforge-admin`; alerts go through Prometheus. |
| Content Security Policy allows inline scripts | low | Next.js and the theme bootstrap emit inline scripts, so the nginx CSP uses `script-src 'self' 'unsafe-inline'`. Nonce-based CSP would need per-request rendering. Other directives are restrictive (`connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`). |
| Session restore costs a refresh per full page load | low | The access token lives in memory only (safer against XSS), so every full reload calls `/auth/refresh`, which shares the per-IP auth rate limit (30/min by default) with login. In-app navigation needs no refresh. Raise the limit for heavy automated testing (the E2E README does). |
| Workflow builder on phones | low | The builder works on small screens (stacked panels, pinch-zoom on the canvas) but is designed for desktop. |
| Task logs | low | Up to 2,000 lines are shown per task, and a handler's lines per attempt are capped (`MAX_LOG_LINES`); the dashboard says when it truncates. |
| Single-region | by design | PostgreSQL is the single source of truth. Use a replicated or managed PostgreSQL for availability ([deployment](../deployment.md)). |

## Development-environment notes

* Windows Smart App Control blocks unsigned Redis binaries, so the native stack runs Redis in a WSL distribution
  (`flowforge-dev`). Remove it with `wsl --unregister flowforge-dev`.
* Use `127.0.0.1` instead of `localhost` in connection strings on Windows. `localhost` resolves to `::1` first, and
  the WSL port relay listens only on IPv4, which adds about 2 s to every new connection.
* npm scripts break when the working path contains `&`, because cmd.exe treats it as a command separator. Set
  `npm_config_script_shell` to Git Bash, as the repository's instructions describe.
