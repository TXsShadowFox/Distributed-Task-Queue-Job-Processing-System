"""Cron expressions with explicit IANA time zones and DST semantics.

Fire times are computed in the schedule's local time zone and stored/compared in UTC. DST behaviour
(verified by unit tests, America/New_York 2026):

* **Spring forward** — a job whose local time does not exist (``30 2 * * *`` on 2026-03-08) runs once at the first
  valid instant after the gap (03:00 EDT). Sub-hourly jobs collapse the skipped hour into that instant.
* **Fall back** — a fixed-time job inside the repeated hour (``30 1 * * *`` on 2026-11-01) runs **once**, at the
  first occurrence. Jobs with a wildcard hour (``0 * * * *``) run at both 01:00 instants, because they are
  genuinely one hour apart.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

MAX_EXPRESSION_LENGTH = 120

ALIASES: dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class CronError(ValueError):
    """Invalid cron expression or time zone."""


def validate_timezone(name: str) -> ZoneInfo:
    if not name or len(name) > 64:
        raise CronError("invalid time zone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"unknown time zone {name!r}") from None


def normalize_expression(expression: str) -> str:
    expr = " ".join(expression.strip().split())
    if not expr:
        raise CronError("cron expression is empty")
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise CronError("cron expression is too long")
    expr = ALIASES.get(expr.lower(), expr)
    if len(expr.split(" ")) != 5:
        raise CronError(
            "cron expression must have 5 fields: minute hour day-of-month month day-of-week"
        )
    return expr


def validate_cron(expression: str) -> str:
    """Return the normalized expression or raise ``CronError``."""
    expr = normalize_expression(expression)
    try:
        CronSim(expr, datetime(2000, 1, 1, tzinfo=UTC))
    except CronSimError as exc:
        raise CronError(f"invalid cron expression: {exc}") from None
    return expr


def next_fire_times(
    expression: str, timezone: str, after: datetime, count: int = 5
) -> list[datetime]:
    """The next ``count`` fire times strictly after ``after``, returned as aware UTC datetimes."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    if not 1 <= count <= 100:
        raise ValueError("count must be between 1 and 100")
    expr = validate_cron(expression)
    tz = validate_timezone(timezone)
    it = CronSim(expr, after.astimezone(tz))
    # cronsim iterates indefinitely for valid expressions, so islice always yields `count` items.
    return [fire.astimezone(UTC) for fire in itertools.islice(it, count)]


def next_fire_time(expression: str, timezone: str, after: datetime) -> datetime:
    return next_fire_times(expression, timezone, after, 1)[0]


def describe(expression: str) -> str:
    """Human-readable description, e.g. ``At 09:00 on Monday through Friday``."""
    expr = validate_cron(expression)
    return str(CronSim(expr, datetime(2000, 1, 1, tzinfo=UTC)).explain())


def next_offset_change(tz: ZoneInfo, after: datetime, horizon_days: int = 400) -> datetime | None:
    """The first instant after ``after`` at which ``tz`` changes its UTC offset (a DST transition), in UTC.

    ``None`` when the zone keeps one offset for the next ``horizon_days``. Scans day by day, then bisects the day of
    the change down to the minute.
    """
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    start = after.astimezone(UTC)
    base = start.astimezone(tz).utcoffset()
    previous = start
    for day in range(1, horizon_days + 1):
        probe = start + timedelta(days=day)
        if probe.astimezone(tz).utcoffset() != base:
            low, high = previous, probe  # invariant: low has the old offset, high the new one
            while high - low > timedelta(minutes=1):
                middle = low + (high - low) / 2
                if middle.astimezone(tz).utcoffset() == base:
                    low = middle
                else:
                    high = middle
            return high.replace(second=0, microsecond=0)
        previous = probe
    return None


def format_offset(offset: timedelta | None) -> str:
    """``timedelta(hours=5, minutes=30)`` → ``+05:30``."""
    minutes = int((offset or timedelta()).total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{mins:02d}"
