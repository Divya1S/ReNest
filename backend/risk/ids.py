"""Public identifiers for risk entities.

External ids are ULID-style: 26 Crockford base32 characters, the first ten
encoding milliseconds since the epoch so ids sort by creation time, the rest
random. Prefixed like Stripe's (`txn_`, `evt_`, `sim_`) so a reviewer can tell
what an id refers to at a glance and a log line is never ambiguous.
"""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    timestamp = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    return _encode(timestamp, 10) + _encode(randomness, 16)


def public_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"
