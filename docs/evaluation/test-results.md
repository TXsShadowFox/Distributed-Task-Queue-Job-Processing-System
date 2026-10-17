# Test results

Results of the final verification runs on 2026-10-14, on the development machine (Windows 11, Python 3.12.10,
PostgreSQL 18.6, Redis 8.8, Microsoft Edge for browser tests). Every suite runs against real services; nothing is
mocked except HTTP transports in the SDK unit tests. Commands are in the [Makefile](../../Makefile).

## Summary

| Suite | Command | Result |
|---|---|---|
| Python unit | `pytest tests/unit` | **342 passed** |
| Python integration (real PostgreSQL + Redis, in-process API and workers) | `pytest tests/integration` | **100 passed** |
| Backend coverage (unit + integration, branch coverage) | `pytest tests/unit tests/integration --cov` | **88.76%** (gate: 85%) |
| Chaos (SIGKILLed processes, TCP partitions) | `FLOWFORGE_RUN_CHAOS=1 pytest tests/chaos` | **5 passed**, after the final worker change too |
| Dashboard unit and component tests (Vitest, jsdom) | `npm test` | **57 passed** (8 files) |
| End-to-end (Playwright, against the **production build**) | `npx playwright test --project=desktop --project=mobile` | **14 passed** after the final worker change: 12 desktop, 2 mobile (Pixel 7). The screenshot capture (a 15th test) passed in the earlier run and was not repeated, to keep the README images |
| Python lint and format | `ruff check .`, `ruff format --check .` | Pass |
| Python types (`mypy --strict`, all packages and every test tier) | `make typecheck` | Pass |
| Dashboard lint and types | `npm run lint`, `npm run typecheck` | Pass (ESLint with zero warnings allowed) |
| Dashboard production build | `npm run build` | Pass: 19 routes; standalone server bundle |
| Security scans | `make security-check` | Pass; see [security review](security-review.md) |
| CI workflow lint | `uvx --from actionlint-py actionlint` | Pass: no findings (a deliberately broken workflow was used to confirm it reports errors) |

## Python tests by file

| File | Tests | Covers |
|---|---:|---|
| `unit/shared/test_lifecycle.py` | 175 | Failure decisions for every state, kind, retry budget and dead-letter setting |
| `unit/shared/test_cron.py` | 43 | Cron validation, fire times across DST in both hemispheres, next offset change, descriptions |
| `unit/shared/test_ids_rbac_messages.py` | 27 | UUIDv7 ordering, the role and permission matrix, grant rules, stream message encoding |
| `unit/shared/test_priority.py` | 26 | Priority bands, queue names, weighted round-robin fairness |
| `unit/shared/test_states.py` | 24 | Transition tables for tasks, runs and nodes; predecessors |
| `unit/shared/test_dag.py` | 18 | Cycle detection with paths, limits, topological order |
| `unit/shared/test_retry.py` | 16 | Backoff math and jitter bounds |
| `unit/sdk/test_client.py` | 13 | SDK retries, `Retry-After`, error mapping, wait and polling |
| `integration/test_security.py` | 22 | Authentication, refresh rotation and reuse, lockout, tenant isolation, RBAC escalation, API keys, rate limits, headers, audit redaction, preferences |
| `integration/test_reliability.py` | 19 | Trigger-enforced transitions, lease expiry, fencing, cancel races, duplicate deliveries, reclaim, claim failures kept queued and counted, reconciler, outbox under Redis outage, drain, graceful shutdown, pool opened at start-up |
| `integration/test_task_lifecycle.py` | 17 | Submission, validation, idempotency, batches, delays, retries, dead-lettering, cancellation, timeouts, priorities, filters |
| `integration/test_schedules.py` | 11 | Schedule CRUD, preview with DST notes, exactly-once concurrent fires, misfire and overlap policies |
| `integration/test_workflows.py` | 9 | Validation, versioning and conflicts, execution, failure skips, resume after retry, cancellation, listings |
| `integration/test_cli.py` | 8 | Admin CLI: promote and demote, create-admin, set-password, migrations, usage errors |
| `integration/test_observability.py` | 7 | Analytics correctness, notifications, health, metrics, system status |
| `integration/test_organizations.py` | 5 | Organization rename, member lifecycle, projects and settings, audit pagination, role matrix |
| `integration/test_sdk.py` | 2 | The SDK against a live API server |
| `chaos/test_chaos.py` | 5 | Worker SIGKILL mid-task, Redis partition, Redis data loss, PostgreSQL partition, scheduler replicas killed mid-cycle |

### Coverage by area (from the run above)

| Area | Coverage |
|---|---|
| Domain rules (`flowforge_shared`) | 91–100% per module |
| Services (`flowforge_core.services`) | 83–100% per module |
| API (routers, middleware, realtime hub) | 77–100% per module; the realtime hub (77%) is the lowest |
| Worker runtime | 78%. Uncovered lines are mostly signal handling, process-pool error paths and Redis error branches. The chaos tier exercises these in subprocesses, where coverage is not measured |
| Infrastructure | Broker 75%, logging setup 64%, tracing 57% (only active when an OTLP endpoint is configured) |
| Entry points (`__main__`) | 0%; started as real processes by the chaos and benchmark harnesses |

## End-to-end scenarios

All run against `next build` output served by the standalone server, the API, a worker and the scheduler. Each test
registers its own organization, so tests are independent and need no seed data.

| Spec | Scenario |
|---|---|
| `auth.spec.ts` | Register; sign out; a protected page redirects to sign-in and back; a wrong password shows an error; client-side validation |
| `tasks.spec.ts` | Submit from the dialog and watch it succeed live; filters, search and sorting in a shareable URL; cancel a delayed task |
| `dead-letter.spec.ts` | Retries with backoff end in the dead-letter queue; inspect and retry with a fresh budget; bulk archive and include archived |
| `workflows.spec.ts` | Build a DAG in the editor; a cycle is refused with its path; edit a step; validate; create; run to success; save v2; browse v1 read-only |
| `api-keys.spec.ts` | Create a scoped key; use it (200, 201, and 403 outside its scopes); rotate immediately (old key 401); revoke (401) |
| `schedules-workers.spec.ts` | Time-zone search; invalid cron feedback; preset; five-run preview; create, pause, delete; workers listed; drain hidden for non-admins |
| `responsive.spec.ts` | Every page from the mobile navigation with no horizontal scrolling; submitting a task from a phone |
| `screenshots.spec.ts` | Captures the README screenshots from the demo data |

## Problems found by these runs and fixed

* The workflow E2E drag ran while the canvas was still animating (introduced when new steps started auto-fitting
  the view). The helper now waits for handle positions to settle; three consecutive runs passed.
* `test_timeout_is_enforced_and_retried` failed once while the laptop slept mid-run (the run took 72 minutes). It
  passed alone and in the full suite after resume; no code change was needed.
* `test_diamond_workflow_runs_in_dependency_order_and_passes_data` failed once on a timestamp comparison the engine
  does not guarantee (transaction start times of racing completions). The test now proves the order from the data the
  downstream step received; the workflow suite passed three times in a row, then in the full run.
* `ruff format --check .` started to flag the Python example in the README, because Ruff 0.17 formats code blocks in
  Markdown. The example was reformatted, so CI's format check passes.
* See the [improvement log](improvement-log.md) for defects found by tests during development.

## Not run here

* Docker image builds, `docker compose up` and the Playwright suite against Compose: they run in CI. Docker is not
  available on this machine; see [known issues](known-issues.md).
* `make` itself is not installed on this machine. The targets wrap the commands listed above, which were run
  directly.
