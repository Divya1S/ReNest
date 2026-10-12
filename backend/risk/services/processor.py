"""The payment processor boundary.

Risk Intelligence is a sandbox: no real money moves. The SandboxProcessor
below stands where a real acquirer/PSP integration would sit, behind the same
narrow interface (`charge(reference, amount, currency) -> ProcessorResult`),
so the surrounding reliability machinery (idempotent reference, bounded
retries, terminal failure states) is exercised for real even though the
processor itself is simulated.

Like a test-mode card number, the caller can ask for specific outcomes so the
failure paths are demonstrable and testable:

    sandbox_behavior = "succeed"              default
                     = "fail_transient_once"  first attempt raises TransientError,
                                              the retry succeeds
                     = "fail_transient"       every attempt raises TransientError
                                              (exhausts retries -> payment.failed)
                     = "decline"              PermanentError: never retried
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .retry import PermanentError, TransientError

SANDBOX_BEHAVIORS = ("succeed", "fail_transient_once", "fail_transient", "decline")


@dataclass(frozen=True)
class ProcessorResult:
    reference: str
    accepted: bool
    message: str = ""


class PaymentProcessor(Protocol):
    name: str

    def charge(self, *, reference: str, amount: Decimal, currency: str, behavior: str = "succeed") -> ProcessorResult: ...


class SandboxProcessor:
    """Deterministic in-process processor. Thread-safe; remembers which
    references have already been charged so a retried charge with the same
    reference is a no-op, exactly as a real processor's idempotency key would
    make it."""

    name = "sandbox"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charged: dict[str, ProcessorResult] = {}
        self._attempts: dict[str, int] = {}

    def charge(self, *, reference: str, amount: Decimal, currency: str, behavior: str = "succeed") -> ProcessorResult:
        with self._lock:
            if reference in self._charged:
                return self._charged[reference]
            self._attempts[reference] = self._attempts.get(reference, 0) + 1
            attempt = self._attempts[reference]

        if behavior == "decline":
            raise PermanentError("Declined by processor (sandbox).")
        if behavior == "fail_transient":
            raise TransientError("Processor timeout (sandbox, permanent).")
        if behavior == "fail_transient_once" and attempt == 1:
            raise TransientError("Processor timeout (sandbox, transient).")

        digest = hashlib.sha256(f"{reference}:{amount}:{currency}".encode()).hexdigest()[:16]
        result = ProcessorResult(reference=f"sbx_{digest}", accepted=True, message="Captured (sandbox).")
        with self._lock:
            self._charged[reference] = result
        return result

    def reset(self) -> None:
        with self._lock:
            self._charged.clear()
            self._attempts.clear()


_processor: PaymentProcessor = SandboxProcessor()


def get_processor() -> PaymentProcessor:
    return _processor


def set_processor(processor: PaymentProcessor) -> None:
    """Swap the processor (tests, or a future real integration)."""
    global _processor
    _processor = processor
