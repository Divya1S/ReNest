"""Bounded retries with exponential backoff and jitter.

Only operations that are safe to repeat go through this: the sandbox processor
charge (idempotent on our reference) and event handlers (idempotent by
contract). Nothing here retries an arbitrary callable blindly; the caller
names the exception types that mean "transient".
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

logger = logging.getLogger("risk.retry")

T = TypeVar("T")


class TransientError(Exception):
    """A failure that is expected to succeed on retry (timeout, 5xx, lock)."""


class PermanentError(Exception):
    """A failure that will not change on retry (validation, declined)."""


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.05
    max_delay: float = 1.0
    multiplier: float = 2.0
    jitter: float = 0.25
    retryable: tuple[type[BaseException], ...] = (TransientError,)
    sleep: Callable[[float], None] = field(default=time.sleep)

    def delay_for(self, attempt: int) -> float:
        raw = min(self.max_delay, self.base_delay * (self.multiplier ** (attempt - 1)))
        spread = raw * self.jitter
        return max(0.0, raw + random.uniform(-spread, spread))


@dataclass
class RetryOutcome:
    attempts: int
    errors: list[str]


def run_with_retries(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    label: str = "operation",
    outcome: RetryOutcome | None = None,
) -> T:
    """Call `operation` until it succeeds or the policy is exhausted.

    Raises the last error once attempts are used up. `outcome`, when given, is
    filled with the attempt count and error messages so callers can persist
    them (event rows record how many tries a handler took).
    """
    policy = policy or RetryPolicy()
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            result = operation()
            if outcome is not None:
                outcome.attempts = attempt
            return result
        except policy.retryable as exc:
            last = exc
            if outcome is not None:
                outcome.attempts = attempt
                outcome.errors.append(f"{type(exc).__name__}: {exc}")
            if attempt >= policy.attempts:
                break
            delay = policy.delay_for(attempt)
            logger.warning("%s failed (attempt %d/%d): %s; retrying in %.3fs", label, attempt, policy.attempts, exc, delay)
            policy.sleep(delay)
    assert last is not None
    raise last
