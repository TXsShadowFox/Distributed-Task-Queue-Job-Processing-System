# Improvement log

Defects and bottlenecks found while evaluating FlowForge, and what was done about each one. Severity: **critical**,
**high**, **medium** or **low**. "Verification" names the test or measurement that showed the fix works.

## Engine and reliability

### 1. Possible deadlock between workflow progression and task updates
* **Severity:** high
* **Problem:** Two transactions could lock a workflow run and one of its tasks in opposite orders.
* **Root cause:** Completion locked the task, then the run, while run-level operations locked the run, then its tasks.
* **Fix:** A single lock order everywhere, run before task (`_lock_parent_run`). The reaper uses `SKIP LOCKED` on both.
* **Verification:** `test_workflows.py` concurrency tests; the full suite runs without deadlock errors.
* **Outcome:** Fixed.
* **Remaining risk:** New code must keep the lock order; it is documented in `workflow_engine.py`.

### 2. Worker could keep a message it read while draining
* **Severity:** medium
* **Problem:** A blocking `XREADGROUP` that started before a drain could return entries after the drain began.
* **Root cause:** The drain flag was checked only before the read.
* **Fix:** Entries that arrive while draining or stopping are handed back atomically (`XADD`, `XACK`, `XDEL` in one
  `MULTI`).
* **Verification:** `test_drain_stops_new_work_and_resume_restarts_it`.
* **Outcome:** Fixed.
* **Remaining risk:** None known.

### 3. Scheduler crashed with "Set changed size during iteration"
* **Severity:** medium
* **Problem:** The queue-metrics loop could crash while workers registered new queues.
* **Root cause:** It iterated a set that another coroutine mutated.
* **Fix:** It now iterates a sorted snapshot.
* **Verification:** Integration suite; no recurrence in the chaos runs.
* **Outcome:** Fixed.
* **Remaining risk:** None known.

### 4. Hot path spent CPU in the ORM
* **Severity:** medium (throughput)
* **Problem:** A worker process was CPU-bound at roughly 75–120 no-op tasks/s, using 92–97% of one core.
* **Root cause:** Profiling showed 39% of worker CPU in SQLAlchemy. Hot-path statements went through ORM bulk
  persistence and a unit-of-work flush, and every completion queried the workflow parent even for tasks outside
  workflows.
* **Fix:** Claim and complete statements run on the session's connection (Core execution, identical SQL); the attempt
  row is a Core `INSERT`; the worker passes the workflow run id it learned at claim time.
* **Verification:** A/B test in one session (3,000 tasks each): 14.0 to 13.3 ms CPU per task (about 5%). The
  task lifecycle, reliability, workflow and observability suites pass.
* **Outcome:** Small improvement. The remaining cost is spread across SQLAlchemy and asyncio.
* **Remaining risk:** Throughput per process is still bounded by Python CPU. Raw asyncpg statements for claim and
  complete are the next candidate ([known issues](known-issues.md)).

### 5. A rejected hypothesis: PostgreSQL commit latency
* **Severity:** informational
* **Problem:** A worker process stayed near 100–120 tasks/s, far below what PostgreSQL should allow.
* **Root cause:** The first suspect was PostgreSQL fsync latency. A measured baseline disproved it: about 12,700 to
  22,000 single-row commits/s against about 3 commits per task. The worker process itself used 92–97% of one core,
  which showed the per-process CPU limit. An early comparison of 8 and 32 execution slots, which gave the same
  throughput, is not evidence either way: that benchmark's pool of 6 + 4 connections capped how many slots could
  reach the database (entry 6).
* **Fix:** The benchmark now records the PostgreSQL baseline, commits per task and worker CPU per task, so its report
  explains the bottleneck from data rather than assumption.
* **Verification:** `docs/benchmarks/results.md`, "Reading the numbers".
* **Outcome:** Bottleneck identified and documented.
* **Remaining risk:** None.

### 6. A pool smaller than a worker's slots churned connections
* **Severity:** medium (throughput and latency)
* **Problem:** In the benchmark (8 slots per worker, a pool of 6 plus 4 overflow), a 100-task burst on eight warm
  workers took 1.9 s. 83 tasks were claimed within 0.2 s, and the other 17 trickled in at one every 0.1 s. Under
  sustained load PostgreSQL started about 8 new sessions per second.
* **Root cause:** A worker needs one connection per busy slot plus two for its heartbeat and log flush. With a smaller
  pool, SQLAlchemy opens overflow connections under load and closes them again when they return to a full pool. On
  this machine PostgreSQL opens connections one at a time, about 0.11 s each (64 requested at once took 6.8 s), so
  every new connection stalled a claim.
* **Fix:** The benchmark uses the production defaults (10 + 10). Workers log `database_pool_smaller_than_concurrency`
  at start-up when the pool is smaller than concurrency + 2, and the [deployment guide](../deployment.md) explains
  the sizing.
* **Verification:** Same machine and commands, eight workers. New sessions under load fell from about 8/s to under
  1/s. 10,000 jobs went from 381.8 to 442.1 tasks/s, with worker CPU per task down from 18.8 to 16.9 ms. A 100-job
  burst went from 54.7 to 320.6 tasks/s (e2e p95 from 1,331 to 292 ms).
* **Outcome:** Fixed. The benchmark matrix was measured again.
* **Remaining risk:** Raising a worker's concurrency means raising its pool too, which counts against PostgreSQL's
  `max_connections`. The start-up warning says so.

### 7. Fresh workers paid for connection set-up on their first tasks
* **Severity:** medium
* **Problem:** The first 100 tasks on eight freshly started workers ran at 16.5 tasks/s (e2e p95 5.5 s); the next
  bursts ran at about 310 tasks/s. This happens after every deploy, restart or scale-out, which is when a backlog is
  most likely.
* **Root cause:** Pools open connections lazily, so each slot's first claim waited for a new connection: about 80
  connections, which PostgreSQL opened one at a time (see the previous entry).
* **Fix:** Each worker opens min(pool size, concurrency + 2) connections before it registers and takes work. If some
  cannot be opened, the worker logs a warning and starts anyway; the pool then opens connections on demand, as
  before.
* **Verification:** First burst on eight fresh workers: 16.5 tasks/s before, 230.5 tasks/s after (e2e p95 from
  5,530 ms to 418 ms). `test_worker_opens_its_database_pool_before_taking_work` fails when the warm-up is removed
  (1 connection instead of 5).
* **Outcome:** Fixed.
* **Remaining risk:** Start-up takes longer, about 1 s per worker here. The first burst is still slower than later
  ones (230 against about 310 tasks/s) while caches fill.

### 8. A workflow test relied on timestamps the engine does not order
* **Severity:** low (test defect)
* **Problem:** `test_diamond_workflow_runs_in_dependency_order_and_passes_data` failed once: the `transform` node's
  `started_at` was 0.4 ms earlier than an upstream node's `finished_at`.
* **Root cause:** Node timestamps are transaction start times (`now()`). When both upstream nodes finish together,
  the transaction that waits for the run lock keeps its earlier start time. The run itself was correct: `transform`
  received both upstream results.
* **Fix:** The test no longer compares timestamps across transactions. It proves the order from the data that
  `transform` received, and a comment explains why timestamps cannot.
* **Verification:** The workflow suite passed three times in a row, and then in the full run.
* **Outcome:** Fixed.
* **Remaining risk:** Displayed node times can be out of order by the length of a lock wait (milliseconds).

## Benchmark harness

### 9. Leaked worker processes contaminated measurements
* **Severity:** high (data integrity of results)
* **Problem:** A profiled worker processed only 1,080 of 3,000 tasks. Workers left over from an aborted run were
  consuming the rest.
* **Root cause:** Worker processes were stopped only on the success path.
* **Fix:** Every spawned process is tracked and stopped in a `finally` block. The earlier, contaminated numbers were
  discarded and re-measured.
* **Verification:** After runs, no `flowforge_worker --concurrency` processes remain (checked with `Get-CimInstance`).
* **Outcome:** Fixed.
* **Remaining risk:** A hard kill of the harness itself can still orphan its children.

### 10. The first measured run paid a cold-start penalty
* **Severity:** medium (data integrity of results)
* **Problem:** Three consecutive 100-job runs on eight freshly started workers measured 16.3, 55.4 and 53.8 tasks/s
  (e2e p95 5.5 s, 1.3 s and 1.3 s). The matrix measured the smallest job count first, so every 100-job result
  understated steady-state throughput.
* **Root cause:** Mostly connection set-up in the fresh workers' pools, which PostgreSQL performs one connection at a
  time on this machine (see the entry on fresh workers above). Statement caches also fill on first use.
* **Fix:** Each worker set first completes 50 unmeasured tasks per worker (`--warmup`; `--warmup 0` measures cold
  workers), and the report states this in its environment section. The product now opens its pool at start-up.
* **Verification:** After the change, two consecutive 100-job runs on eight workers measured 54.5 and 51.7 tasks/s.
  The full matrix was then measured again.
* **Outcome:** Fixed.
* **Remaining risk:** None for the harness. The [performance report](performance-report.md) gives cold-start
  numbers before and after the product fix.

### 11. A laptop's standby silently spoiled a benchmark run
* **Severity:** high (data integrity of results)
* **Problem:** During a re-run of the matrix, the laptop entered Modern Standby for almost four hours. The 50,000-job
  run on one worker reported 22.8 tasks/s and timed out with 37,451 tasks done. The harness then went on to the next
  worker set and crashed with a PostgreSQL deadlock while emptying the tables.
* **Root cause:** Nothing kept the machine awake or noticed that it had stopped. After a timeout the harness carried
  on, so the next workers processed the leftover tasks while the harness truncated the tables under them.
* **Fix:** The harness asks Windows to stay awake while it runs. It discards a run and stops if its own half-second
  sampling stalls for more than 5 s, and it stops the matrix after a timeout, keeping the partial report.
* **Verification:** The Windows event log shows the standby (10:28 to 14:21). Suspending the harness for 8 s in the
  middle of a run made it discard that run, exit with an error and stop all its services. The published matrix was
  measured while the machine stayed awake.
* **Outcome:** Fixed.
* **Remaining risk:** Closing the lid still suspends the machine; the stall check then discards the run instead of
  publishing it.

## API and data

### 12. Workflow list showed the last run's version as null
* **Severity:** low
* **Problem:** `GET /workflows` returned `last_run.version = null` while `/workflow-runs` returned the version.
* **Root cause:** The summary builder did not join `workflow_versions`.
* **Fix:** Joined the version in `_workflow_out`.
* **Verification:** `test_workflow_runs_listing` asserts `last_run.version == 1`.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 13. Preferences and project settings could be set but never cleared
* **Severity:** medium
* **Problem:** Sending `null` to clear the default project or the retention override did nothing.
* **Root cause:** `model_dump(exclude_none=True)` dropped explicit nulls before merging.
* **Fix:** `exclude_unset=True`, then keys whose value is null are removed after the merge.
* **Verification:** `test_preferences_merge_clear_and_stay_in_tenant`, `test_projects_create_update_and_clear_settings`.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 14. A project setting that did nothing
* **Severity:** medium (inert control)
* **Problem:** `settings.default_queue` was accepted and stored, but nothing read it.
* **Root cause:** It was planned, but the routing was never built.
* **Fix:** Removed from the API schema and the OpenAPI document, and regenerated the client types. Unknown settings
  now return 422.
* **Verification:** `test_projects_create_update_and_clear_settings` expects 422 for `default_queue`.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 15. Worker utilization was understated
* **Severity:** medium
* **Problem:** A worker started an hour ago appeared about 4% as busy in the 24-hour view as it really was.
* **Root cause:** Busy time was divided by the whole window, not by the time the worker was online.
* **Fix:** Divide by the online seconds within the window; the API now also returns `online_seconds`.
* **Verification:** `test_observability.py` checks the formula and the online time.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 16. The latency series was sparse
* **Severity:** low
* **Problem:** The chart joined distant points with straight lines, implying data that did not exist.
* **Root cause:** Only buckets with data were returned.
* **Fix:** Gap-filled with `generate_series`, like throughput. The chart draws gaps and marks isolated points.
* **Verification:** `test_observability.py` asserts the latency and throughput series share timestamps and include
  empty buckets.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 17. The DST warning almost never appeared
* **Severity:** low
* **Problem:** The schedule preview warned about daylight-saving only when its five previewed runs straddled a
  change.
* **Root cause:** The check compared only the previewed offsets.
* **Fix:** It also finds the zone's next offset change (`next_offset_change`) and states its date and offsets.
* **Verification:** Unit tests for New York (both directions), London, Sydney, Kolkata and UTC; an API test.
* **Outcome:** Fixed.
* **Remaining risk:** None.

## Operations and security

### 18. A declared admin CLI did not exist
* **Severity:** high
* **Problem:** `flowforge-admin` was declared in `pyproject.toml` but not implemented. The demo account could not
  become platform admin, so worker drain was unusable, and with no email there was no way to reset a password.
* **Root cause:** The CLI was planned and never written.
* **Fix:** Added `migrate`, `downgrade`, `current`, `makemigration`, `promote`, `demote`, `create-admin` and
  `set-password`. Passwords come only from the environment or a prompt, and every action is audited.
* **Verification:** `tests/integration/test_cli.py` (8 tests).
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 19. Log redaction hid a non-secret count
* **Severity:** low
* **Problem:** The retention log showed `"refresh_tokens": "[REDACTED]"` for a row count.
* **Root cause:** Keys containing `token` are always redacted, which is intentionally conservative.
* **Fix:** The field was renamed to `expired_sessions`. Redaction stays strict, so a numeric secret under a sensitive
  key is still masked.
* **Verification:** Scheduler log after restart.
* **Outcome:** Fixed.
* **Remaining risk:** None.

### 20. Repeated claim failures were invisible to monitoring
* **Severity:** medium
* **Problem:** When claiming a task failed with an unexpected error, the worker logged `task_supervisor_crashed` and
  left the stream entry pending, to be retried on every reclaim. Nothing counted these failures, so the only signals
  of a bad release were log lines and, eventually, a growing backlog.
* **Root cause:** The fail-safe (no acknowledgement, so the entry is retried) was designed but not instrumented.
* **Fix:** A delivery-count limit that parks such entries was considered and rejected: a bug in the claim path would
  park or fail every task. The fail-safe stays, and each failure now increments
  `flowforge_worker_handler_crashes_total`, which drives the critical `FlowForgeWorkerHandlerCrashes` alert and a
  series on the Grafana "Recovery activity" panel.
* **Verification:** `test_unexpected_claim_error_keeps_entry_pending_until_fixed` injects a claim failure. The
  counter rises on every reclaim while the task stays `QUEUED` with its entry pending; once the fault is removed the
  task runs once and succeeds on attempt 1.
* **Outcome:** Fixed.
* **Remaining risk:** The alert rule has not been loaded into a running Prometheus ([known issues](known-issues.md)).

### 21. Unit and integration tests were not type-checked
* **Severity:** low
* **Problem:** `make typecheck` and CI ran `mypy --strict` on the chaos and performance tests only. The unit and
  integration tests had 7 errors.
* **Root cause:** The type-check target listed test directories one by one, and the larger suites were never added.
* **Fix:** The errors were fixed: untyped JSON returns, an ignore comment whose error code no longer matched, and an
  optional count compared with an integer. The target now covers the whole `tests` tree.
* **Verification:** `mypy tests` passes (32 files). CI was updated and still passes `actionlint`.
* **Outcome:** Fixed.
* **Remaining risk:** None.

## Dashboard

### 22. Hydration and instant-navigation errors on Next.js 16.4
* **Severity:** high
* **Problem:** Dashboard pages logged hydration mismatches and chart width warnings.
* **Root cause:** Session-dependent UI rendered during prerendering.
* **Fix:** The authenticated shell renders only in the browser (`use(browser())`), and dashboard routes opt out of
  instant-navigation validation.
* **Verification:** Every page screenshot run reports "no console errors or warnings".
* **Outcome:** Fixed.
* **Remaining risk:** Framework upgrades may change these APIs.

### 23. UI fixes found by browser review and E2E
* **Severity:** low to medium
* **Problems and fixes:** See the table in [ui-review.md](ui-review.md): filter lag, unreadable workflow graphs,
  stacked new steps, stale UI after an SSE reconnect, duration rounding, clipped axis labels, and copy errors.
* **Verification:** The Playwright suite passes (15 tests) and screenshots were reviewed.
* **Outcome:** Fixed.

### 24. The README screenshot capture hung
* **Severity:** low
* **Problem:** The screenshot spec timed out.
* **Root cause:** It waited for `networkidle`, which never happens while the live event stream is open.
* **Fix:** It waits for loading skeletons to disappear.
* **Verification:** `screenshots.spec.ts` passes and wrote 11 images.
* **Outcome:** Fixed.
* **Remaining risk:** None.
