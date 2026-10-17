# 0007. Cron fires deduplicated by idempotency key and row locks

**Status:** Accepted

## Context

Several scheduler replicas must be able to run for availability without firing a schedule twice, and fire times must
respect time zones, including daylight-saving gaps and repeated hours.

## Decision

Fire times are computed with `cronsim` in the schedule's IANA time zone and stored in UTC. A replica locks due
schedules (`FOR UPDATE SKIP LOCKED`), creates each task with the idempotency key `schedule:<id>:<fire time>`, and
advances `next_run_at` in the same transaction. Misfire policy (catch up once, or skip) and overlap policy (allow, or
skip while the previous run is active) are set per schedule.

## Consequences

* Exactly one task per fire time, even when a replica is killed mid-transaction (chaos test).
* A time that does not exist because clocks spring forward runs once, at the first valid instant. A fixed time inside
  the repeated autumn hour runs once. The schedule preview warns about upcoming clock changes in the chosen zone.
* Granularity is one minute, as with standard cron.
