from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import create_events_router
from relayna.observability import (
    RedisObservationStore,
    RedisServiceEventFeedStore,
    SSEKeepaliveSent,
    make_studio_observation_forwarder,
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
        self.expirations: dict[str, int] = {}

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
