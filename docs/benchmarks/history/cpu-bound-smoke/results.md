# FlowForge benchmark results: CPU-bound tasks

Measured 2026-10-14T04:47:25+00:00 with `python -m tests.performance.benchmark --workload cpu`. Every
number below was measured on the hardware listed; nothing is estimated or extrapolated.

Workload: `demo.cpu_hash` with payload `{"iterations": 50000}`, which runs 50,000 rounds of SHA-256 in the worker's process pool (CPU-bound).

## Environment

| | |
|---|---|
| CPU | 12th Gen Intel(R) Core(TM) i5-12500H (12 cores / 16 threads) |
| Memory | 7.7 GB |
| OS | Windows 11 (10.0.26200) |
| Python | 3.12.10 |
| PostgreSQL | 18.6 |
| Redis | 8.8.0 |
| PostgreSQL baseline | 20360.0 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.cpu_hash; payload: {"iterations": 50000}; worker concurrency: 8; worker cpu processes: 2; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 5 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) | Peak RSS, worker / API (MB) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|
| 200 | 1 | 200 | 77.7 | 1336.3 / 2448.0 / 2550.1 | 1240.0 / 2348.6 / 2456.1 | 91.0 | 1.0 | 0 | 2.44 | 32.19 | 222.0 / 118.1 |
