"""Task priorities, queue naming and priority-band stream selection.

Redis Streams has no native priority, so each queue is split into three streams (bands). Workers choose which band
to read first using smooth weighted round-robin (the algorithm nginx uses for upstream weights): with weights
6/3/1 the high band is tried first 60% of the time, but the low band is still tried first 10% of the time, so a
steady stream of high-priority work cannot starve low-priority work indefinitely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum

MIN_PRIORITY = 0
MAX_PRIORITY = 9
DEFAULT_PRIORITY = 5
DEFAULT_QUEUE = "default"

QUEUE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_QUEUE_RE = re.compile(QUEUE_NAME_PATTERN)


class PriorityBand(IntEnum):
    HIGH = 0
    NORMAL = 1
    LOW = 2


BAND_WEIGHTS: dict[PriorityBand, int] = {
    PriorityBand.HIGH: 6,
    PriorityBand.NORMAL: 3,
    PriorityBand.LOW: 1,
}


def validate_priority(priority: int) -> int:
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise ValueError(f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}")
    return priority


def band_for_priority(priority: int) -> PriorityBand:
    validate_priority(priority)
    if priority >= 7:
        return PriorityBand.HIGH
    if priority >= 4:
        return PriorityBand.NORMAL
    return PriorityBand.LOW


def validate_queue_name(name: str) -> str:
    if not _QUEUE_RE.fullmatch(name):
        raise ValueError(
            "queue name must be 1-63 chars of lowercase letters, digits, '-' or '_' and start alphanumeric"
        )
    return name


def stream_key(queue: str, band: PriorityBand, prefix: str = "ff") -> str:
    return f"{prefix}:q:{queue}:{band.name.lower()}"


def stream_keys_for_queue(queue: str, prefix: str = "ff") -> list[str]:
    return [stream_key(queue, band, prefix) for band in PriorityBand]


@dataclass(slots=True)
class SmoothWeightedRoundRobin:
    """Deterministic smooth weighted round-robin over priority bands."""

    weights: dict[PriorityBand, int] = field(default_factory=lambda: dict(BAND_WEIGHTS))
    _current: dict[PriorityBand, int] = field(init=False)

    def __post_init__(self) -> None:
        if not self.weights or any(w <= 0 for w in self.weights.values()):
            raise ValueError("weights must be positive")
        self._current = dict.fromkeys(self.weights, 0)

    def next_band(self) -> PriorityBand:
        total = sum(self.weights.values())
        for band, weight in self.weights.items():
            self._current[band] += weight
        chosen = max(self._current, key=lambda b: (self._current[b], -int(b)))
        self._current[chosen] -= total
        return chosen

    def read_order(self) -> list[PriorityBand]:
        """Band to try first this round, followed by the rest in strict priority order."""
        first = self.next_band()
        return [first, *[b for b in sorted(self.weights) if b != first]]
