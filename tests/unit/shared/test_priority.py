import itertools
from collections import Counter

import pytest

from flowforge_shared.priority import (
    PriorityBand,
    SmoothWeightedRoundRobin,
    band_for_priority,
    stream_key,
    stream_keys_for_queue,
    validate_priority,
    validate_queue_name,
)


@pytest.mark.parametrize(
    ("priority", "band"),
    [
        (0, PriorityBand.LOW),
        (3, PriorityBand.LOW),
        (4, PriorityBand.NORMAL),
        (5, PriorityBand.NORMAL),
        (6, PriorityBand.NORMAL),
        (7, PriorityBand.HIGH),
        (9, PriorityBand.HIGH),
    ],
)
def test_band_mapping(priority: int, band: PriorityBand) -> None:
    assert band_for_priority(priority) is band


@pytest.mark.parametrize("priority", [-1, 10, 100])
def test_out_of_range_priority(priority: int) -> None:
    with pytest.raises(ValueError):
        validate_priority(priority)


@pytest.mark.parametrize("name", ["default", "reports", "emails-eu", "q_1", "a"])
def test_valid_queue_names(name: str) -> None:
    assert validate_queue_name(name) == name


@pytest.mark.parametrize("name", ["", "Default", "-x", "has space", "a" * 64, "x:y", "ünicode"])
def test_invalid_queue_names(name: str) -> None:
    with pytest.raises(ValueError):
        validate_queue_name(name)


def test_stream_keys() -> None:
    assert stream_key("default", PriorityBand.HIGH) == "ff:q:default:high"
    assert stream_keys_for_queue("reports", prefix="t") == [
        "t:q:reports:high",
        "t:q:reports:normal",
        "t:q:reports:low",
    ]


def test_swrr_matches_weights_and_interleaves() -> None:
    rr = SmoothWeightedRoundRobin()
    picks = [rr.next_band() for _ in range(100)]
    counts = Counter(picks)
    assert counts == {PriorityBand.HIGH: 60, PriorityBand.NORMAL: 30, PriorityBand.LOW: 10}
    # "smooth": the low band is never starved for more than one full cycle of 10 picks
    gaps = [i for i, b in enumerate(picks) if b is PriorityBand.LOW]
    assert max(b - a for a, b in itertools.pairwise(gaps)) <= 10


def test_read_order_contains_all_bands_once() -> None:
    rr = SmoothWeightedRoundRobin()
    for _ in range(20):
        order = rr.read_order()
        assert sorted(order) == list(PriorityBand)
        rest = order[1:]
        assert rest == sorted(rest)


def test_swrr_rejects_bad_weights() -> None:
    with pytest.raises(ValueError):
        SmoothWeightedRoundRobin(weights={PriorityBand.HIGH: 0})
