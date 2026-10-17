# 0004. At-least-once execution, exactly-once state transitions

**Status:** Accepted

## Context

Exactly-once *execution* of arbitrary side effects is impossible in a distributed system: a worker can perform the
side effect and die before recording that it did. Systems that claim otherwise either redefine the term or are
wrong.

## Decision

FlowForge guarantees three things: every task reaches exactly one terminal state, at most one attempt is recorded as
successful, and no submitted task is lost while PostgreSQL keeps its data. A handler may run more than once. Handlers
receive the task's idempotency key and attempt number so they can make side effects idempotent, and an
`Idempotency-Key` on submission makes the whole flow idempotent end to end.

## Consequences

* Neither the documentation nor the dashboard claims exactly-once execution.
* Tests assert "one successful attempt per task", never "the handler ran once" (see `tests/chaos`).
