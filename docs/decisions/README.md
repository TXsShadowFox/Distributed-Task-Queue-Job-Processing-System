# Architecture decision records

Each record states the context, the decision, and its consequences, including the costs we accepted.

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgresql-truth-redis-dispatch.md) | PostgreSQL is the source of truth; Redis Streams only dispatch | Accepted |
| [0002](0002-transactional-outbox.md) | Dispatch through a transactional outbox | Accepted |
| [0003](0003-leases-and-fencing.md) | Leases with fencing tokens for running tasks | Accepted |
| [0004](0004-at-least-once-delivery.md) | At-least-once execution, exactly-once state transitions | Accepted |
| [0005](0005-custom-worker-runtime.md) | A custom asyncio worker runtime instead of Celery/RQ | Accepted |
| [0006](0006-priority-bands.md) | Priority bands with smooth weighted round-robin | Accepted |
| [0007](0007-cron-fire-keys.md) | Cron fires deduplicated by idempotency key and row locks | Accepted |
| [0008](0008-authentication.md) | Short JWTs, rotating refresh cookies with reuse detection, hashed API keys | Accepted |
| [0009](0009-sse-realtime.md) | Server-Sent Events over fetch for realtime updates | Accepted |
| [0010](0010-modular-monolith.md) | Modular monolith in a uv workspace | Accepted |
| [0011](0011-workflow-versioning.md) | Immutable workflow versions; runs pin their version | Accepted |
| [0012](0012-native-dev-stack.md) | A native development stack that does not need Docker | Accepted |
