from __future__ import annotations

import asyncio
import time
from typing import Optional


class RateLimiter:
    def __init__(
        self,
        max_requests_per_minute: Optional[int] = None,
        max_requests_per_day: Optional[int] = None,
    ) -> None:
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_day = max_requests_per_day

        self._lock = asyncio.Lock()
        self._last_request_ts = 0.0
        self._day_window_start = time.time()
        self._day_count = 0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.time()

            # Reset 24h window.
            if now - self._day_window_start >= 86400:
                self._day_window_start = now
                self._day_count = 0

            if self.max_requests_per_day is not None and self._day_count >= self.max_requests_per_day:
                wait_seconds = 86400 - (now - self._day_window_start)
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                self._day_window_start = time.time()
                self._day_count = 0

            if self.max_requests_per_minute is not None and self.max_requests_per_minute > 0:
                min_interval = 60.0 / float(self.max_requests_per_minute)
                elapsed = now - self._last_request_ts
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)

            self._last_request_ts = time.time()
            self._day_count += 1
