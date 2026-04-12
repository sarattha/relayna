from __future__ import annotations

import asyncio

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import (
    CreateServiceRequest,
    RedisServiceRegistryStore,
    RedisStudioEventStore,
    ServiceRegistryService,
    create_studio_app,
    get_studio_runtime,
)
from relayna_studio.events import StudioControlPlaneEvent, StudioEventEnvelope, StudioEventIngestService
from relayna_studio.search import (
    RedisStudioSearchStore,
    StudioSearchService,
    StudioServiceSearchQuery,
    StudioTaskSearchQuery,
    _later_iso,
)

from relayna.observability import RelaynaServiceEvent, ServiceEventSourceKind, StudioEventIngestMethod


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
                results.append(True)
            elif op == "publish":
                results.append(1)
            elif op == "set":
                key, value = args
                self._redis.values[str(key)] = str(value)
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        del ex
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
        return None


async def _create_service(registry: ServiceRegistryService, service_id: str, *, name: str = "Payments API") -> None:
    await registry.create_service(
        CreateServiceRequest(
            service_id=service_id,
            name=name,
            base_url=f"https://{service_id}.example.test",
            environment="prod",
            tags=["core", "payments"] if service_id == "payments-api" else ["core", "billing"],
            auth_mode="internal_network",
        )
    )


def _install_service(app, service_id: str, *, name: str = "Payments API") -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(_create_service(runtime.registry_service, service_id, name=name))


def test_search_service_indexes_events_and_filters_tasks() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        registry = ServiceRegistryService(store=RedisServiceRegistryStore(redis))
        event_store = RedisStudioEventStore(redis, prefix="studio:events", ttl_seconds=60, history_maxlen=20)
        search_service = StudioSearchService(
            registry_service=registry,
            event_store=event_store,
            store=RedisStudioSearchStore(redis, prefix="studio:search"),
            task_index_ttl_seconds=600,
        )
        registry.set_search_indexer(search_service)
        ingest_service = StudioEventIngestService(
            registry_service=registry,
            event_store=event_store,
            http_client=httpx.AsyncClient(),
            search_indexer=search_service,
        )
        await _create_service(registry, "payments-api")
        await _create_service(registry, "billing-api", name="Billing API")

        response = await ingest_service.ingest_events(
            [
                StudioEventEnvelope(
                    service_id="payments-api",
                    ingest_method=StudioEventIngestMethod.PULL,
                    event=RelaynaServiceEvent(
                        cursor="evt-1",
                        task_id="task-123",
                        event_type="status.processing",
                        source_kind=ServiceEventSourceKind.STATUS,
                        component="status",
                        timestamp="2026-04-10T02:00:00Z",
                        event_id="evt-1",
                        correlation_id="corr-123",
                        payload={"status": "processing", "stage": "authorize"},
                    ),
                ),
                StudioEventEnvelope(
                    service_id="billing-api",
                    ingest_method=StudioEventIngestMethod.PULL,
                    event=RelaynaServiceEvent(
                        cursor="evt-2",
                        task_id="task-999",
                        event_type="status.completed",
                        source_kind=ServiceEventSourceKind.STATUS,
                        component="status",
                        timestamp="2026-04-10T03:00:00Z",
                        event_id="evt-2",
                        payload={"status": "completed"},
                    ),
                ),
            ]
        )

        assert response.model_dump() == {"inserted": 2, "duplicate": 0, "invalid": 0}

        task_results = await search_service.search_tasks(
            StudioTaskSearchQuery(task_id="task-123", status="processing", stage="authorize")
        )
        assert task_results.count == 1
        assert task_results.items[0].service_id == "payments-api"
        assert task_results.items[0].correlation_id == "corr-123"

        service_results = await search_service.search_services(StudioServiceSearchQuery(query="pay"))
        assert service_results.count == 1
        assert service_results.items[0].service_id == "payments-api"
        assert "name" in service_results.items[0].matched_fields

        await ingest_service.http_client.aclose()

    asyncio.run(scenario())


def test_search_service_prunes_expired_tasks_and_cascades_service_delete() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        registry = ServiceRegistryService(store=RedisServiceRegistryStore(redis))
        search_store = RedisStudioSearchStore(redis, prefix="studio:search")
        search_service = StudioSearchService(
            registry_service=registry,
            event_store=RedisStudioEventStore(redis, prefix="studio:events", ttl_seconds=60, history_maxlen=20),
            store=search_store,
            task_index_ttl_seconds=600,
        )
        registry.set_search_indexer(search_service)
        await _create_service(registry, "payments-api")

        service = await registry.get_service("payments-api")
        await search_service.upsert_task_document(
            service,
            StudioControlPlaneEvent(
                service_id="payments-api",
                ingest_method=StudioEventIngestMethod.PULL,
                ingested_at="2026-04-10T01:00:00Z",
                dedupe_key="evt-1",
                task_id="task-123",
                event_type="status.completed",
                source_kind=ServiceEventSourceKind.STATUS,
                component="status",
                timestamp="2026-04-10T01:00:00Z",
                event_id="evt-1",
                payload={"status": "completed"},
            ),
        )
        document_ids = await search_store.list_task_document_ids()
        assert len(document_ids) == 1
        document = await search_store.get_task_document(next(iter(document_ids)))
        assert document is not None
        await search_store.set_task_document(document.model_copy(update={"expires_at": "2026-04-10T01:00:01Z"}))
        assert await search_service.prune_expired_task_documents() == 1
        assert (await search_service.search_tasks(StudioTaskSearchQuery(task_id="task-123"))).count == 0

        await search_service.upsert_task_document(
            service,
            StudioControlPlaneEvent(
                service_id="payments-api",
                ingest_method=StudioEventIngestMethod.PULL,
                ingested_at="2026-04-10T02:00:00Z",
                dedupe_key="evt-2",
                task_id="task-456",
                event_type="status.completed",
                source_kind=ServiceEventSourceKind.STATUS,
                component="status",
                timestamp="2026-04-10T02:00:00Z",
                event_id="evt-2",
                payload={"status": "completed"},
            ),
        )
        await registry.delete_service("payments-api")
        assert (await search_service.search_services(StudioServiceSearchQuery(query="pay"))).count == 0
        assert (await search_service.search_tasks(StudioTaskSearchQuery(task_id="task-456"))).count == 0

    asyncio.run(scenario())


def test_search_service_backfills_from_retained_events_when_index_is_empty() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        registry = ServiceRegistryService(store=RedisServiceRegistryStore(redis))
        event_store = RedisStudioEventStore(redis, prefix="studio:events", ttl_seconds=60, history_maxlen=20)
        await _create_service(registry, "payments-api")
        inserted = await event_store.insert_event(
            StudioEventEnvelope(
                service_id="payments-api",
                ingest_method=StudioEventIngestMethod.PULL,
                event=RelaynaServiceEvent(
                    cursor="evt-1",
                    task_id="task-123",
                    event_type="status.completed",
                    source_kind=ServiceEventSourceKind.STATUS,
                    component="status",
                    timestamp="2026-04-10T02:00:00Z",
                    event_id="evt-1",
                    correlation_id="corr-123",
                    payload={"status": "completed", "stage": "settled"},
                ),
            )
        )
        assert inserted is True

        search_service = StudioSearchService(
            registry_service=registry,
            event_store=event_store,
            store=RedisStudioSearchStore(redis, prefix="studio:search"),
            task_index_ttl_seconds=600,
        )
        await search_service.initialize()

        results = await search_service.search_tasks(StudioTaskSearchQuery(task_id="task-123"))
        assert results.count == 1
        assert results.items[0].correlation_id == "corr-123"
        assert results.items[0].stage == "settled"

    asyncio.run(scenario())


def test_task_search_document_ids_do_not_collide_on_colon_delimited_inputs() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        registry = ServiceRegistryService(store=RedisServiceRegistryStore(redis))
        event_store = RedisStudioEventStore(redis, prefix="studio:events", ttl_seconds=60, history_maxlen=20)
        search_store = RedisStudioSearchStore(redis, prefix="studio:search")
        search_service = StudioSearchService(
            registry_service=registry,
            event_store=event_store,
            store=search_store,
            task_index_ttl_seconds=600,
        )
        registry.set_search_indexer(search_service)

        await registry.create_service(
            CreateServiceRequest(
                service_id="svc:a",
                name="Service A",
                base_url="https://service-a.example.test",
                environment="prod",
                tags=["core", "billing"],
                auth_mode="internal_network",
            )
        )
        await registry.create_service(
            CreateServiceRequest(
                service_id="svc",
                name="Service B",
                base_url="https://service-b.example.test",
                environment="prod",
                tags=["core", "billing"],
                auth_mode="internal_network",
            )
        )

        svc_a = await registry.get_service("svc:a")
        svc_b = await registry.get_service("svc")

        await search_service.upsert_task_document(
            svc_a,
            StudioControlPlaneEvent(
                service_id="svc:a",
                ingest_method=StudioEventIngestMethod.PULL,
                ingested_at="2026-04-10T01:00:00Z",
                dedupe_key="evt-1",
                task_id="1",
                event_type="status.processing",
                source_kind=ServiceEventSourceKind.STATUS,
                component="status",
                timestamp="2026-04-10T01:00:00Z",
                event_id="evt-1",
                payload={"status": "processing"},
            ),
        )
        await search_service.upsert_task_document(
            svc_b,
            StudioControlPlaneEvent(
                service_id="svc",
                ingest_method=StudioEventIngestMethod.PULL,
                ingested_at="2026-04-10T02:00:00Z",
                dedupe_key="evt-2",
                task_id="a:1",
                event_type="status.completed",
                source_kind=ServiceEventSourceKind.STATUS,
                component="status",
                timestamp="2026-04-10T02:00:00Z",
                event_id="evt-2",
                payload={"status": "completed"},
            ),
        )

        svc_a_results = await search_service.search_tasks(StudioTaskSearchQuery(service_id="svc:a"))
        svc_b_results = await search_service.search_tasks(StudioTaskSearchQuery(service_id="svc"))

        assert svc_a_results.count == 1
        assert svc_a_results.items[0].service_id == "svc:a"
        assert svc_a_results.items[0].task_id == "1"
        assert svc_b_results.count == 1
        assert svc_b_results.items[0].service_id == "svc"
        assert svc_b_results.items[0].task_id == "a:1"
        assert len(await search_store.list_task_document_ids()) == 2

    asyncio.run(scenario())


def test_task_search_requires_a_filter_or_time_range() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        registry = ServiceRegistryService(store=RedisServiceRegistryStore(redis))
        search_service = StudioSearchService(
            registry_service=registry,
            event_store=RedisStudioEventStore(redis, prefix="studio:events", ttl_seconds=60, history_maxlen=20),
            store=RedisStudioSearchStore(redis, prefix="studio:search"),
            task_index_ttl_seconds=600,
        )
        try:
            await search_service.search_tasks(StudioTaskSearchQuery())
        except ValueError as exc:
            assert "Provide at least one task search filter" in str(exc)
        else:
            raise AssertionError("Expected task search without filters to fail.")

    asyncio.run(scenario())


def test_create_studio_app_exposes_services_search_before_service_id_route(monkeypatch) -> None:
    monkeypatch.setattr(
        studio_app, "Redis", type("RedisFactory", (), {"from_url": staticmethod(lambda url: FakeRedis())})
    )

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )

    with TestClient(app) as client:
        _install_service(app, "payments-api")

        response = client.get("/studio/services/search", params={"query": "pay"})

        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["items"][0]["service_id"] == "payments-api"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("2026-04-10T10:00:00", "2026-04-10T09:00:00Z", "2026-04-10T10:00:00Z"),
        ("2026-04-10T10:00:00Z", "2026-04-10T09:00:00", "2026-04-10T10:00:00Z"),
    ],
)
def test_parse_datetime_treats_offsetless_timestamps_as_utc(left: str, right: str, expected: str) -> None:
    assert _later_iso(left, right) == expected
