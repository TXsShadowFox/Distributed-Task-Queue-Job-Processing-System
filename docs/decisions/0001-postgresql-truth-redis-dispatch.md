# 0001. PostgreSQL is the source of truth; Redis Streams only dispatch

**Status:** Accepted

## Context

A task queue needs durable state (what exists, what ran, what failed) and a low-latency way to hand work to workers.
Redis is excellent at the second and weak at the first: without careful persistence settings it loses acknowledged
writes on restart, and it cannot enforce relational invariants or answer the dashboard's transactional queries.

## Decision

All task, attempt, lease, schedule and workflow state lives in PostgreSQL, guarded by constraints, a
state-transition trigger and guarded `UPDATE`s. Redis Streams carry only "task X is ready" notifications (one stream
per queue and priority band, one consumer group per queue), plus pub/sub for realtime events and the rate limiter's
counters.

## Consequences

* Losing Redis loses no work: the reconciler re-dispatches `QUEUED` tasks from PostgreSQL
  ([reliability](../architecture/reliability.md)).
* Every task costs a few PostgreSQL transactions (about three commits, measured in
  [benchmarks](../benchmarks/results.md)). On the benchmark machine, worker CPU was the bottleneck, not PostgreSQL.
* Workers need both stores. A worker partitioned from either one pauses and heals without restarting (chaos tests).
