# FlowForge benchmark results

Measured 2026-10-14T04:25:04+00:00 with `python -m tests.performance.benchmark`. Every number below was
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
| PostgreSQL baseline | 23354.0 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.noop; worker concurrency: 8; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: none (cold workers).

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 100 | 8 | 100 | 16.5 | 777.8 / 5530.2 / 5956.4 | 759.0 / 5516.4 / 5939.9 | 15.0 | 1.0 | 0 | 3.62 | 19.84 |
| 100 | 8 | 100 | 306.9 | 245.4 / 317.9 / 321.7 | 91.5 / 270.1 / 276.0 | 96.0 | 1.0 | 0 | 0.83 | 18.91 |
| 100 | 8 | 100 | 314.8 | 181.3 / 305.2 / 313.9 | 58.0 / 242.1 / 246.0 | 94.0 | 1.0 | 0 | 2.1 | 20.31 |
