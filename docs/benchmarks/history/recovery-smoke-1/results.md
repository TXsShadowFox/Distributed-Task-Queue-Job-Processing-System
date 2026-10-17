# FlowForge benchmark results: I/O-bound tasks

Measured 2026-10-14T04:48:52+00:00 with `python -m tests.performance.benchmark --workload io`. Every
number below was measured on the hardware listed; nothing is estimated or extrapolated.

Workload: `demo.sleep` with payload `{"seconds": 0.1, "report_progress": false}`, which waits 100 ms without using CPU (I/O-bound).

## Environment

| | |
|---|---|
| CPU | 12th Gen Intel(R) Core(TM) i5-12500H (12 cores / 16 threads) |
| Memory | 7.7 GB |
| OS | Windows 11 (10.0.26200) |
| Python | 3.12.10 |
| PostgreSQL | 18.6 |
| Redis | 8.8.0 |
| PostgreSQL baseline | 19183.3 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.sleep; payload: {"seconds": 0.1, "report_progress": false}; worker concurrency: 8; worker cpu processes: 2; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 5 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) | Peak RSS, worker / API (MB) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|
| 600 | 2 | 600 | 8.3 | 4209.4 / 10092.4 / 72256.5 | 4080.5 / 9957.0 / 72131.0 | 135.0 | 1.0 | 0 | 4.17 | None | 95.6 / 120.6 |

## Recovery

One worker was killed with SIGKILL (no shutdown, no lease release) once the given number of tasks had
succeeded. The tasks it was running are retried once their 30 s leases expire (the reaper checks every
5 s). Entries it had read but not yet claimed are reclaimed by other workers after 60 s.

| Jobs | Workers at start | Killed after | Lost attempts | Tasks retried | Lost tasks done (s after kill) | Last task done (s after kill) | Completed | Duplicate successes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 600 | 2 | 180 | 0 | 0 | None | 70.2 | 600 | 0 |
