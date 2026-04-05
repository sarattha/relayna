from __future__ import annotations

import asyncio
from typing import Protocol


class IdempotencyBackend(Protocol):
    async def acquire(self, key: str) -> bool: ...

    async def release(self, key: str) -> None: ...


class InMemoryIdempotencyBackend:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._keys: set[str] = set()

    async def acquire(self, key: str) -> bool:
        async with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True

    async def release(self, key: str) -> None:
        async with self._lock:
            self._keys.discard(key)


__all__ = ["IdempotencyBackend", "InMemoryIdempotencyBackend"]
