# FlowForge benchmark results

Measured 2026-10-14T04:23:55+00:00 with `python -m tests.performance.benchmark`. Every number below was
measured on the hardware listed; nothing is estimated or extrapolated.

## Environment

| | |
|---|---|
| CPU | 12th Gen Intel(R) Core(TM) i5-12500H (12 cores / 16 threads) |
| Memory | 7.7 GB |
| OS | Windows 11 (10.0.26200) |
| Python | 3.12.10 |
| PostgreSQL | 18.6 |
| Redis | 8.8.0 |
| PostgreSQL baseline | 21367.7 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.noop; worker concurrency: 8; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 50 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 10,000 | 8 | 10,000 | 442.1 | 10433.3 / 18724.0 / 19596.8 | 10358.0 / 18644.2 / 19513.0 | 81.0 | 1.0 | 0 | 2.97 | 16.92 |
| 100 | 8 | 100 | 320.6 | 194.4 / 292.2 / 311.4 | 93.5 / 239.1 / 247.0 | 84.0 | 1.0 | 0 | 6.12 | 18.12 |

## Reading the numbers

* Each task costs about 2.97 PostgreSQL commits (submission, outbox relay, claim, completion,
  events), measured from `pg_stat_database`. This includes the services' background transactions.
* PostgreSQL alone sustained 21367.7 commits/s on this machine, which would allow roughly
  7195 tasks/s. The best measured throughput was 442.1 tasks/s, so PostgreSQL is not
  the bottleneck here.
* With 8 worker process(es), each task costs about 16.92 ms of worker CPU, so one process
  tops out near 59 tasks/s whatever its number of slots. The worker is CPU-bound.
* End-to-end and queue-wait percentiles include the backlog: all jobs of a run are submitted at once, so
  the last ones wait for the ones before them. The execution time of `demo.noop` is pure platform
  overhead.
* Runs of 100 jobs finish in about a second, so fixed latencies (submission, relay hop, the first
  poll) dominate their throughput figure. Read the larger runs for sustained rates.
