# 0010. Modular monolith in a uv workspace

**Status:** Accepted

## Context

The API, worker and scheduler need the same domain rules and persistence code. Splitting them into services with
their own databases would add network hops and distributed transactions for no benefit at this scale.

## Decision

One repository and one database. A uv workspace layers the packages:

1. `flowforge_shared`: pure rules, no I/O.
2. `flowforge_core`: database, services and broker.
3. The three entry points (API, worker, scheduler) and the SDK.

The Next.js dashboard is a separate app that uses only the public API. One backend Docker image runs any of the
processes.

## Consequences

* Shared invariants (state machine, retry math, RBAC) are written once and unit-tested without services.
* Processes scale independently, but all of them share PostgreSQL capacity.
