# FlowForge benchmark results

Measured 2026-10-14T04:17:01+00:00 with `python -m tests.performance.benchmark`. Every number below was
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
| PostgreSQL baseline | 20030.7 single-row commits/s (8 connections, no FlowForge involved) |

Settings: task type: demo.noop; worker concurrency: 8; submission: HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight; database pool per process: 6 + 4 overflow; timings: production defaults (heartbeat 5 s, lease 30 s); warm up: each worker set first completes 50 unmeasured tasks per worker.

## Results

| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) | Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) |
|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 100 | 1 | 100 | 114.8 | 509.9 / 853.8 / 866.7 | 477.5 / 812.8 / 842.0 | 34.0 | 1.0 | 0 | 3.0 | 8.28 |
| 1,000 | 1 | 1,000 | 132.3 | 3925.4 / 7178.6 / 7488.1 | 3887.0 / 7134.6 / 7451.1 | 31.0 | 1.0 | 0 | 3.09 | 7.14 |
| 10,000 | 1 | 10,000 | 133.6 | 37346.6 / 70019.0 / 72945.3 | 37310.0 / 69989.0 / 72911.0 | 32.0 | 1.0 | 0 | 3.11 | 7.24 |
| 50,000 | 1 | 50,000 | 136.8 | 179161.0 / 341393.0 / 355603.0 | 179128.0 / 341364.2 / 355568.2 | 31.0 | 1.0 | 0 | 3.1 | 7.09 |
| 100 | 2 | 100 | 156.4 | 301.7 / 498.5 / 528.9 | 258.5 / 460.4 / 471.2 | 36.5 | 1.0 | 0 | 3.34 | 9.38 |
| 1,000 | 2 | 1,000 | 221.5 | 2320.7 / 4229.7 / 4387.5 | 2270.5 / 4195.1 / 4330.2 | 35.0 | 1.0 | 0 | 3.03 | 8.0 |
| 10,000 | 2 | 10,000 | 231.1 | 20695.8 / 39604.4 / 41476.8 | 20664.5 / 39567.1 / 41434.0 | 36.0 | 1.0 | 0 | 3.07 | 8.37 |
| 50,000 | 2 | 50,000 | 232.8 | 105647.1 / 196897.9 / 204991.7 | 105611.5 / 196853.0 / 204940.1 | 36.0 | 1.0 | 0 | 3.07 | 8.36 |
| 100 | 4 | 100 | 110.5 | 276.0 / 389.6 / 800.5 | 218.5 / 372.0 / 784.1 | 48.0 | 1.0 | 0 | 1.13 | 14.38 |
| 1,000 | 4 | 1,000 | 270.4 | 1964.0 / 3460.4 / 3660.6 | 1906.5 / 3370.1 / 3581.0 | 55.0 | 1.0 | 0 | 3.12 | 13.72 |
| 10,000 | 4 | 10,000 | 296.0 | 16948.2 / 29565.0 / 30755.8 | 16896.0 / 29507.0 / 30707.0 | 55.0 | 1.0 | 0 | 3.05 | 12.87 |
| 50,000 | 4 | 50,000 | 313.9 | 76607.7 / 141720.5 / 146290.4 | 76550.0 / 141660.0 / 146236.0 | 54.0 | 1.0 | 0 | 3.06 | 12.31 |
| 100 | 8 | 100 | 53.4 | 281.5 / 1328.8 / 1746.7 | 167.0 / 1310.2 / 1710.9 | 58.5 | 1.0 | 0 | 2.27 | 21.41 |
| 1,000 | 8 | 1,000 | 275.1 | 1576.1 / 2772.0 / 2858.3 | 1492.0 / 2657.2 / 2778.0 | 75.5 | 1.0 | 0 | 2.84 | 18.64 |
| 10,000 | 8 | 10,000 | 366.4 | 12882.0 / 22984.3 / 24053.9 | 12774.5 / 22892.0 / 23961.0 | 91.0 | 1.0 | 0 | 3.06 | 19.24 |
| 50,000 | 8 | 50,000 | 356.9 | 62120.0 / 116788.6 / 121678.3 | 62014.0 / 116698.1 / 121582.0 | 93.0 | 1.0 | 0 | 3.06 | 19.48 |

## Reading the numbers

* Each task costs about 3.065 PostgreSQL commits (submission, outbox relay, claim, completion,
  events), measured from `pg_stat_database`. This includes the services' background transactions.
* PostgreSQL alone sustained 20030.7 commits/s on this machine, which would allow roughly
  6535 tasks/s. The best measured throughput was 366.4 tasks/s, so PostgreSQL is not
  the bottleneck here.
* With 1 worker process(es), each task costs about 7.14 ms of worker CPU, so one process
  tops out near 140 tasks/s whatever its number of slots. The worker is CPU-bound.
* With 8 worker processes the cost rose to about 19.24 ms per task: the processes share the
  machine's cores (and hyperthreads) with each other, the API, the scheduler, PostgreSQL and Redis.
  That is why throughput grows with workers, but less than proportionally.
* End-to-end and queue-wait percentiles include the backlog: all jobs of a run are submitted at once, so
  the last ones wait for the ones before them. The execution time of `demo.noop` is pure platform
  overhead.
* Runs of 100 jobs finish in about a second, so fixed latencies (submission, relay hop, the first
  poll) dominate their throughput figure. Read the larger runs for sustained rates.
