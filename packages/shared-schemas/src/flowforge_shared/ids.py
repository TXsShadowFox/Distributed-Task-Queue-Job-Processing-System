"""Time-ordered identifiers (UUIDv7, RFC 9562).

Task IDs are UUIDv7 so B-tree inserts are append-mostly (better index locality than random UUIDv4) and IDs sort
by creation time. Within one millisecond a 12-bit counter keeps IDs from this process strictly increasing
(RFC 9562 §6.2 method 1); on counter overflow the timestamp is advanced by one millisecond.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0
_COUNTER_MAX = 0xFFF


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _counter = secrets.randbits(11)  # random start, leaves headroom before overflow
        else:
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = secrets.randbits(11)
        ts_ms = _last_ms
        counter = _counter
    rand_b = secrets.randbits(62)
    value = (ts_ms & 0xFFFF_FFFF_FFFF) << 80 | 0x7 << 76 | counter << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the Unix-millisecond timestamp embedded in a UUIDv7."""
    if value.version != 7:
        raise ValueError("not a UUIDv7")
    return value.int >> 80
