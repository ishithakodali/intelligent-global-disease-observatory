from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._bucket: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = 0.0

    def _cleanup_stale_keys(self, now: float) -> None:
        boundary = now - self.window_seconds
        stale_keys: list[str] = []
        for key, queue in self._bucket.items():
            while queue and queue[0] < boundary:
                queue.popleft()
            if not queue:
                stale_keys.append(key)

        for key in stale_keys:
            del self._bucket[key]

    def check(self, client_key: str) -> RateLimitResult:
        now = time.time()
        if now - self._last_cleanup > self.window_seconds:
            self._cleanup_stale_keys(now)
            self._last_cleanup = now

        queue = self._bucket[client_key]
        boundary = now - self.window_seconds

        while queue and queue[0] < boundary:
            queue.popleft()

        if len(queue) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (now - queue[0])))
            return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

        queue.append(now)
        remaining = max(0, self.max_requests - len(queue))
        return RateLimitResult(allowed=True, remaining=remaining, retry_after_seconds=0)


def resolve_client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "anonymous"
