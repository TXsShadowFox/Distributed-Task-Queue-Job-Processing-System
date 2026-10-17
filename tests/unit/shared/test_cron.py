import itertools
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from flowforge_shared.cron import (
    CronError,
    describe,
    format_offset,
    next_fire_time,
    next_fire_times,
    next_offset_change,
    normalize_expression,
    validate_cron,
    validate_timezone,
)

NY = "America/New_York"


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_after_is_exclusive() -> None:
    assert next_fire_time("0 * * * *", "UTC", utc(2026, 1, 1, 10, 0)) == utc(2026, 1, 1, 11, 0)


def test_results_are_utc_for_local_schedule() -> None:
    # 09:00 New York in winter (EST, UTC-5) is 14:00 UTC
    [fire] = next_fire_times("0 9 * * *", NY, utc(2026, 1, 5, 0, 0), 1)
    assert fire == utc(2026, 1, 5, 14, 0)
    assert fire.tzinfo is UTC


def test_weekday_schedule_skips_weekend() -> None:
    fires = next_fire_times("0 9 * * 1-5", "UTC", utc(2026, 1, 2, 10, 0), 3)  # Fri after 09:00
    assert fires == [utc(2026, 1, 5, 9), utc(2026, 1, 6, 9), utc(2026, 1, 7, 9)]


def test_spring_forward_nonexistent_time_runs_once_after_gap() -> None:
    fires = next_fire_times("30 2 * * *", NY, utc(2026, 3, 7, 17, 0), 2)
    # 2026-03-08 02:30 does not exist in New York; runs at 03:00 EDT (07:00 UTC)
    assert fires[0] == utc(2026, 3, 8, 7, 0)
    assert fires[1] == utc(2026, 3, 9, 6, 30)  # back to 02:30 EDT


def test_fall_back_fixed_time_runs_once() -> None:
    fires = next_fire_times("30 1 * * *", NY, utc(2026, 10, 31, 16, 0), 2)
    # 01:30 occurs twice on 2026-11-01; the job runs once, at the first (EDT) occurrence
    assert fires == [utc(2026, 11, 1, 5, 30), utc(2026, 11, 2, 6, 30)]


def test_fall_back_hourly_runs_at_both_instants() -> None:
    fires = next_fire_times("0 * * * *", NY, utc(2026, 11, 1, 4, 30), 3)
    assert fires == [utc(2026, 11, 1, 5), utc(2026, 11, 1, 6), utc(2026, 11, 1, 7)]
    assert len(set(fires)) == 3


def test_fire_times_strictly_increase_across_dst() -> None:
    fires = next_fire_times("*/30 * * * *", NY, utc(2026, 3, 8, 5, 0), 20)
    assert all(a < b for a, b in itertools.pairwise(fires))


@pytest.mark.parametrize("alias", ["@daily", "@hourly", "@weekly", "@monthly", "@yearly", "@DAILY"])
def test_aliases(alias: str) -> None:
    assert len(validate_cron(alias).split()) == 5


def test_normalizes_whitespace() -> None:
    assert normalize_expression("  */5   *  * * *  ") == "*/5 * * * *"


@pytest.mark.parametrize(
    "expr",
    ["", "* * * *", "* * * * * *", "61 * * * *", "0 25 * * *", "0 0 31 2 *", "nonsense", "x" * 200],
)
def test_invalid_expressions(expr: str) -> None:
    with pytest.raises(CronError):
        validate_cron(expr)


@pytest.mark.parametrize("tz", ["Not/AZone", "", "../etc/passwd", "x" * 100])
def test_invalid_timezones(tz: str) -> None:
    with pytest.raises(CronError):
        validate_timezone(tz)


def test_valid_timezone() -> None:
    assert validate_timezone("Asia/Kolkata") == ZoneInfo("Asia/Kolkata")


def test_naive_after_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_fire_times("* * * * *", "UTC", datetime(2026, 1, 1), 1)  # noqa: DTZ001


@pytest.mark.parametrize("count", [0, 101])
def test_count_bounds(count: int) -> None:
    with pytest.raises(ValueError):
        next_fire_times("* * * * *", "UTC", utc(2026, 1, 1), count)


def test_describe() -> None:
    assert describe("0 9 * * 1-5") == "At 09:00 on Monday through Friday"


@pytest.mark.parametrize(
    ("zone", "after", "expected"),
    [
        (NY, utc(2026, 10, 11), utc(2026, 11, 1, 6, 0)),  # 02:00 EDT -> 01:00 EST
        (NY, utc(2026, 11, 2), utc(2027, 3, 14, 7, 0)),  # 02:00 EST -> 03:00 EDT
        ("Europe/London", utc(2026, 10, 11), utc(2026, 10, 25, 1, 0)),
        ("Australia/Sydney", utc(2026, 10, 11), utc(2027, 4, 3, 16, 0)),  # southern hemisphere
        ("Asia/Kolkata", utc(2026, 10, 11), None),
        ("UTC", utc(2026, 10, 11), None),
    ],
)
def test_next_offset_change(zone: str, after: datetime, expected: datetime | None) -> None:
    assert next_offset_change(ZoneInfo(zone), after) == expected


def test_next_offset_change_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="aware"):
        next_offset_change(ZoneInfo(NY), datetime(2026, 1, 1))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("offset", "text"),
    [
        (timedelta(hours=5, minutes=30), "+05:30"),
        (timedelta(hours=-4), "-04:00"),
        (timedelta(0), "+00:00"),
        (None, "+00:00"),
        (timedelta(hours=-3, minutes=-30), "-03:30"),
    ],
)
def test_format_offset(offset: timedelta | None, text: str) -> None:
    assert format_offset(offset) == text
