from __future__ import annotations

import pytest

from relayna.status_store import RedisStatusStore


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, key: str, payload: str) -> None:
        self._ops.append(("lpush", (key, payload)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._ops.append(("ltrim", (key, start, stop)))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl)))

    def publish(self, channel: str, payload: str) -> None:
        self._ops.append(("publish", (channel, payload)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            if op == "lpush":
                key, payload = args
                self._redis.history.setdefault(key, []).insert(0, payload)
                results.append(1)
            elif op == "ltrim":
                key, start, stop = args
                items = self._redis.history.get(key, [])
                self._redis.history[key] = items[int(start) : int(stop) + 1]
                results.append(True)
            elif op == "expire":
                key, ttl = args
                self._redis.expirations[key] = int(ttl)
                results.append(True)
            elif op == "publish":
                channel, payload = args
                self._redis.published.append((channel, payload))
                results.append(1)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.history: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self._seen_keys: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        assert value == "1"
        if nx and key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        if ex is not None:
            self.expirations[key] = ex
        return True

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


@pytest.mark.asyncio
async def test_set_history_skips_duplicate_event_id() -> None:
    redis = FakeRedis()
    store = RedisStatusStore(redis, prefix="translation-status", ttl_seconds=60, history_maxlen=10)
    event = {"task_id": "task-1", "status": "validating", "event_id": "evt-1"}

    await store.set_history("task-1", event)
    await store.set_history("task-1", dict(event))

    assert redis.history[store.history_key("task-1")] == ['{"task_id": "task-1", "status": "validating", "event_id": "evt-1"}']
    assert redis.published == [
        (
            store.channel_name("task-1"),
            '{"task_id": "task-1", "status": "validating", "event_id": "evt-1"}',
        )
    ]


@pytest.mark.asyncio
async def test_set_history_hash_dedupes_when_event_id_missing() -> None:
    redis = FakeRedis()
    store = RedisStatusStore(redis, prefix="translation-status", ttl_seconds=60, history_maxlen=10)
    event = {"task_id": "task-1", "status": "translating", "message": "Translating content."}

    await store.set_history("task-1", event)
    await store.set_history("task-1", dict(event))

    assert len(redis.history[store.history_key("task-1")]) == 1
    assert len(redis.published) == 1
