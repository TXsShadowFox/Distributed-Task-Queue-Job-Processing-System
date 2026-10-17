"""Exceptions handler authors raise to steer retry behaviour.

* Any unexpected exception is treated as **retryable** (transient until proven otherwise).
* ``NonRetryableError`` skips the remaining retries (e.g. invalid input that will never succeed); the task goes to the
  dead-letter queue (or FAILED when ``dead_letter`` is disabled).
"""

from __future__ import annotations


class TaskError(Exception):
    retryable = True


class RetryableError(TaskError):
    retryable = True


class NonRetryableError(TaskError):
    retryable = False


class TaskCancelled(Exception):
    """Raised inside a handler (by ``ctx.check_cancelled()``) when cancellation was requested."""
