"""Optional, expiring evidence. Recording must never affect primary operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any


async def _record(redis: Any, prefix: str, source: str, *, succeeded: bool) -> None:
    try:
        async with asyncio.timeout(0.25):
            stamp = datetime.now(UTC).isoformat()
            pipe = redis.pipeline(transaction=True)
            pipe.set(f"{prefix}:doctor:{source}:attempt", stamp, ex=604800)
            pipe.set(f"{prefix}:doctor:{source}:outcome", "ok" if succeeded else "unavailable", ex=604800)
            if succeeded:
                pipe.set(f"{prefix}:doctor:{source}:success", stamp, ex=604800)
            await pipe.execute()
    except Exception:
        pass
