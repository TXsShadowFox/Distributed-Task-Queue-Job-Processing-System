# Benchmark results

Raw output of `tests/performance/benchmark.py`. Each directory holds `results.md` and `results.json` (every
percentile and counter). The services' logs from a run go to `logs/` next to them and are not committed. The
analysis, the method and the history behind these numbers are in the
[performance report](../evaluation/performance-report.md).

## Current results

| Results | What was measured | Reproduce with |
|---|---|---|
| [No-op tasks](results.md) | `demo.noop`, platform overhead only: 100 to 50,000 jobs on 1, 2, 4 and 8 workers | `make benchmark` |

Not yet measured in the final configuration (they need about 15 minutes on mains power, `make bench-workloads`):
the I/O-bound and CPU-bound matrices and the full crash-recovery runs. Their output goes to `io-bound/`, `cpu-bound/`
and `recovery/`. Smoke-scale runs of both are under History.

## History

Earlier runs that the performance report cites, kept as evidence. All were measured on 2026-10-14 on the same laptop,
on mains power.

| Run | What it shows |
|---|---|
| [pool-6+4-full-matrix](history/pool-6+4-full-matrix/results.md) | The full no-op matrix with database pools smaller than a worker's slots, before the fix |
| [pool-ab-8-workers-pool-6+4](history/pool-ab-8-workers-pool-6+4/results.md), [pool-ab-8-workers-pool-10+10](history/pool-ab-8-workers-pool-10+10/results.md) | The same eight-worker runs with the small and the default pool |
| [cold-start-before-pool-warm-up](history/cold-start-before-pool-warm-up/results.md), [cold-start-after-pool-warm-up](history/cold-start-after-pool-warm-up/results.md) | Three bursts on freshly started workers, before and after workers open their pool at start-up |
| [cpu-bound-smoke](history/cpu-bound-smoke/results.md) | 200 CPU-bound tasks on one worker, with memory |
| [recovery-smoke-1](history/recovery-smoke-1/results.md), [recovery-smoke-2](history/recovery-smoke-2/results.md) | A worker killed under load: entries read but unclaimed (first), tasks running (second, twice) |

## Running the harness

It needs PostgreSQL and Redis (`python scripts/devstack.py up`). It wipes the `flowforge_test` database and Redis
database 2, never the development data, and must not run at the same time as the integration or chaos tests, which
use the same database. On Windows it keeps the machine awake while it runs; elsewhere, make sure the machine does not
sleep, because a paused run is discarded. Results describe the machine they were measured on, so run the harness on
your own hardware before planning capacity.
