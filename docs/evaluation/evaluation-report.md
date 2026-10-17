# Final evaluation report

**Date:** 2026-10-14. **Scope:** the FlowForge build described in the project prompt (phases 0–8).
**Method:** every claim below is backed by a test run, a measurement or a file in this repository. Where something
was not executed, this report says so.

FlowForge is about 12,400 lines of Python (87 source files) and 9,700 lines of hand-written TypeScript, with 72 API
operations on 60 paths, 22 tables, 442 Python tests, 5 chaos tests, 57 dashboard tests and 15 browser tests.

## Feature status

**Implemented** means the code exists. **Tested** means an automated test exercises it against real services.
**Deployed** means it ran in a production-like environment. Nothing has been deployed to a server; everything ran on
the development machine.

| Area | Status | Evidence |
|---|---|---|
| Task submission, validation, idempotency, batches, priorities, delays | Implemented, tested | `test_task_lifecycle.py`, `tasks.spec.ts` |
| Redis Streams dispatch with transactional outbox, consumer groups, reclaim | Implemented, tested | `test_reliability.py` |
| Custom worker runtime (async, thread and process executors; leases; heartbeats; drain; cancel; logs; progress) | Implemented, tested | `test_reliability.py`, `test_task_lifecycle.py`, chaos tests |
| Retries with backoff, dead-letter queue, manual retry | Implemented, tested | `test_task_lifecycle.py`, `dead-letter.spec.ts` |
| Crash, partition and data-loss recovery | Implemented, tested with real process kills and TCP partitions | `tests/chaos` (5 scenarios) |
| Cron schedules with time zones, DST handling, exactly-once fires, misfire and overlap policies | Implemented, tested | `test_schedules.py`, `test_cron.py`, chaos scheduler test, `schedules-workers.spec.ts` |
| DAG workflows: editor, validation, versioning, execution, skip and resume, cancel | Implemented, tested | `test_workflows.py`, `test_dag.py`, `workflows.spec.ts` |
| Authentication, refresh rotation and reuse detection, lockout | Implemented, tested | `test_security.py`, `auth.spec.ts` |
| Organizations, projects, roles, tenant isolation, members | Implemented, tested | `test_security.py`, `test_organizations.py` |
| API keys (scopes, expiry, rotation with grace, revocation) | Implemented, tested | `test_security.py`, `api-keys.spec.ts` |
| Rate limiting, security headers, audit log | Implemented, tested | `test_security.py` |
| Dashboard: 10 pages plus auth, 404 and error pages | Implemented, tested against the production build | Playwright (15), Vitest (57), screenshot review |
| Realtime updates (SSE) and live log tail | Implemented, tested | `tasks.spec.ts` (live status), `test_observability.py` |
| Analytics (throughput, latency, failures, utilization) | Implemented, tested | `test_observability.py`, screenshots |
| Python SDK (sync and async) | Implemented, tested | `test_client.py`, `test_sdk.py` |
| Admin CLI (`flowforge-admin`) | Implemented, tested | `test_cli.py` |
| Prometheus metrics, alert rules, Grafana dashboard | Metrics implemented and tested (`test_observability.py`). **Alert rules and dashboard are partially verified**: syntax-checked, never loaded into Prometheus or Grafana | `infrastructure/prometheus`, `infrastructure/grafana` |
| OpenTelemetry tracing | Implemented, **partially verified**: enabled only with an OTLP endpoint, and no collector was run here | `observability/tracing.py` |
| Dockerfiles, Compose, nginx | Implemented, **partially verified**: YAML and JSON parse; images never built here | `infrastructure/`, `docker-compose.yml` |
| CI (GitHub Actions) | Implemented, **not run** (repository not pushed). Every command it runs was run locally except Docker | `.github/workflows/ci.yml` |
| Benchmarks | Implemented. **Measured:** the full no-op matrix with CPU per task, and before/after runs for the two fixes. **Partially measured:** a CPU-bound workload, memory and crash recovery, at smoke scale. **Not yet measured:** the I/O-bound and CPU-bound matrices and the API load test (`make bench-workloads`, Locust) | [results](../benchmarks/README.md), [performance report](performance-report.md) |

**Blocked:** Docker-based verification on this machine. Docker is not installed, and Windows Smart App Control blocks
the unsigned binaries such tools need. CI covers it once the repository is pushed.

## Completion gates (from the prompt)

| Gate | Status | Evidence |
|---|---|---|
| Core journeys pass end to end | ✅ | 14 functional Playwright tests against the production build, re-run after the final worker change |
| No known critical or high vulnerabilities | ✅ | pip-audit, bandit and npm audit (production) clean; dev-only advisory triaged ([security review](security-review.md)) |
| Required tests pass | ✅ | 442 Python, 5 chaos, 57 dashboard and 14 browser tests passing; the screenshot capture passed earlier ([test results](test-results.md)) |
| Worker crash recovery works | ✅ | `test_worker_killed_mid_task_is_recovered_by_another_worker` (SIGKILL) |
| Migrations run | ✅ | `flowforge-admin migrate` on fresh databases; the test suite downgrades and re-applies on every run |
| Redis reclaim, retry and dead-letter work | ✅ | `test_pending_entry_of_crashed_consumer_is_reclaimed`, retry and dead-letter tests, chaos Redis tests |
| Authorization tests pass | ✅ | `test_security.py` (22), `test_organizations.py` (5) |
| Dashboard uses real data | ✅ | Every page reads the API; screenshots come from a running stack |
| Responsive browser checks pass | ✅ | `responsive.spec.ts` (every page, no horizontal scrolling) |
| No major console errors | ✅ | Screenshot runs report zero console errors or warnings |
| Docker Compose starts | ⚠️ Not verified locally | No Docker on this machine; CI job prepared |
| Benchmarks are recorded | ⚠️ Mostly | The no-op matrix with hardware and settings, plus smoke-scale workload and recovery runs ([docs/benchmarks](../benchmarks/README.md)); the workload matrices and the load test still need a run on mains power |
| CI passes | ⚠️ Not run | Repository not pushed |
| README setup is verified | Partly | The native setup was followed on this machine; the Compose path was not |
| Limitations are documented | ✅ | [known issues](known-issues.md) |

## Scores

The prompt's weighting, scored out of 10 with the evidence for each.

| Dimension (weight) | Score | Evidence | Main deductions |
|---|---:|---|---|
| Correctness (25%) | 9.0 | 442 Python tests at 88.8% branch coverage; the state machine enforced in Python, SQL and a trigger, and proven equal; E2E covers every core journey | Compose and CI not executed |
| Reliability (20%) | 9.0 | Outbox, fenced leases, reclaim, reconciler and exactly-once fires, each proven by tests that kill real processes and cut real connections; a failure matrix maps each failure to a test | Reconciliation of a partial Redis loss waits for the band's stream to drain; entries that cannot be claimed are retried until a fix ships (deliberate and alerted, but noisy) |
| Security (15%) | 8.5 | Argon2id; rotating refresh tokens with reuse detection; strict tenant isolation proven by tests; hashed scoped keys; audit log; clean scans | CSP allows inline scripts; single HS256 key |
| Architecture (15%) | 8.5 | Layered modular monolith; pure domain package; PostgreSQL-as-truth with Redis as an expendable channel; 12 ADRs | Per-task CPU cost of the SQLAlchemy-based hot path |
| UI / accessibility (10%) | 8.5 | 10 functional pages over the real API, keyboard and screen-reader affordances, validated chart palette, light and dark, mobile checks | No automated axe audit; the builder is desktop-first |
| Performance (10%) | 7.0 | 143 no-op tasks/s per worker process and 438 on eight (50,000-job runs), 244,400 tasks without a duplicate; the bottleneck located by measurement (worker CPU, not PostgreSQL or the relay); two defects found by benchmarking and fixed (+23% on eight workers, first burst after a start 14× faster); crash recovery and a CPU-bound workload measured at smoke scale | About 7 ms of Python CPU per task limits each process; sub-linear scaling on a laptop; the I/O-bound and CPU-bound matrices, full recovery runs and the Locust load test are still to run on mains power; nothing measured on Linux or a server |
| Documentation (5%) | 9.0 | README with real screenshots; architecture with five Mermaid diagram types; reliability model; ADRs; deployment guide; evaluation set | Container docs are unverified locally |

**Weighted total: 8.6 / 10** (0.25 × 9.0 + 0.20 × 9.0 + 0.15 × 8.5 + 0.15 × 8.5 + 0.10 × 8.5 + 0.10 × 7.0 + 0.05 × 9.0).

## What changed during evaluation

The [improvement log](improvement-log.md) has 24 entries, each with root cause, fix and verification. The most
significant:

* A missing admin CLI left platform-admin features unusable and password resets impossible. Now implemented and
  tested.
* The benchmark harness leaked worker processes, which contaminated measurements. Fixed; numbers were re-measured.
* A disproved hypothesis: PostgreSQL commit latency is not the throughput limit. Per-process Python CPU is, and the
  report now shows the evidence.
* Measuring found that a worker whose database pool is smaller than its slots keeps opening connections, and that
  fresh workers opened their pools only on first use. PostgreSQL on the test machine opens connections one at a time
  at about 0.11 s each. After the fixes, eight workers process 23% more tasks per second and the first burst after
  a start is 14× faster.
* Repeated claim failures (for example after a bad release) are now counted and alerted on, and the fail-safe that
  keeps such tasks queued is tested.
* The benchmark harness now covers what the prompt asks for: I/O-bound and CPU-bound workloads, memory, and
  recovery from a killed worker under load. So far these are measured at smoke scale only.
* Inert settings, unclearable preferences, understated utilization, misleading latency charts and a stale UI after
  reconnects were all fixed and covered by tests.

## Top risks and next steps

1. **Run CI and the Compose stack** (push the repository). This is the only unverified delivery path.
2. **Finish the measurements.** Run `make bench-workloads` (I/O-bound and CPU-bound matrices, crash recovery) and the
   Locust load test on mains power, about 20 minutes in all.
3. **Per-process throughput.** Move claim and complete to raw asyncpg statements, then re-measure with the same
   harness.
4. **Partial Redis data loss.** The reconciler re-dispatches lost entries only once their band's stream is empty.
   Checking how long each `QUEUED` task has waited since its last dispatch would repair a partial loss sooner.
5. **Content Security Policy.** Switch to a nonce-based CSP to drop `'unsafe-inline'` for scripts.
6. **Operations.** Load the alert rules and dashboard into a real Prometheus and Grafana, and tune the thresholds on
   production traffic.
