from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import ServiceRecord, ServiceStatus, create_studio_app, get_studio_runtime
from relayna_studio.events import RedisStudioEventStore, StudioEventEnvelope, StudioEventStream

from relayna.api import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata
from relayna.observability import RelaynaServiceEvent, ServiceEventSourceKind, StudioEventIngestMethod


class FakePubSub:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._channels: set[str] = set()
        self._messages: list[dict[str, object]] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._redis.pubsubs.append(self)

    async def unsubscribe(self, channel: str) -> None:
        self._channels.discard(channel)

    async def close(self) -> None:
        self.closed = True

    async def get_message(self, timeout: float | None = None) -> dict[str, object] | None:
        del timeout
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(0)
        return None

    def listen(self) -> AsyncIterator[dict[str, object]]:
        async def _iterator() -> AsyncIterator[dict[str, object]]:
            while True:
                message = await self.get_message()
                if message is not None:
                    yield message
                await asyncio.sleep(0)

        return _iterator()

    def push(self, channel: str, payload: str) -> None:
        if channel in self._channels:
            self._messages.append({"type": "message", "data": payload})


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

    def publish(self, channel: str, payload: str) -> None:
        self._ops.append(("publish", (channel, payload)))

    def set(self, key: str, value: str) -> None:
        self._ops.append(("set", (key, value)))

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
            elif op == "publish":
                channel, payload = args
                for pubsub in self._redis.pubsubs:
                    pubsub.push(str(channel), str(payload))
                results.append(1)
            elif op == "set":
                key, value = args
                self._redis.values[str(key)] = str(value)
                results.append(True)
        return results


class FakeRedis:
    instances: list[FakeRedis] = []

    def __init__(self, url: str = "redis://test") -> None:
        self.url = url
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expirations: dict[str, int] = {}
        self.pubsubs: list[FakePubSub] = []
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> FakeRedis:
        return cls(url)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
        return deleted

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        for member in members:
            bucket.discard(member)
        return before - len(bucket)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        return items[int(start) : int(stop) + 1]

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def aclose(self) -> None:
        self.close_calls += 1


def make_capability_document(*, supported_routes: list[str]) -> dict[str, object]:
    return CapabilityDocument(
        relayna_version="1.3.4",
        topology_kind="shared_tasks_shared_status",
        alias_config_summary=AliasConfigSummary(),
        supported_routes=supported_routes,
        feature_flags=[],
        service_metadata=CapabilityServiceMetadata(
            service_title="Payments API",
            capability_path="/relayna/capabilities",
            discovery_source="live",
            compatibility="capabilities_v1",
        ),
    ).model_dump(mode="json")


def make_record(*, service_id: str, status: ServiceStatus = ServiceStatus.HEALTHY, capabilities=None) -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name=service_id.replace("-", " ").title(),
        base_url=f"https://{service_id}.example.test",
        environment="prod",
        tags=["core"],
        auth_mode="internal_network",
        status=status,
        capabilities=capabilities,
        last_seen_at=None,
    )


def install_service(app, record: ServiceRecord) -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(runtime.registry_store.create(record))


def test_studio_ingest_route_dedupes_and_marks_out_of_order(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    with TestClient(app) as client:
        install_service(app, make_record(service_id="payments-api"))

        response = client.post(
            "/studio/ingest/events",
            json={
                "events": [
                    {
                        "service_id": "payments-api",
                        "ingest_method": "push",
                        "event": {
                            "cursor": "evt-2",
                            "task_id": "task-123",
                            "event_type": "status.completed",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T01:00:00Z",
                            "event_id": "evt-2",
                            "payload": {"status": "completed"},
                        },
                    },
                    {
                        "service_id": "payments-api",
                        "ingest_method": "push",
                        "event": {
                            "cursor": "obs-1",
                            "task_id": "task-123",
                            "event_type": "TaskMessageReceived",
                            "source_kind": "observation",
                            "component": "consumer",
                            "timestamp": "2026-04-10T00:30:00Z",
                            "event_id": "obs-1",
                            "payload": {"detail": "received"},
                        },
                    },
                    {
                        "service_id": "payments-api",
                        "ingest_method": "push",
                        "event": {
                            "cursor": "evt-2",
                            "task_id": "task-123",
                            "event_type": "status.completed",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T01:00:00Z",
                            "event_id": "evt-2",
                            "payload": {"status": "completed"},
                        },
                    },
                ]
            },
        )

        assert response.status_code == 200
        assert response.json() == {"inserted": 2, "duplicate": 1, "invalid": 0}

        task_events = client.get("/studio/tasks/payments-api/task-123/events")
        assert task_events.status_code == 200
        assert task_events.json()["count"] == 2
        assert task_events.json()["items"][0]["event_type"] == "status.completed"
        assert task_events.json()["items"][1]["event_type"] == "TaskMessageReceived"
        assert task_events.json()["items"][1]["out_of_order"] is True

        service_events = client.get("/studio/services/payments-api/events", params={"source_kind": "observation"})
        assert service_events.status_code == 200
        assert service_events.json()["count"] == 1
        assert service_events.json()["items"][0]["source_kind"] == "observation"


def test_studio_pull_sync_ingests_service_feed(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/feed"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "items": [
                    {
                        "cursor": "evt-1",
                        "task_id": "task-123",
                        "event_type": "status.processing",
                        "source_kind": "status",
                        "component": "status",
                        "timestamp": "2026-04-10T01:00:00Z",
                        "event_id": "evt-1",
                        "payload": {"status": "processing"},
                    }
                ],
                "next_cursor": None,
            },
        )

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="payments-api",
                status=ServiceStatus.HEALTHY,
                capabilities=make_capability_document(supported_routes=["events.feed"]),
            ),
        )
        runtime = get_studio_runtime(app)
        asyncio.run(runtime.event_ingest_service.sync_registered_services())

        response = client.get("/studio/services/payments-api/events")
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["items"][0]["ingest_method"] == "pull"
        assert asyncio.run(runtime.event_store.get_pull_cursor("payments-api")) == "evt-1"


def test_studio_pull_sync_advances_existing_cursor(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.path == "/events/feed"
        if request_count == 1:
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "items": [
                        {
                            "cursor": "evt-1",
                            "task_id": "task-123",
                            "event_type": "status.processing",
                            "source_kind": "status",
                            "component": "status",
                            "timestamp": "2026-04-10T01:00:00Z",
                            "event_id": "evt-1",
                            "payload": {"status": "processing"},
                        }
                    ],
                    "next_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 2,
                "items": [
                    {
                        "cursor": "evt-2",
                        "task_id": "task-123",
                        "event_type": "status.completed",
                        "source_kind": "status",
                        "component": "status",
                        "timestamp": "2026-04-10T02:00:00Z",
                        "event_id": "evt-2",
                        "payload": {"status": "completed"},
                    },
                    {
                        "cursor": "evt-1",
                        "task_id": "task-123",
                        "event_type": "status.processing",
                        "source_kind": "status",
                        "component": "status",
                        "timestamp": "2026-04-10T01:00:00Z",
                        "event_id": "evt-1",
                        "payload": {"status": "processing"},
                    },
                ],
                "next_cursor": None,
            },
        )

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(
            app,
            make_record(
                service_id="payments-api",
                status=ServiceStatus.HEALTHY,
                capabilities=make_capability_document(supported_routes=["events.feed"]),
            ),
        )
        runtime = get_studio_runtime(app)
        asyncio.run(runtime.event_ingest_service.sync_registered_services())
        assert asyncio.run(runtime.event_store.get_pull_cursor("payments-api")) == "evt-1"

        asyncio.run(runtime.event_ingest_service.sync_registered_services())

        response = client.get("/studio/services/payments-api/events")
        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert response.json()["items"][0]["event_id"] == "evt-2"
        assert asyncio.run(runtime.event_store.get_pull_cursor("payments-api")) == "evt-2"


@pytest.mark.asyncio
async def test_studio_event_stream_emits_live_inserted_items() -> None:
    store = RedisStudioEventStore(FakeRedis(), prefix="studio-events", ttl_seconds=60, history_maxlen=10)
    stream = StudioEventStream(event_store=store, keepalive_interval_seconds=None)
    envelope = StudioEventEnvelope(
        service_id="payments-api",
        ingest_method=StudioEventIngestMethod.PUSH,
        event=RelaynaServiceEvent(
            cursor="evt-1",
            task_id="task-123",
            event_type="status.completed",
            source_kind=ServiceEventSourceKind.STATUS,
            component="status",
            timestamp="2026-04-10T01:00:00Z",
            event_id="evt-1",
            payload={"status": "completed"},
        ),
    )

    generator = stream.stream_task("payments-api", "task-123")
    ready = await anext(generator)
    assert ready == b"event: ready\ndata: {}\n\n"

    next_chunk = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    await store.insert_event(envelope)
    chunk = await asyncio.wait_for(next_chunk, timeout=1.0)

    assert b"status.completed" in chunk
    assert b"task-123" in chunk
