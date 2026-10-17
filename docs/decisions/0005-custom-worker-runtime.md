# 0005. A custom asyncio worker runtime instead of Celery/RQ

**Status:** Accepted

## Context

The requirements call for custom workers with leases, fencing, cooperative cancellation, graceful drain, per-attempt
logs, progress reporting and priority bands, all tied to the PostgreSQL state machine. Celery and RQ keep state in
the broker and acknowledge on their own terms, so most of these features would have to be rebuilt around them
anyway.

## Decision

`flowforge_worker` is an asyncio runtime:

* A consume loop reads streams up to the number of free slots.
* Each message is handled in its own task: claim, acknowledge, execute, complete.
* Background loops heartbeat, reclaim idle pending entries, flush logs, and listen for control messages (cancel,
  drain).

Handlers are typed functions registered with a Pydantic payload model. The payload's JSON Schema is published to the
catalog and enforced by the API at submission. `executor="process"` runs CPU-bound handlers in a process pool.

## Consequences

* The full lifecycle is under test, including SIGKILL and partition scenarios.
* Measured cost: about 7 ms of worker CPU per no-op task on the benchmark machine (up to 14 ms in a slower session),
  mostly SQLAlchemy and asyncio overhead. One worker process therefore handles about 140 tasks/s, and throughput
  scales by adding processes, less than linearly on a laptop
  ([performance report](../evaluation/performance-report.md)).
* We own this code, and there is no ecosystem of ready-made integrations.
