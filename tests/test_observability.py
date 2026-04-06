from __future__ import annotations

import pytest

from relayna.observability import RedisObservationStore, SSEKeepaliveSent, emit_observation, make_redis_observation_sink


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lpush(self, key: str, payload: str) -> None:
        self._ops.append(("lpush", (key, payload)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._ops.append(("ltrim", (key, start, stop)))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", (key, ttl)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            if op == "lpush":
                key, payload = args
                self._redis.history.setdefault(str(key), []).insert(0, str(payload))
                results.append(1)
            elif op == "ltrim":
                key, start, stop = args
                items = self._redis.history.get(str(key), [])
                self._redis.history[str(key)] = items[int(start) : int(stop) + 1]
                results.append(True)
            elif op == "expire":
                key, ttl = args
                self._redis.expirations[str(key)] = int(ttl)
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.history: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}
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

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.history.get(key, [])
        return items[int(start) : int(stop) + 1]


@pytest.mark.asyncio
async def test_emit_observation_is_noop_when_sink_is_none() -> None:
    await emit_observation(None, SSEKeepaliveSent(task_id="task-123"))


@pytest.mark.asyncio
async def test_emit_observation_suppresses_sink_failures() -> None:
    async def sink(event: object) -> None:
        raise RuntimeError("sink failed")

    await emit_observation(sink, SSEKeepaliveSent(task_id="task-123"))


@pytest.mark.asyncio
async def test_redis_observation_store_skips_events_without_task_id() -> None:
    redis = FakeRedis()
    store = RedisObservationStore(redis, prefix="obs", ttl_seconds=60, history_maxlen=10)

    stored = await store.set_event(object())

    assert stored is False
    assert redis.history == {}


@pytest.mark.asyncio
async def test_make_redis_observation_sink_persists_and_dedupes_events() -> None:
    redis = FakeRedis()
    store = RedisObservationStore(redis, prefix="obs", ttl_seconds=60, history_maxlen=10)
    sink = make_redis_observation_sink(store)
    event = SSEKeepaliveSent(task_id="task-123")

    await sink(event)
    await sink(event)

    history = await store.get_history("task-123")

    assert len(history) == 1
    assert history[0]["event_type"] == "SSEKeepaliveSent"
    assert redis.expirations[store.history_key("task-123")] == 60
