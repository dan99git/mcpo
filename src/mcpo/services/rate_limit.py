"""In-memory sliding-window rate limiter for model API keys.

Deliberately NOT persisted: rate limiting is a hot-path concern and writing the
JSON key store on every request would serialise all inference behind a file
lock. This limiter lives in process memory, keyed by the authenticated key id,
and is a no-op unless a positive per-minute limit is configured
(MCPO_MODEL_KEY_RPM). Per-worker: with multiple worker processes each holds its
own window, so the effective ceiling is limit * workers — documented, not a bug.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict

_WINDOW_SECONDS = 60.0


class SlidingWindowRateLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit = max(0, int(limit_per_minute))
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, identity: str, *, now: float | None = None) -> tuple[bool, int, float]:
        """Record a hit for identity. Returns (allowed, remaining, retry_after).

        retry_after is seconds until the oldest in-window hit ages out (0 when
        allowed). When disabled, always allows with remaining=-1.
        """
        if not self.enabled:
            return True, -1, 0.0
        current = time.monotonic() if now is None else now
        cutoff = current - _WINDOW_SECONDS
        with self._lock:
            window = self._hits.get(identity)
            if window is None:
                window = deque()
                self._hits[identity] = window
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self.limit:
                retry_after = window[0] + _WINDOW_SECONDS - current
                return False, 0, max(0.0, retry_after)
            window.append(current)
            return True, self.limit - len(window), 0.0

    def reset(self, identity: str | None = None) -> None:
        with self._lock:
            if identity is None:
                self._hits.clear()
            else:
                self._hits.pop(identity, None)


_limiter_lock = threading.Lock()
_limiter: SlidingWindowRateLimiter | None = None


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Process-wide limiter built from MCPO_MODEL_KEY_RPM (0/unset = disabled)."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            try:
                limit = int(os.getenv("MCPO_MODEL_KEY_RPM", "0"))
            except ValueError:
                limit = 0
            _limiter = SlidingWindowRateLimiter(limit)
        return _limiter


def reset_rate_limiter() -> None:
    """Test hook: drop the cached limiter so env changes take effect."""
    global _limiter
    with _limiter_lock:
        _limiter = None
