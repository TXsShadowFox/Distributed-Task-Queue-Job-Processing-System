# 0006. Priority bands with smooth weighted round-robin

**Status:** Accepted

## Context

Redis Streams have no notion of priority, and strict priority ordering starves low-priority work under sustained
load.

## Decision

Priorities 0 to 9 map to three bands per queue: `high` (7–9), `normal` (4–6) and `low` (0–3), each its own stream.
Before every read, a worker orders the bands with smooth weighted round-robin (weights 6/3/1, the algorithm nginx
uses for upstreams) and reads them in that order.

## Consequences

* High-priority tasks are preferred, while low-priority tasks are still read first about 10% of the time.
* Ordering is per band, not global. Two priority-8 tasks are FIFO relative to each other, but a priority-9 task does
  not jump ahead of an older priority-7 task in the same band.
