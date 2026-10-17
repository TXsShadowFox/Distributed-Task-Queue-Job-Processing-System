# Performance report

This report covers FlowForge's throughput, latency, resource use and crash recovery on one machine, where the time
goes, and what changed because of these measurements. Every number comes from the benchmark harness
(`tests/performance/benchmark.py`), the Locust load test (`tests/performance/locustfile.py`) or a profiling session,
on the machine described under [Setup](#setup). Nothing is estimated or extrapolated. Raw results:
[docs/benchmarks](../benchmarks/README.md).

## Summary

* **Throughput.** With no-op tasks, one worker process sustains 143 tasks/s and eight sustain 438 tasks/s on a
  16-thread laptop.
* **Correctness under load.** All 244,400 measured tasks in the 16 runs of the main matrix succeeded on their first
  attempt. No task ran twice.
* **The limit is worker CPU.** A worker process keeps one core busy and spends 6.8 ms of CPU per task, mostly in
  SQLAlchemy and asyncio. PostgreSQL is far from its limit: about 3 commits per task, against 16,700 commits/s on
  the same machine. The outbox relay finished publishing 50,000 entries long before the workers finished them.
* **Workloads.** A CPU-bound handler (about 25 ms of SHA-256 per task) ran at 77.7 tasks/s on one worker,
  97% of what its two process-pool processes allow, in a smoke-scale run. I/O-bound throughput is bounded by slots
  by design (8 slots and 100 ms waits allow 80 tasks/s per worker) but has not been measured yet; see
  [I/O-bound and CPU-bound workloads](#io-bound-and-cpu-bound-workloads).
* **Recovery.** When a worker was SIGKILLed under load, every task still completed exactly once. Tasks it was
  running were retried 33 to 37 s later (30 s leases); entries it had read but not claimed were picked up
  70 s later (60 s reclaim threshold). Measured at smoke scale; see
  [Recovery from a killed worker](#recovery-from-a-killed-worker).
* **Measuring found two defects, both fixed.** A database pool smaller than a worker's slots kept opening new
  connections, which PostgreSQL on this machine opens one at a time at about 0.11 s each. Workers also opened their
  pools only on first use. Fixing the first made eight workers 23% faster and 100-task bursts about 6× faster; fixing
  the second made the first burst after a start 14× faster.
* **Scaling.** Two workers deliver 1.8× the throughput of one, and eight deliver 3.1×. The processes share the
  laptop's cores with each other and with PostgreSQL, Redis, the API and the scheduler, so each task costs more CPU
  as workers are added.
* **API under load.** Not measured yet; see [API under load](#api-under-load).
* **How far to trust it.** Sustained results repeat within a few percent in one session, but this laptop ran about
  40% slower in one session after resuming from sleep. These are one laptop's numbers, not a server's.

## Setup

### Machine

| | |
|---|---|
| CPU | Intel Core i5-12500H: 4 performance cores with hyper-threading and 8 efficiency cores (12 cores, 16 threads) |
| Memory | 7.7 GB |
| OS | Windows 11 (10.0.26200), Balanced power plan, on mains power |
| Python | 3.12.10, with asyncio's default event loop on Windows (Proactor) |
| PostgreSQL | 18.6 for Windows; `fsync` and `synchronous_commit` on; `shared_buffers` 256 MB |
| Redis | 8.8.0 in a WSL 2 distribution; append-only file with `appendfsync everysec` |

No other FlowForge processes ran during the measurements, and neither did a browser or Node. The only other load came
from short helper scripts in the same session.

### Method

The harness starts the API, one scheduler and the workers as separate processes, the way production runs them
(`python -m flowforge_api`, `python -m flowforge_scheduler`, `python -m flowforge_worker`). They use production
settings: 8 execution slots and 2 process-pool processes per worker, database pools of 10 plus 10 overflow per
process, 5 s heartbeats and 30 s leases. For each worker count (1, 2, 4 and 8), the harness:

1. waits until every worker has registered and sent a heartbeat;
2. runs 50 unmeasured tasks per worker as a warm-up (`--warmup 0` measures cold workers instead);
3. for each job count, empties the task tables, submits every job at once through `POST /tasks/batch` (1,000 tasks
   per request, 4 requests in flight), and waits for all of them to finish.

Timings come from PostgreSQL's timestamps:

| Metric | Definition |
|---|---|
| Throughput | Completed tasks ÷ (last `finished_at` − first `created_at`) |
| End-to-end (e2e) | `finished_at` − `created_at`, per task |
| Queue wait | From `queued_at` to the start of the attempt (the claim) |
| Execution | From the claim's commit to the completion's commit, for the successful attempt |
| Commits per task | Change in `pg_stat_database.xact_commit` ÷ jobs, background transactions included |
| Worker CPU per task | User + system CPU time of all worker process trees ÷ completed tasks |
| Peak memory | Largest resident set of one worker's process tree, and of the API, sampled every 0.5 s |
| PostgreSQL baseline | Single-row `INSERT` commits per second from 8 connections over 3 s, without FlowForge |

Every run also checks correctness: each task's final state, the attempts per task, and tasks with more than one
successful attempt. The harness uses the `flowforge_test` database and Redis database 2 with its own key prefix, so
development data is never touched. It keeps a Windows machine awake while it runs, and it discards a run and stops if
its own half-second sampling ever stalls for more than 5 s (see [Variance](#variance) for why).

## Platform overhead: no-op tasks

`demo.noop` returns immediately, so these runs measure FlowForge itself. Throughput in tasks per second:

| Jobs | 1 worker | 2 workers | 4 workers | 8 workers |
|---:|---:|---:|---:|---:|
| 100 | 149.8 | 235.5 | 314.6 | 336.8 |
| 1,000 | 141.3 | 246.4 | 348.7 | 411.8 |
| 10,000 | 141.3 | 256.4 | 372.1 | 440.4 |
| 50,000 | 143.1 | 255.7 | 365.4 | 438.4 |

Runs of 100 jobs finish in under a second, so fixed costs weigh on them: the relay hop, the first read, and the claim
and completion round trips. Read the 10,000- and 50,000-job runs for sustained rates. The full table, with latency
percentiles and commits and CPU per task, is in [results.md](../benchmarks/results.md).

### Scaling with worker processes

| Workers | Tasks/s at 50,000 jobs | Speed-up | Efficiency | Worker CPU per task | Cores busy in workers |
|---:|---:|---:|---:|---:|---:|
| 1 | 143.1 | 1.00× | 100% | 6.8 ms | 0.97 |
| 2 | 255.7 | 1.79× | 89% | 7.6 ms | 1.93 |
| 4 | 365.4 | 2.55× | 64% | 10.5 ms | 3.83 |
| 8 | 438.4 | 3.06× | 38% | 17.0 ms | 7.45 |

Every worker process keeps close to one core busy at every scale, so throughput is roughly the cores busy in workers
divided by the CPU each task costs. What grows with the number of workers is that cost: 6.8 ms per task with one
worker, 17.0 ms with eight. Extra processes land on hyper-threaded siblings and on the slower efficiency cores, and
they compete with PostgreSQL, Redis, the API and the scheduler for the same 16 threads. Laptop processors also lower
their clock speed as more cores become busy. These explanations fit the data but were not measured one by one, and
scaling on a larger server was not measured.

### Where the time goes

**Not PostgreSQL.** Each task costs about 3.0 commits: submission, relay, claim, completion and event rows, plus the
services' background transactions. PostgreSQL alone sustained 16,700 single-row commits per second on this machine
(between 12,700 and 22,300 across sessions), which would allow about 5,500 tasks/s. The best run reached 440 tasks/s.

**Not the outbox relay.** In the 50,000-job run on eight workers, submissions were committed over the first 15.1 s,
and the relay had published every entry by 15.8 s, each within 0.87 s of the start of its submission transaction.
The workers finished at 114 s, so they had a backlog throughout. The eight workers' shares of the work differed by at
most 1.9% of the mean, and the completion rate stayed between 409 and 459 tasks/s in every 10-second window except
the first and the last.

**A worker process is CPU-bound.** With one worker, the worker process kept 0.97 cores busy. A cProfile session of one
worker, taken before the hot-path change described below, attributed its time as follows (profiling inflates the cost
of small calls, so the shares are indicative):

| Where | Share of profiled time |
|---|---:|
| SQLAlchemy | 38.5% |
| asyncio (standard library) | 15.9% |
| Windows socket sends (`WSASend`) | 5.9% |
| redis-py | 5.5% |
| asyncpg | 4.5% |
| Waiting on the I/O completion port (includes idle time) | 4.1% |
| FlowForge's worker runtime | 2.8% |

Moving the claim and complete statements from ORM unit-of-work execution to Core execution on the session's
connection (identical SQL) cut CPU per task by about 5% in an A/B test: 14.0 to 13.3 ms, 3,000 tasks each, in one
session. The rest of the cost is spread thinly across SQLAlchemy and asyncio. The next candidate is to issue the
hot-path statements through asyncpg directly.

## I/O-bound and CPU-bound workloads

The harness runs two more workloads (`--workload io` and `--workload cpu`, or `make bench-workloads`):

* **I/O-bound** (`demo.sleep`, waits 100 ms without using CPU). A worker's throughput is bounded by its slots: 8 slots
  ÷ 0.1 s = 80 tasks/s per worker. Raise `--concurrency`, and the database pool with it, for I/O-heavy handlers.
* **CPU-bound** (`demo.cpu_hash`, about 25 ms of SHA-256). The handler runs in the worker's process pool (2 processes
  by default), so one worker can finish at most about 80 tasks/s. More pool processes or more workers add cores.

Only a smoke-scale CPU-bound run exists so far: 200 jobs on one worker ran at 77.7 tasks/s, 97% of the pool's
ceiling. Each task used 32.2 ms of CPU (the 25 ms handler plus platform overhead), so the worker kept about 2.5 cores
busy, and the median attempt took 91 ms because eight slots share two pool processes
([raw output](../benchmarks/history/cpu-bound-smoke/results.md)).

The full matrices (100 to 10,000 jobs on 1 to 8 workers) are not published. The first attempt was spoiled by the
laptop's standby (see [Variance](#variance)), and afterwards the laptop ran on battery, which changes the processor's
power limits and would make the numbers incomparable with the rest of this report. They take about 15 minutes on
mains power: `make bench-workloads`.

## Memory

Peak resident memory of the largest single process tree, sampled every 0.5 s, in the smoke-scale runs:

| Process | Peak resident memory |
|---|---:|
| Worker, I/O-bound workload | 95 to 96 MB |
| Worker with its two process-pool processes, CPU-bound workload | 222 MB |
| API | 118 to 121 MB |

The trees include the few MB of the Windows virtual-env launcher process. The main no-op matrix was measured before
the harness recorded memory, and memory over a long run (a leak check) has not been measured.

## Recovery from a killed worker

`--kill-worker-after` SIGKILLs one worker once a given fraction of the jobs has succeeded: no shutdown hook runs and
no lease is released. Two mechanisms then recover its work:

* tasks it was **running** keep their leases until they expire (30 s; the reaper checks every 5 s), are marked
  `LOST`, and are retried after their backoff (1 to 2 s for a first retry);
* stream entries it had **read but not yet claimed** stay in its pending list until another worker reclaims them,
  60 s after the read (checked every 15 s).

Smoke-scale results, I/O-bound workload, two workers of which one is killed
([raw output](../benchmarks/history/recovery-smoke-1/results.md), [second run](../benchmarks/history/recovery-smoke-2/results.md)):

| Jobs | Killed after | Lost attempts | Tasks retried | Lost tasks done (s after kill) | Last task done (s after kill) | Completed | Duplicate successes |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 600 | 180 | 0 | 0 | — | 70.2 | 600 | 0 |
| 400 | 200 | 8 | 8 | 33.4 | 33.4 | 400 | 0 |
| 400 | 200 | 8 | 8 | 36.9 | 36.9 | 400 | 0 |

In the first run the worker died after reading eight entries but before claiming them, so nothing was lost, but those
eight waited for the 60 s reclaim. In the other two it died with eight tasks running, and they were retried once their
leases expired. Every task completed, and none succeeded twice. Both delays are settings: `lease_ttl_seconds` bounds
the first, `worker_reclaim_min_idle_ms` the second. Lowering the reclaim idle time is safe, because only one claim of
a task can succeed. The full recovery benchmark (10,000 jobs on four workers, killed three times) is part of
`make bench-workloads`.

## Two defects found by measuring

### A pool smaller than a worker's slots

The harness first gave each process a pool of 6 connections plus 4 overflow, while each worker runs 8 slots. A worker
needs one connection per busy slot plus two for its heartbeat and log flush. With a smaller pool, SQLAlchemy opens
overflow connections under load and closes them when they return to a full pool. On this machine that is expensive:

* Opening one connection takes 114 ms (median of 20). Requested together, 16 connections were all open after 1.66 s
  and 64 after 6.8 s, so PostgreSQL opened them one at a time.
* Under sustained load on eight workers, PostgreSQL started about 8 new sessions per second
  (`pg_stat_database.sessions`).
* In a 100-task burst on eight warm workers, 83 tasks were claimed within 0.2 s, and the other 17 one every 0.1 s.
  Each was waiting for a new connection.

Same machine and harness, eight workers, before and after switching to the production defaults (10 + 10):

| Eight workers | Pool 6 + 4 | Pool 10 + 10 |
|---|---:|---:|
| New PostgreSQL sessions per second under load | about 8 | under 1 |
| 10,000 jobs | 381.8 tasks/s | 442.1 tasks/s |
| Worker CPU per task | 18.8 ms | 16.9 ms |
| 100-job burst | 54.7 tasks/s, e2e p95 1,331 ms | 320.6 tasks/s, e2e p95 292 ms |

Across the full matrix, measured an hour apart in the same session:

| Workers | 50,000 jobs, pool 6 + 4 | 50,000 jobs, pool 10 + 10 | 100-job burst, pool 6 + 4 | 100-job burst, pool 10 + 10 |
|---:|---:|---:|---:|---:|
| 1 | 136.8 | 143.1 (+5%) | 114.8 | 149.8 |
| 2 | 232.8 | 255.7 (+10%) | 156.4 | 235.5 |
| 4 | 313.9 | 365.4 (+16%) | 110.5 | 314.6 |
| 8 | 356.9 | 438.4 (+23%) | 53.4 | 336.8 |

The production defaults were never affected (a pool of 10 covers 8 slots), but any operator who raises a worker's
concurrency without raising its pool would be. Workers now log `database_pool_smaller_than_concurrency` at start-up
in that case, and the [deployment guide](../deployment.md) explains the sizing.

### Pools that opened on first use

A fresh worker opened its connections only as its slots first needed them, so the first claims after every start
waited for connection set-up. With eight workers, that meant about 80 connections opened one at a time. Workers now
open min(pool size, concurrency + 2) connections before they register and take work.
`test_worker_opens_its_database_pool_before_taking_work` checks this, and fails when the warm-up is removed.

Three consecutive 100-job bursts on eight freshly started workers, with no warm-up batch:

| | First burst | Second | Third |
|---|---:|---:|---:|
| Before: pools open on first use | 16.5 tasks/s (e2e p95 5,530 ms) | 306.9 (318 ms) | 314.8 (305 ms) |
| After: pools open at start-up | 230.5 tasks/s (e2e p95 418 ms) | 220.6 (451 ms) | 324.4 (297 ms) |

The first burst is still a little slower than later ones while statement caches fill. With the old pool of 6 + 4, the
same experiment gave 16.3, 55.4 and 53.8 tasks/s. The benchmark keeps its warm-up batch, so its results describe
steady state.

## Latency

Every benchmark run submits all its jobs at once, so most of each task's end-to-end time is spent waiting behind the
tasks submitted before it. In every run of 1,000 jobs or more, e2e p99 was between 0.85 and 0.99 of the time needed
to drain the whole backlog. Those percentiles describe a backlog, not how responsive the platform is. Responsiveness
shows in two other measures:

* **Execution time.** For a no-op task, the median time from the claim's commit to the completion's commit was 27 to
  35 ms with one or two workers and 80 to 90 ms with eight. That is not handler time. Each worker runs eight slots on
  one busy event loop, so an attempt mostly waits for its turn.
* **Latency under a steady load.** Not measured yet. The Locust run described below would provide it: its tasks
  arrive at a steady rate below the workers' capacity, unlike the benchmark's bursts.

## API under load

Not run in the final configuration. `tests/performance/locustfile.py` simulates users who mostly submit tasks and
poll for them: submit a task (50% of requests), read one of their tasks (20%), list tasks (20%) and the analytics
overview (10%), with 0.1 to 0.5 s between requests. Its docstring has the command. Start the API with a raised
`FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE` first, because every simulated user registers an account.

## Correctness under load

All 16 runs of the main matrix, 244,400 tasks plus warm-ups: every task `SUCCEEDED` after exactly one attempt, no task
had two successful attempts, and none failed. The same held for the earlier matrix with the small pool (another
244,400 tasks) and for every diagnostic run. Failure handling under load is measured in
[Recovery from a killed worker](#recovery-from-a-killed-worker); crashes, partitions and Redis data loss are also
covered by the chaos tests ([test results](test-results.md)).

## Variance

* **Within a session**, sustained runs repeat within about 6%. Two full matrices with the old pool settings, one with
  and one without the warm-up batch, agreed within that range for every 10,000- and 50,000-job run. With default
  pools, 10,000 jobs on eight workers measured 442.1 tasks/s in a diagnostic run and 440.4 tasks/s in the full
  matrix. A second run of the default-pool matrix was cut short (see below), but its first three runs matched the
  first run within 2%.
* **Between sessions** the machine varies more. After the laptop resumed from sleep, one worker needed 12.5 to 14 ms
  of CPU per task (72 to 77 tasks/s), against 7.9 ms earlier the same evening with the same code. Later sessions
  measured about 7 ms again. The cause was not found: the power plan and power source were unchanged. Compare
  numbers only within one session.
* **Sleep and standby spoil runs.** During one matrix the laptop entered Modern Standby for almost four hours: a
  50,000-job run reported 22.8 tasks/s and timed out. The harness now keeps Windows awake while it runs, discards a
  run if its own sampling stalls for more than 5 s, and stops the matrix after a timeout, because unfinished tasks
  would distort every later run.
* **Runs of 100 jobs** vary most, because they last well under a second.

## Limits of these measurements

* One laptop running Windows: asyncio's Proactor event loop, PostgreSQL for Windows with its slow connection set-up,
  and Redis inside a WSL 2 virtual machine. A Linux server will behave differently; that was not measured.
* All jobs of a run are submitted at once, and there is no long soak test. The longest run lasted about six minutes.
* The load test used one API process; production runs several behind nginx.
* The harness and the services shared one machine.

## Recommendations

1. Give each worker a database pool of at least its concurrency + 2, and keep the total across all processes under
   PostgreSQL's `max_connections`.
2. Add throughput by adding worker processes, preferably across machines. On this laptop the second worker added
   113 tasks/s, while the fifth to eighth added about 18 tasks/s each.
3. Next optimisation: run the claim and complete statements through asyncpg directly, then re-measure with this
   harness.
4. Before planning capacity, run `make benchmark` on the target hardware.
