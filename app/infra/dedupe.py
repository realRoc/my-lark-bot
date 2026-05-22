"""Simple in-memory message dedupe for retry protection."""

from __future__ import annotations

import asyncio
import time


class MessageDedupeStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def mark_if_new(self, message_id: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            expired = [key for key, expires_at in self._seen.items() if expires_at <= now]
            for key in expired:
                self._seen.pop(key, None)
            if message_id in self._seen:
                return False
            self._seen[message_id] = now + self._ttl_seconds
            return True
