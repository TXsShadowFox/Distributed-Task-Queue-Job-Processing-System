# FlowForge benchmark results

Measured 2026-10-14T04:46:51+00:00 with `python -m tests.performance.benchmark`. Every number below was
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
| PostgreSQL baseline | 16712.7 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.noop; worker concurrency: 8; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 10 + 10 overflow (production defaults); timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 50 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 100 | 1 | 100 | 149.8 | 375.5 / 637.5 / 660.9 | 337.5 / 608.3 / 648.0 | 27.0 | 1.0 | 0 | 3.22 | 6.25 |
| 1,000 | 1 | 1,000 | 141.3 | 3620.0 / 6768.5 / 7026.8 | 3589.0 / 6738.0 / 6998.0 | 31.0 | 1.0 | 0 | 2.99 | 6.7 |
| 10,000 | 1 | 10,000 | 141.3 | 33674.0 / 65479.0 / 68869.6 | 33640.0 / 65446.6 / 68825.0 | 31.0 | 1.0 | 0 | 3.06 | 6.78 |
| 50,000 | 1 | 50,000 | 143.1 | 169842.3 / 326043.4 / 339815.2 | 169809.5 / 326013.3 / 339785.0 | 31.0 | 1.0 | 0 | 3.07 | 6.78 |
| 100 | 2 | 100 | 235.5 | 244.7 / 410.3 / 423.7 | 211.5 / 375.6 / 405.0 | 34.0 | 1.0 | 0 | 3.76 | 8.12 |
| 1,000 | 2 | 1,000 | 246.4 | 2144.6 / 3877.4 / 4026.4 | 2110.5 / 3840.2 / 3990.0 | 35.0 | 1.0 | 0 | 2.8 | 7.44 |
| 10,000 | 2 | 10,000 | 256.4 | 19601.4 / 35953.0 / 37396.2 | 19569.0 / 35920.2 / 37363.1 | 35.0 | 1.0 | 0 | 3.07 | 7.52 |
| 50,000 | 2 | 50,000 | 255.7 | 95728.9 / 179294.3 / 186756.3 | 95692.5 / 179256.0 / 186718.0 | 35.0 | 1.0 | 0 | 3.04 | 7.56 |
| 100 | 4 | 100 | 314.6 | 188.5 / 300.4 / 317.6 | 144.0 / 253.3 / 299.0 | 46.5 | 1.0 | 0 | 6.84 | 10.62 |
| 1,000 | 4 | 1,000 | 348.7 | 1520.9 / 2731.9 / 2850.7 | 1475.0 / 2683.1 / 2786.0 | 45.0 | 1.0 | 0 | 2.82 | 10.33 |
| 10,000 | 4 | 10,000 | 372.1 | 12919.2 / 23768.1 / 25016.9 | 12868.0 / 23716.0 / 24963.0 | 48.0 | 1.0 | 0 | 3.05 | 10.42 |
| 50,000 | 4 | 50,000 | 365.4 | 65070.2 / 118725.0 / 124724.1 | 65021.5 / 118630.1 / 124677.0 | 48.0 | 1.0 | 0 | 3.02 | 10.47 |
| 100 | 8 | 100 | 336.8 | 175.7 / 285.9 / 293.2 | 66.0 / 225.1 / 232.0 | 89.5 | 1.0 | 0 | 0.33 | 18.12 |
| 1,000 | 8 | 1,000 | 411.8 | 1333.8 / 2341.3 / 2414.1 | 1260.0 / 2260.1 / 2356.0 | 80.0 | 1.0 | 0 | 3.21 | 17.27 |
| 10,000 | 8 | 10,000 | 440.4 | 10787.3 / 19333.8 / 20218.9 | 10707.5 / 19254.0 / 20130.0 | 81.0 | 1.0 | 0 | 2.95 | 16.89 |
| 50,000 | 8 | 50,000 | 438.4 | 50690.5 / 93037.9 / 97479.3 | 50607.0 / 92952.1 / 97323.0 | 82.0 | 1.0 | 0 | 3.04 | 17.0 |

## Reading the numbers

* Each task costs about 3.04 PostgreSQL commits (submission, outbox relay, claim, completion,
  events), measured from `pg_stat_database`. This includes the services' background transactions.
* PostgreSQL alone sustained 16712.7 commits/s on this machine, which would allow roughly
  5498 tasks/s. The best measured throughput was 440.4 tasks/s, so PostgreSQL is not
  the bottleneck here.
* With 1 worker process(es), each task costs about 6.78 ms of worker CPU, so one process
  tops out near 147 tasks/s whatever its number of slots. The worker is CPU-bound.
* With 8 worker processes the cost rose to about 17 ms per task: the processes share the
  machine's cores (and hyperthreads) with each other, the API, the scheduler, PostgreSQL and Redis.
  That is why throughput grows with workers, but less than proportionally.
* End-to-end and queue-wait percentiles include the backlog: all jobs of a run are submitted at once, so
  the last ones wait for the ones before them. The execution time of `demo.noop` is pure platform
  overhead.
* Runs of 100 jobs finish in about a second, so fixed latencies (submission, relay hop, the first
  poll) dominate their throughput figure. Read the larger runs for sustained rates.
