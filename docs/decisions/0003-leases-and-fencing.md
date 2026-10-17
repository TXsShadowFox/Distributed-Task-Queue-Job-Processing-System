# 0003. Leases with fencing tokens for running tasks

**Status:** Accepted

## Context

Workers crash, pause (garbage collection, VM migration, laptop sleep) and get partitioned. A task must not stay
`RUNNING` forever when its worker is gone. But a worker that was only *presumed* dead must not later overwrite the
result of the attempt that replaced it.

## Decision

A claim sets `lease_owner`, a fresh random `lease_token`, and `lease_expires_at = now() + lease_ttl_seconds`. Worker
heartbeats renew the leases of their running tasks. The reaper expires overdue leases: the attempt becomes `LOST`
and the normal retry policy applies. Every write from an attempt includes its token in the `WHERE` clause. A mismatch
is rejected and recorded as a `FENCED` event.

## Consequences

* Crash detection takes up to one lease period plus one reaper interval (30 s + 5 s by default), which is tunable.
* Zombie writes are harmless and visible (`flowforge_fenced_writes_total`).
* A handler that blocks the event loop for longer than the lease would lose it. CPU-bound task types run in a
  process pool for this reason.
