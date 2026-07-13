from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from relayna.observability import RedisServiceEventFeedStore


@pytest.mark.skipif(
    "RELAYNA_TEST_REDIS_URL" not in os.environ,
    reason="Set RELAYNA_TEST_REDIS_URL to run the real Redis service-event feed test.",
)
@pytest.mark.asyncio
async def test_service_event_feed_v2_against_real_redis() -> None:
    redis = Redis.from_url(os.environ["RELAYNA_TEST_REDIS_URL"])
    prefix = f"relayna-test:service-feed:{uuid4().hex}"
    store = RedisServiceEventFeedStore(redis, prefix=prefix, ttl_seconds=60, feed_maxlen=5000)
    try:
        concurrency = asyncio.Semaphore(100)

        async def add(index: int) -> bool:
            async with concurrency:
                return await store.add_status_event(
                    {
                        "task_id": f"task-{index}",
                        "event_id": f"event-{index}",
                        "status": "completed",
                    }
                )

        inserted = await asyncio.gather(*(add(index) for index in range(5000)))
        assert all(inserted)
        assert (
            await store.add_status_event({"task_id": "duplicate", "event_id": "event-1", "status": "completed"})
            is False
        )
        assert await redis.zcard(store.feed_key()) == 5000
        assert await redis.hlen(store.feed_payloads_key()) == 5000

        first_page = await store.get_feed(limit=100)
        assert first_page.count == 100
        assert first_page.next_cursor is not None

        seen = [item.cursor for item in first_page.items]
        cursor = first_page.next_cursor
        while cursor is not None:
            page = await store.get_feed(after=cursor, limit=100)
            seen.extend(item.cursor for item in page.items)
            cursor = page.next_cursor
        assert len(seen) == 5000
        assert len(set(seen)) == 5000

        missing = await store.get_feed(after="missing", limit=100)
        assert [item.cursor for item in missing.items] == [item.cursor for item in first_page.items]
        assert await redis.ttl(store.feed_key()) > 0
        assert await redis.ttl(store.feed_payloads_key()) > 0
        assert await redis.ttl(store.feed_sequence_key()) > 0

        async def read_while_trimming() -> None:
            for _ in range(100):
                page = await store.get_feed(limit=100)
                assert page.count == 100

        await asyncio.gather(
            read_while_trimming(),
            *(add(index) for index in range(5000, 5100)),
        )
        assert await redis.zcard(store.feed_key()) == 5000
        assert await redis.hlen(store.feed_payloads_key()) == 5000
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
