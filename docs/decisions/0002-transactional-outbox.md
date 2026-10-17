# 0002. Dispatch through a transactional outbox

**Status:** Accepted

## Context

Writing the task row and then calling `XADD` is a dual write. A crash or a Redis error between the two leaves either
a task nobody will run, or a stream entry for a task that was rolled back.

## Decision

Every state change that requires a dispatch inserts an `outbox_events` row in the same transaction. An insert
trigger calls `pg_notify('ff_outbox')`. The scheduler's relay locks unpublished rows (`FOR UPDATE SKIP LOCKED`),
publishes them with `XADD` and marks them published. Polling every second is the fallback if a notification is
missed.

## Consequences

* A dispatch is never lost and never sent for an uncommitted task.
* A crash after `XADD` and before the commit publishes the row twice. Workers discard the duplicate because their
  claim (`WHERE state = 'QUEUED'`) fails (`flowforge_duplicate_deliveries_total`).
* Dispatch latency includes one relay hop: milliseconds with `LISTEN/NOTIFY`, at most a second without it.
* Several relays can run at once without blocking each other.
