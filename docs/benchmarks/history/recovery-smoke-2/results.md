# FlowForge benchmark results: I/O-bound tasks

Measured 2026-10-14T04:52:37+00:00 with `python -m tests.performance.benchmark --workload io`. Every
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
| PostgreSQL baseline | 22001.3 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.sleep; payload: {"seconds": 0.1, "report_progress": false}; worker concurrency: 8; worker cpu processes: 2; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 5 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) | Peak RSS, worker / API (MB) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|
| 400 | 2 | 400 | 11.2 | 2149.5 / 5567.1 / 35134.2 | 2017.0 / 5287.0 / 5588.0 | 133.0 | 1.02 | 0 | 3.93 | None | 95.4 / 120.9 |
| 400 | 2 | 400 | 10.2 | 2091.1 / 5429.2 / 38559.0 | 1939.5 / 5131.6 / 5449.0 | 129.0 | 1.02 | 0 | 4.05 | None | 95.7 / 121.0 |

## Recovery

One worker was killed with SIGKILL (no shutdown, no lease release) once the given number of tasks had
succeeded. The tasks it was running are retried once their 30 s leases expire (the reaper checks every
5 s). Entries it had read but not yet claimed are reclaimed by other workers after 60 s.

| Jobs | Workers at start | Killed after | Lost attempts | Tasks retried | Lost tasks done (s after kill) | Last task done (s after kill) | Completed | Duplicate successes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 400 | 2 | 200 | 8 | 8 | 33.4 | 33.4 | 400 | 0 |
| 400 | 2 | 200 | 8 | 8 | 36.9 | 36.9 | 400 | 0 |
