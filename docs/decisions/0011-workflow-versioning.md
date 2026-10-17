# 0011. Immutable workflow versions; runs pin their version

**Status:** Accepted

## Context

Editing a workflow while runs are in flight must not change what those runs execute, and saves from two editors must
not silently overwrite each other.

## Decision

Each save creates a new immutable version (its nodes and edges). Passing `expected_version` on save gives optimistic
concurrency: a stale save answers `409` with the latest version number. A run references the version it started
with. Old versions stay browsable and can be restored as a new version.

## Consequences

* Run history always shows the graph that actually ran.
* Storage grows with each save. Versions are small (at most 200 nodes) and are kept for the workflow's lifetime.
