# Phase 0 — Plan, Requirements, Risks

Status: living document. Last updated during Phase 0 (initial build session).

## 1. Repository inspection

The working folder contained only the build prompt (`../Distributed Task Queue & Job Processing System Prompt.md`).
No existing code, git history or infrastructure. FlowForge is built from scratch in `flowforge/`.

### Build/verification environment (recorded, not assumed)

| Item | Value |
|---|---|
| OS | Windows 11 Home 10.0.26200 (Smart App Control **On**) |
| CPU | Intel Core i5-12500H, 12 cores / 16 threads |
| RAM | 7.7 GB |
| Python | 3.12.10 (uv 0.13.0 workspace) |
| Node.js | 25.9.0, npm 11.12.1 |
| PostgreSQL | 18.6 — EDB portable binaries, run natively (`scripts/devstack.py`) |
| Redis | 8.8.0 — inside a WSL2 Alpine 3.24 distro (`flowforge-dev`) because Smart App Control blocks the unsigned Windows Redis build |
| Docker | **Not installed** — Compose files are written but cannot be started on this machine (see known-issues) |

Two environment hazards found and handled:

1. The parent folder name contains `&`. npm's Windows `.cmd` shims break on it (`'Job' is not recognized…`).
   Workaround used and documented: `npm_config_script_shell` → Git Bash (or work from a path without `&`).
2. Smart App Control blocks some unsigned native binaries (the Windows Redis build; once, transiently, an asyncpg DLL).
   We do not bypass the policy; Redis runs in WSL instead.

## 2. Spec review — gaps in the prompt and how they are resolved

The prompt is strong on intent but leaves several engineering decisions open or ambiguous.
Each item below is an *improvement over the prompt*; the rationale is recorded in `docs/decisions/`.

| # | Gap / ambiguity in the prompt | Resolution |
|---|---|---|
| 1 | State machine lists states but no transition table; no state for delayed jobs; `FAILED` vs `DEAD_LETTERED` undefined. | Added `SCHEDULED`. Full transition table in `flowforge_shared.states`, enforced three times: Python validation, guarded SQL `UPDATE … WHERE state IN (legal predecessors)`, and a PostgreSQL trigger that rejects illegal transitions. `DEAD_LETTERED` = permanent failure with DLQ enabled (default); `FAILED` = permanent failure with `dead_letter=false`. |
| 2 | "Leases" and "Redis pending messages" are both requested, which risks two competing sources of truth. | PostgreSQL is the single source of truth for task state and leases (with **fencing tokens**). Redis Streams is the dispatch channel: a message is ACKed only after the durable claim commits; PEL reclaim covers crashes between read and claim. |
| 3 | Outbox requested without a relay latency strategy. | Outbox rows + statement-level `NOTIFY` trigger; relay `LISTEN`s for sub-10 ms dispatch and polls as a fallback. Rows are claimed with `FOR UPDATE SKIP LOCKED`, so multiple relays are safe. |
| 4 | Redis data loss (AOF `everysec`) can drop dispatched messages; prompt does not cover it. | Reconciler re-dispatches `QUEUED` tasks of a stream that is empty but still has queued tasks in PostgreSQL. Duplicate dispatch is harmless (idempotent claim). |
| 5 | Priority with Redis Streams is not native. | Three priority bands per queue (high 7–9, normal 4–6, low 0–3) with smooth weighted round-robin reads to avoid starvation. |
| 6 | Cron + restart recovery could double-fire. | Unique `(schedule_id, fire_time)` on tasks makes each fire time exactly-once at the database level; explicit misfire and overlap policies; DST handled with `cronsim` semantics and tested. |
| 7 | Concurrent completion of two DAG parents could double-create or miss a child. | Per-run row lock (`SELECT … FOR UPDATE` on `workflow_runs`) serializes progression; unique `workflow_node_run_id` on tasks as a backstop. Workflow definitions are **versioned** so edits never mutate running runs. |
| 8 | Workers are shared infrastructure, but "drain" is a tenant-visible control. | Shared worker fleet; fleet view is read-only for tenants, task IDs of other tenants are never shown, and drain requires a platform admin. |
| 9 | API payload validation, yet workers own the handlers. | Workers publish a task-type catalog (JSON Schema from Pydantic) on start-up; the API validates payloads against it. No code is ever accepted over the API. |
| 10 | SDK retries on network errors could duplicate submissions. | The SDK auto-generates an idempotency key per `submit()` call, so retries are safe. |
| 11 | Spec's table list lacks storage for logs, sessions, versions, catalog. | Added `task_logs`, `refresh_tokens`, `workflow_versions`, `task_types`. |
| 12 | Monorepo layout puts DB/domain code nowhere shared by API, worker and scheduler. | Added `packages/core` (`flowforge_core`). Pure logic (states, retry math, DAG, cron, priority) lives in `packages/shared-schemas` (`flowforge_shared`) and is unit-tested without services. |
| 13 | Testcontainers requires Docker, which may not exist (it does not here). | Tests use `FLOWFORGE_TEST_DATABASE_URL` / `FLOWFORGE_TEST_REDIS_URL` when set, otherwise Testcontainers; CI uses service containers. |
| 14 | "Notifications" control in the top bar has no defined backend. | Notifications are derived from real events (dead-lettered tasks, failed workflow runs, offline workers) with per-user read markers. |

## 3. Requirements (condensed)

Functional: submit/inspect/cancel/retry tasks; delayed and cron schedules; DAG workflows with versioning; worker fleet with
heartbeats, drain, leases; DLQ inspect/retry/archive; analytics from real data; API keys with scopes/rotation/revocation;
RBAC (owner/admin/developer/viewer) with organization and project isolation; real-time updates (SSE); Python SDK.

Non-functional: at-least-once execution with documented failure modes; no illegal state transitions under concurrency;
graceful shutdown; observability (Prometheus, Grafana, OpenTelemetry, JSON logs); WCAG AA-minded glass UI;
reproducible benchmarks; CI; documentation.

## 4. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| No Docker locally → Compose unverifiable | Certain | Medium | Native dev stack for all testing; Compose validated statically; CI builds containers; clearly reported. |
| Smart App Control blocks native deps | Medium | High | Tested every native wheel used; Redis moved to WSL; fall back to pure-Python alternatives if needed. |
| 7.7 GB RAM limits benchmark size / parallel services | High | Medium | Bound worker counts, measure memory, record limitations; 50k-job run only if it fits. |
| Windows asyncio differences (signals, Proactor loop) | Medium | Medium | Cross-platform signal handling; tests run on Windows here and Linux in CI. |
| Scope size (full SaaS) vs. quality | High | High | Vertical slices; engine correctness first; every UI control backed by a real endpoint. |
| Lease expiry while a slow handler still runs (zombie writes) | Medium | High | Fencing token on every worker-originated write; zombie results are discarded and recorded. |
| Clock skew between hosts | Low | Medium | All lease/schedule comparisons use the database clock (`now()`), never worker clocks. |

## 5. Milestones

- [x] P1 Foundation — workspace, settings, DB models + migrations, FastAPI skeleton, Next.js skeleton, tooling
- [x] P2 Engine — submission, outbox relay, Redis Streams, worker runtime, claim/ack, state tracking
- [x] P3 Reliability — retries/backoff, idempotency, delayed tasks, DLQ, leases/reaper, PEL reclaim, reconciler, fault tests
- [x] P4 Workflows — DAG validation, versions, runs, progression, cancellation, resume-after-retry
- [x] P5 Dashboard — design system, auth, all pages wired to real endpoints, SSE
- [x] P6 Security/Observability — RBAC tests, API keys, audit logs, rate limiting, metrics, tracing
- [x] P7 Production readiness — Docker, Compose, Nginx, Prometheus/Grafana, CI, benchmarks, docs.
  *Partially verified:* the container files and the CI workflow were written and syntax-checked but not executed
  here (no Docker on this machine, repository not pushed). The no-op benchmark matrix is complete; the I/O-bound and
  CPU-bound matrices, full recovery runs and the load test exist only at smoke scale so far. See
  `docs/evaluation/known-issues.md`.
- [x] P8 Final evaluation — full test runs, browser checks, chaos, security review, reports
  (`docs/evaluation/evaluation-report.md`)
