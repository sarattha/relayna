from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass(slots=True)
class MemoryObservationCollector:
    """Simple in-process collector for testing and local inspection."""

    items: list[object] = field(default_factory=list)

    async def __call__(self, event: object) -> None:
        self.items.append(event)


class AsyncQueueObservationCollector:
    """Observation sink backed by an asyncio queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue(maxsize=maxsize)

    async def __call__(self, event: object) -> None:
        await self.queue.put(event)

    async def drain(self) -> list[object]:
        items: list[object] = []
        while not self.queue.empty():
            items.append(self.queue.get_nowait())
        return items


__all__ = ["AsyncQueueObservationCollector", "MemoryObservationCollector"]
