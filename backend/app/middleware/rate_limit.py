"""In-memory token-bucket rate limiter for external endpoints.

Used by the hardened GTM-A ``/v1/query`` surface. Keyed by ``(tenant_id,
user_id)`` so disabled/header auth modes still isolate noisy dev users.
Redis-backed replacement is a Phase 2 concern — v1 intentionally runs
inside the process to avoid a Redis dependency for the first customer.

The limiter is:

* **Bounded**: the key map is capped at :data:`_MAX_KEYS` entries and
  oldest-last buckets are evicted when the cap is hit (JPL-2 — bounded
  memory growth).
* **Thread-safe-enough**: the limiter uses a single :class:`asyncio.Lock`
  to guard bucket math; the hot path is O(1).
* **Monotonic-clock based**: uses ``time.monotonic`` so clock skew or
  NTP jumps cannot be used to rapidly refill tokens.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass


_MAX_KEYS = 4096  # JPL-2: bound dictionary growth


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    """Simple per-key token bucket with a monotonic refill rate.

    Args:
        capacity: maximum tokens a bucket can hold (burst allowance).
        refill_per_sec: tokens added per second toward ``capacity``.
    """

    def __init__(self, capacity: int, refill_per_sec: float):
        assert capacity >= 1, "capacity must be >= 1"
        assert refill_per_sec > 0, "refill_per_sec must be positive"
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = asyncio.Lock()

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.last_refill)
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_sec)
        bucket.last_refill = now

    async def acquire(self, key: str, cost: float = 1.0) -> bool:
        """Take ``cost`` tokens from the bucket for ``key``. Returns True
        on success, False if the bucket is exhausted."""
        assert cost > 0, "cost must be positive"
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, last_refill=now)
                self._buckets[key] = bucket
                # Evict oldest to preserve JPL-2 bound.
                while len(self._buckets) > _MAX_KEYS:
                    self._buckets.popitem(last=False)
            else:
                # Keep recently-used keys at the end of the ordered map.
                self._buckets.move_to_end(key)
                self._refill(bucket, now)
            if bucket.tokens < cost:
                return False
            bucket.tokens -= cost
            return True

    def reset(self) -> None:
        """Drop all buckets (tests only)."""
        self._buckets.clear()
