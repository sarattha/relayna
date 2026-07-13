from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import create_events_router
from relayna.observability import (
    RedisObservationStore,
    RedisServiceEventFeedStore,
    SSEKeepaliveSent,
    make_studio_observation_forwarder,
)
from relayna.observability.feed import (
    StudioObservationForwarder,
    _json_default,
    normalize_observation_feed_event,
    normalize_status_feed_event,
)
from relayna.status import RedisStatusStore


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

    def sadd(self, key: str, *values: object) -> None:
        self._ops.append(("sadd", (key, *values)))

    def publish(self, channel: str, payload: str) -> None:
        self._ops.append(("publish", (channel, payload)))

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            if op == "lpush":
                key, payload = args
                self._redis.lists.setdefault(str(key), []).insert(0, str(payload))
                results.append(1)
            elif op == "ltrim":
                key, start, stop = args
                items = self._redis.lists.get(str(key), [])
                self._redis.lists[str(key)] = items[int(start) : int(stop) + 1]
                results.append(True)
            elif op == "expire":
                key, ttl = args
                self._redis.expirations[str(key)] = int(ttl)
                results.append(True)
            elif op == "sadd":
                key, *values = args
                members = self._redis.sets.setdefault(str(key), set())
                members.update(str(value) for value in values)
                results.append(len(values))
            elif op == "publish":
                results.append(1)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.cursor_read_counts: list[int] = []
        self.payload_read_counts: list[int] = []

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        return items[int(start) : int(stop) + 1]

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> int | list[int | str]:
        if "ZADD" not in script:
            assert "ZREVRANGEBYSCORE" in script
            assert numkeys == 2
            index_key, payloads_key = [str(value) for value in keys_and_args[:numkeys]]
            after = str(keys_and_args[numkeys])
            page_size = int(keys_and_args[numkeys + 1])
            index = self.sorted_sets.get(index_key, {})
            if after and after in index:
                members = [member for member in self._ordered_members(index_key) if index[member] < index[after]][
                    : page_size + 1
                ]
            else:
                members = self._ordered_members(index_key)[: page_size + 1]
            self.cursor_read_counts.append(len(members))
            self.payload_read_counts.append(len(members))
            payloads = self.hashes.get(payloads_key, {})
            result: list[int | str] = [1]
            for member in members:
                payload = payloads.get(member)
                if payload is None:
                    return [0, member]
                result.extend((member, payload))
            return result

        assert numkeys == 4
        dedupe_key, sequence_key, index_key, payloads_key = [str(value) for value in keys_and_args[:numkeys]]
        serialized, cursor = [str(value) for value in keys_and_args[numkeys : numkeys + 2]]
        maxlen = int(keys_and_args[numkeys + 2])
        ttl_seconds = int(keys_and_args[numkeys + 3])
        if dedupe_key in self.values:
            return 0
        self.values[dedupe_key] = "1"
        if ttl_seconds:
            self.expirations[dedupe_key] = ttl_seconds

        sequence = int(self.values.get(sequence_key, "0")) + 1
        self.values[sequence_key] = str(sequence)
        index = self.sorted_sets.setdefault(index_key, {})
        index[cursor] = float(sequence)
        self.hashes.setdefault(payloads_key, {})[cursor] = serialized
        excess = len(index) - maxlen
        if excess > 0:
            evicted = sorted(index, key=lambda member: (index[member], member))[:excess]
            for member in evicted:
                del index[member]
                self.hashes[payloads_key].pop(member, None)
        if ttl_seconds:
            self.expirations[sequence_key] = ttl_seconds
            self.expirations[index_key] = ttl_seconds
            self.expirations[payloads_key] = ttl_seconds
        return 1

    def register_script(self, script: str):
        async def execute(*, keys: list[str], args: list[object]) -> int | list[int | str]:
            return await self.eval(script, len(keys), *keys, *args)

        return execute

    def _ordered_members(self, key: str) -> list[str]:
        index = self.sorted_sets.get(key, {})
        return sorted(index, key=lambda member: (index[member], member), reverse=True)

    async def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        members = self._ordered_members(key)[int(start) : int(stop) + 1]
        self.cursor_read_counts.append(len(members))
        return members

    async def zscore(self, key: str, member: str) -> float | None:
        return self.sorted_sets.get(key, {}).get(member)

    async def zrevrangebyscore(
        self,
        key: str,
        max_score: str,
        min_score: str,
        *,
        start: int,
        num: int,
    ) -> list[str]:
        assert max_score.startswith("(")
        assert min_score == "-inf"
        maximum = float(max_score[1:])
        index = self.sorted_sets.get(key, {})
        members = [member for member in self._ordered_members(key) if index[member] < maximum]
        page = members[int(start) : int(start) + int(num)]
        self.cursor_read_counts.append(len(page))
        return page

    async def hmget(self, key: str, members: list[str]) -> list[str | None]:
        self.payload_read_counts.append(len(members))
        values = self.hashes.get(key, {})
        return [values.get(member) for member in members]

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


def test_service_event_feed_route_merges_status_and_observations() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        feed_store = RedisServiceEventFeedStore(redis, prefix="feed", ttl_seconds=60, feed_maxlen=10)
        status_store = RedisStatusStore(
            redis,
            prefix="status",
            ttl_seconds=60,
            history_maxlen=10,
            service_event_store=feed_store,
        )
        observation_store = RedisObservationStore(
            redis,
            prefix="obs",
            ttl_seconds=60,
            history_maxlen=10,
            service_event_store=feed_store,
        )

        await status_store.set_history(
            "task-123",
            {
                "task_id": "task-123",
                "status": "completed",
                "event_id": "evt-1",
                "timestamp": "2026-04-10T01:00:00Z",
            },
        )
        await observation_store.set_event(SSEKeepaliveSent(task_id="task-123"))

        app = FastAPI()
        app.include_router(create_events_router(service_event_store=feed_store))
        client = TestClient(app)

        first_page = client.get("/events/feed", params={"limit": 1})
        assert first_page.status_code == 200
        assert first_page.json()["count"] == 1
        assert first_page.json()["items"][0]["source_kind"] == "observation"
        assert first_page.json()["next_cursor"] == first_page.json()["items"][0]["cursor"]

        second_page = client.get("/events/feed", params={"after": first_page.json()["next_cursor"], "limit": 10})
        assert second_page.status_code == 200
        assert second_page.json()["count"] == 1
        assert second_page.json()["items"][0]["source_kind"] == "status"
        assert second_page.json()["items"][0]["event_type"] == "status.completed"
        assert second_page.json()["items"][0]["task_id"] == "task-123"

    import asyncio

    asyncio.run(scenario())


def test_studio_observation_forwarder_retries_pending_batch_after_http_error() -> None:
    async def scenario() -> None:
        request_payloads: list[dict[str, object]] = []
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            request_payloads.append(json.loads(request.content.decode("utf-8")))
            if request_count == 1:
                return httpx.Response(503, json={"detail": "unavailable"})
            return httpx.Response(200, json={"inserted": 2, "duplicate": 0, "invalid": 0})

        forwarder = make_studio_observation_forwarder(
            studio_base_url="https://studio.example.test",
            service_id="payments-api",
            batch_size=10,
            client_factory=lambda timeout: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                timeout=timeout,
            ),
        )

        await forwarder(SSEKeepaliveSent(task_id="task-1"))
        await forwarder.flush()
        await forwarder(SSEKeepaliveSent(task_id="task-2"))
        await forwarder.flush()

        assert len(request_payloads) == 2
        assert len(request_payloads[0]["events"]) == 1
        assert len(request_payloads[1]["events"]) == 2
        assert [item["event"]["task_id"] for item in request_payloads[1]["events"]] == ["task-1", "task-2"]

    import asyncio

    asyncio.run(scenario())


def test_service_event_feed_edge_paths_and_automatic_forwarder_flush() -> None:
    async def scenario() -> None:
        assert _json_default(datetime(2026, 1, 1, tzinfo=UTC)).startswith("2026-01-01")
        try:
            _json_default(object())
        except TypeError:
            pass
        else:
            raise AssertionError("non-datetime values must fail JSON normalization")

        assert normalize_status_feed_event({}) is None
        status = normalize_status_feed_event(
            {
                "task_id": 7,
                "status": None,
                "meta": {"parent_task_id": " parent "},
                "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )
        assert status is not None
        assert status.event_type == "status.unknown"
        assert status.parent_task_id == "parent"
        assert len(status.cursor) == 64
        assert normalize_observation_feed_event(object()) is None
        assert normalize_observation_feed_event(type("NoTask", (), {})()) is None

        redis = FakeRedis()
        store = RedisServiceEventFeedStore(redis, prefix="edge", ttl_seconds=None, feed_maxlen=250)
        assert await store.add_status_event({}) is False
        assert await store.add_observation_event(object()) is False
        assert (await store.get_feed()).items == []
        for index in range(105):
            assert await store.add_status_event({"task_id": f"task-{index}", "event_id": f"event-{index}"}) is True
        assert await store.add_status_event({"task_id": "duplicate", "event_id": "event-1"}) is False
        first = await store.get_feed(limit=1)
        assert first.next_cursor is not None
        deep = await store.get_feed(after="event-4", limit=2)
        assert [item.event_id for item in deep.items] == ["event-3", "event-2"]
        fallback = await store.get_feed(after="missing", limit=1)
        assert fallback.items[0].event_id == "event-104"
        assert len(redis.sorted_sets[store.feed_key()]) == 105
        assert len(redis.hashes[store.feed_payloads_key()]) == 105

        posts: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={})

        forwarder = StudioObservationForwarder(
            studio_base_url="https://studio.example/",
            service_id=" service ",
            batch_size=1,
            client_factory=lambda timeout: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                timeout=timeout,
            ),
        )
        await forwarder.flush()
        await forwarder(object())
        await forwarder(SSEKeepaliveSent(task_id="task"))
        assert len(posts) == 1
        assert posts[0]["events"][0]["service_id"] == "service"

    import asyncio

    asyncio.run(scenario())


def test_service_event_feed_bounds_deep_and_missing_cursor_reads() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceEventFeedStore(redis, prefix="bounded", ttl_seconds=60, feed_maxlen=5000)
        for index in range(5000):
            assert await store.add_status_event({"task_id": f"task-{index}", "event_id": f"event-{index}"})

        redis.cursor_read_counts.clear()
        redis.payload_read_counts.clear()
        head = await store.get_feed(limit=100)
        assert [item.event_id for item in head.items[:2]] == ["event-4999", "event-4998"]
        assert head.next_cursor == "event-4900"
        assert redis.cursor_read_counts == [101]
        assert redis.payload_read_counts == [101]

        redis.cursor_read_counts.clear()
        redis.payload_read_counts.clear()
        deep = await store.get_feed(after="event-2500", limit=100)
        assert [item.event_id for item in deep.items[:2]] == ["event-2499", "event-2498"]
        assert deep.next_cursor == "event-2400"
        assert redis.cursor_read_counts == [101]
        assert redis.payload_read_counts == [101]

        redis.cursor_read_counts.clear()
        redis.payload_read_counts.clear()
        missing = await store.get_feed(after="missing", limit=100)
        assert [item.event_id for item in missing.items[:2]] == ["event-4999", "event-4998"]
        assert missing.next_cursor == "event-4900"
        assert redis.cursor_read_counts == [101]
        assert redis.payload_read_counts == [101]

    import asyncio

    asyncio.run(scenario())


def test_service_event_feed_atomically_trims_index_and_payloads() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceEventFeedStore(redis, prefix="trimmed", ttl_seconds=30, feed_maxlen=3)
        for index in range(5):
            assert await store.add_status_event({"task_id": f"task-{index}", "event_id": f"event-{index}"})

        assert store.feed_key() == "trimmed:feed:index"
        assert store.feed_payloads_key() == "trimmed:feed:payloads"
        assert store.feed_sequence_key() == "trimmed:feed:sequence"
        assert set(redis.sorted_sets[store.feed_key()]) == {"event-2", "event-3", "event-4"}
        assert set(redis.hashes[store.feed_payloads_key()]) == {"event-2", "event-3", "event-4"}
        assert redis.expirations[store.feed_key()] == 30
        assert redis.expirations[store.feed_payloads_key()] == 30
        assert redis.expirations[store.feed_sequence_key()] == 30
        assert redis.expirations[store.event_key("event-4")] == 30
        assert [item.event_id for item in (await store.get_feed(limit=10)).items] == ["event-4", "event-3", "event-2"]

    import asyncio

    asyncio.run(scenario())


def test_service_event_feed_rejects_inconsistent_index_payloads() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceEventFeedStore(redis, prefix="corrupt", ttl_seconds=None, feed_maxlen=10)
        assert await store.add_status_event({"task_id": "task", "event_id": "event-1"})
        del redis.hashes[store.feed_payloads_key()]["event-1"]
        with pytest.raises(RuntimeError, match="payload is missing"):
            await store.get_feed()

        mismatched = normalize_status_feed_event({"task_id": "task", "event_id": "different"})
        assert mismatched is not None
        redis.hashes[store.feed_payloads_key()]["event-1"] = mismatched.model_dump_json()
        with pytest.raises(RuntimeError, match="payload cursor mismatch"):
            await store.get_feed()

    import asyncio

    asyncio.run(scenario())

    with pytest.raises(RuntimeError, match="empty script response"):
        RedisServiceEventFeedStore._parse_page([])
    with pytest.raises(RuntimeError, match="cursor 'unknown'"):
        RedisServiceEventFeedStore._parse_page([0])
    with pytest.raises(RuntimeError, match="cursor 'event-1'"):
        RedisServiceEventFeedStore._parse_page([0, b"event-1"])
    with pytest.raises(RuntimeError, match="incomplete cursor/payload pair"):
        RedisServiceEventFeedStore._parse_page([1, "event-1"])
    with pytest.raises(RuntimeError, match="invalid cursor/payload value"):
        RedisServiceEventFeedStore._parse_page([1, 7, "payload"])
