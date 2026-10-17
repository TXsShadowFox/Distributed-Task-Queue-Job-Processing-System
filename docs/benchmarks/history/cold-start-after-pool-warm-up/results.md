# FlowForge benchmark results

Measured 2026-10-14T04:29:38+00:00 with `python -m tests.performance.benchmark`. Every number below was
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
| PostgreSQL baseline | 20808.3 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.noop; worker concurrency: 8; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: none (cold workers).

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 100 | 8 | 100 | 230.5 | 328.5 / 417.8 / 430.4 | 77.5 / 303.1 / 325.1 | 144.5 | 1.0 | 0 | 0.82 | 23.91 |
| 100 | 8 | 100 | 220.6 | 252.4 / 450.8 / 452.9 | 104.5 / 367.1 / 370.0 | 130.5 | 1.0 | 0 | 4.43 | 21.25 |
| 100 | 8 | 100 | 324.4 | 182.9 / 296.5 / 301.4 | 70.5 / 234.2 / 242.1 | 89.0 | 1.0 | 0 | 0.19 | 18.28 |
