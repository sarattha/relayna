from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import relayna.studio.app as studio_app
from relayna.api import (
    AliasConfigSummary,
    CapabilityDocument,
    CapabilityServiceMetadata,
    WorkerHeartbeatSummary,
    create_worker_health_router,
)
from relayna.observability import RelaynaServiceEvent, ServiceEventSourceKind, StudioEventIngestMethod
from relayna.studio import (
    CapabilityRefreshError,
    RedisServiceRegistryStore,
    RedisStudioEventStore,
    RedisStudioHealthStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioEventEnvelope,
    StudioHealthRefreshService,
    create_studio_app,
    get_studio_runtime,
)


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

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeCapabilityFetcher:
    def __init__(self, result: CapabilityDocument | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def fetch(self, base_url: str) -> CapabilityDocument:
        self.calls.append(base_url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_capability_document(*, supported_routes: list[str] | None = None) -> CapabilityDocument:
    return CapabilityDocument(
        relayna_version="1.3.4",
        topology_kind="shared_tasks_shared_status",
        alias_config_summary=AliasConfigSummary(),
        supported_routes=["status.latest"] if supported_routes is None else supported_routes,
        feature_flags=[],
        service_metadata=CapabilityServiceMetadata(
            service_title="Payments API",
            capability_path="/relayna/capabilities",
            discovery_source="live",
            compatibility="capabilities_v1",
        ),
    )


def make_record(
    *,
    service_id: str = "payments-api",
    status: ServiceStatus = ServiceStatus.REGISTERED,
    capabilities: dict[str, object] | None = None,
    last_seen_at: datetime | None = None,
) -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name="Payments API",
        base_url=f"https://{service_id}.example.test",
        environment="prod",
        tags=["core"],
        auth_mode="internal_network",
        status=status,
        capabilities=capabilities,
        last_seen_at=last_seen_at,
    )


async def insert_service_event(
    event_store: RedisStudioEventStore, *, service_id: str, source_kind: ServiceEventSourceKind, timestamp: str
) -> None:
    await event_store.insert_event(
        StudioEventEnvelope(
            service_id=service_id,
            ingest_method=StudioEventIngestMethod.PUSH,
            event=RelaynaServiceEvent(
                cursor=f"{source_kind}-{timestamp}",
                task_id="task-123",
                event_type="status.completed" if source_kind == ServiceEventSourceKind.STATUS else "TaskObserved",
                source_kind=source_kind,
                component="worker",
                timestamp=timestamp,
                event_id=f"{source_kind}-{timestamp}",
                payload={"status": "completed"},
            ),
        )
    )


def test_studio_event_store_tracks_service_activity_snapshot() -> None:
    async def scenario() -> None:
        event_store = RedisStudioEventStore(FakeRedis(), prefix="studio:events", ttl_seconds=60, history_maxlen=10)
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp="2026-04-10T01:00:00Z",
        )
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.OBSERVATION,
            timestamp="2026-04-10T01:05:00Z",
        )
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp="2026-04-10T00:55:00Z",
        )
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.OBSERVATION,
            timestamp="2026-04-10T01:02:00Z",
        )

        snapshot = await event_store.get_service_activity_snapshot("payments-api")

        assert snapshot.latest_status_event_at == "2026-04-10T01:00:00Z"
        assert snapshot.latest_observation_event_at == "2026-04-10T01:05:00Z"
        assert snapshot.latest_ingested_at is not None

    asyncio.run(scenario())


def test_studio_health_service_reports_disabled_state() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(store=store)
        await store.create(make_record(status=ServiceStatus.DISABLED))
        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=RedisStudioEventStore(redis),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
        )

        document = await health.get_health("payments-api")
        assert document.overall_status == "disabled"

    asyncio.run(scenario())


def test_studio_health_service_reports_stale_capability_without_refresh() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        old = datetime.now(UTC) - timedelta(seconds=600)
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(store=store)
        await store.create(
            make_record(
                status=ServiceStatus.HEALTHY,
                capabilities=make_capability_document().model_dump(mode="json"),
                last_seen_at=old,
            )
        )
        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=RedisStudioEventStore(redis),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
        )

        document = await health.get_health("payments-api")
        assert document.capability_status.state == "stale"
        assert document.overall_status == "stale"

    asyncio.run(scenario())


def test_studio_health_service_reports_stale_observation() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        fetcher = FakeCapabilityFetcher(make_capability_document())
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(store=store, capability_fetcher=fetcher)
        await store.create(make_record(status=ServiceStatus.HEALTHY))
        event_store = RedisStudioEventStore(redis, ttl_seconds=60, history_maxlen=10)
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp="2026-04-01T01:00:00Z",
        )
        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=event_store,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
        )

        document = await health.refresh_health("payments-api")
        assert document.observation_freshness.state == "stale"
        assert document.overall_status == "stale"

    asyncio.run(scenario())


def test_studio_health_service_reports_unsupported_heartbeat_and_healthy_state() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(
            store=store,
            capability_fetcher=FakeCapabilityFetcher(make_capability_document()),
        )
        await store.create(make_record(status=ServiceStatus.HEALTHY))
        event_store = RedisStudioEventStore(redis, ttl_seconds=60, history_maxlen=10)
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp=datetime.now(UTC).isoformat(),
        )
        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=event_store,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
        )

        document = await health.refresh_health("payments-api")
        assert document.worker_health.state == "unsupported"
        assert document.overall_status == "healthy"

    asyncio.run(scenario())


def test_studio_health_service_reports_stale_heartbeat() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(
            store=store,
            capability_fetcher=FakeCapabilityFetcher(
                make_capability_document(supported_routes=["status.latest", "health.workers"])
            ),
        )
        await store.create(make_record(status=ServiceStatus.HEALTHY))
        event_store = RedisStudioEventStore(redis, ttl_seconds=60, history_maxlen=10)
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp=datetime.now(UTC).isoformat(),
        )

        stale_heartbeat = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/relayna/health/workers"
            return httpx.Response(
                200,
                json={
                    "reported_at": datetime.now(UTC).isoformat(),
                    "workers": [
                        {"worker_name": "aggregation-1", "running": True, "last_heartbeat_at": stale_heartbeat}
                    ],
                },
            )

        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=event_store,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        document = await health.refresh_health("payments-api")
        assert document.worker_health.state == "stale"
        assert document.overall_status == "stale"

    asyncio.run(scenario())


def test_studio_health_service_marks_unreachable_and_preserves_last_successful_capability() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        fetcher = FakeCapabilityFetcher(make_capability_document())
        store = RedisServiceRegistryStore(redis)
        registry = ServiceRegistryService(store=store, capability_fetcher=fetcher)
        await store.create(make_record(status=ServiceStatus.HEALTHY))
        event_store = RedisStudioEventStore(redis, ttl_seconds=60, history_maxlen=10)
        await insert_service_event(
            event_store,
            service_id="payments-api",
            source_kind=ServiceEventSourceKind.STATUS,
            timestamp=datetime.now(UTC).isoformat(),
        )
        health = StudioHealthRefreshService(
            registry_service=registry,
            health_store=RedisStudioHealthStore(redis),
            activity_reader=event_store,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
        )

        initial = await health.refresh_health("payments-api")
        fetcher.result = CapabilityRefreshError("capability timeout")
        failed = await health.refresh_health("payments-api")

        assert initial.capability_status.last_successful_at is not None
        assert failed.http_status.state == "unreachable"
        assert failed.capability_status.last_successful_at == initial.capability_status.last_successful_at
        assert failed.overall_status == "unreachable"

    asyncio.run(scenario())


def test_studio_health_routes_merge_health_into_registry_and_compat_refresh(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    capability_document = make_capability_document(supported_routes=["status.latest", "health.workers"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/relayna/health/workers"
        return httpx.Response(
            200,
            json={
                "reported_at": datetime.now(UTC).isoformat(),
                "workers": [
                    {
                        "worker_name": "aggregation-1",
                        "running": True,
                        "last_heartbeat_at": datetime.now(UTC).isoformat(),
                    }
                ],
            },
        )

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        capability_fetcher=FakeCapabilityFetcher(capability_document),
        federation_client_factory=lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
    )

    with TestClient(app) as client:
        runtime = get_studio_runtime(app)
        asyncio.run(runtime.registry_store.create(make_record(status=ServiceStatus.HEALTHY)))
        asyncio.run(
            insert_service_event(
                runtime.event_store,
                service_id="payments-api",
                source_kind=ServiceEventSourceKind.STATUS,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

        refresh_response = client.post("/studio/services/payments-api/health/refresh")
        assert refresh_response.status_code == 200
        assert refresh_response.json()["overall_status"] == "healthy"

        health_response = client.get("/studio/services/payments-api/health")
        assert health_response.status_code == 200
        assert health_response.json()["worker_health"]["state"] == "healthy"

        list_response = client.get("/studio/services")
        assert list_response.status_code == 200
        assert list_response.json()["services"][0]["health"]["overall_status"] == "healthy"

        detail_response = client.get("/studio/services/payments-api")
        assert detail_response.status_code == 200
        assert detail_response.json()["health"]["worker_health"]["state"] == "healthy"

        compat_refresh = client.post("/studio/services/payments-api/refresh")
        assert compat_refresh.status_code == 200
        assert compat_refresh.json()["health"]["overall_status"] == "healthy"


def test_create_worker_health_router_serves_worker_heartbeat_payload() -> None:
    app = FastAPI()
    app.include_router(
        create_worker_health_router(
            heartbeat_provider=lambda: [
                WorkerHeartbeatSummary(
                    worker_name="aggregation-1",
                    running=True,
                    last_heartbeat_at=datetime(2026, 4, 11, 0, 0, tzinfo=UTC),
                )
            ]
        )
    )
    client = TestClient(app)

    response = client.get("/relayna/health/workers")

    assert response.status_code == 200
    assert response.json()["workers"][0]["worker_name"] == "aggregation-1"
