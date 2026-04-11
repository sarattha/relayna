from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import relayna.studio.app as studio_app
from relayna.studio import (
    CreateServiceRequest,
    LokiLogConfig,
    LokiLogProvider,
    RedisServiceRegistryStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioLogConfigError,
    StudioLogQuery,
    StudioLogQueryService,
    UpdateServiceRequest,
    create_studio_app,
    create_studio_logs_router,
    get_studio_runtime,
)


class FakeRedis:
    instances: list[FakeRedis] = []

    def __init__(self, url: str = "redis://test") -> None:
        self.url = url
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.close_calls = 0
        FakeRedis.instances.append(self)

    @classmethod
    def from_url(cls, url: str) -> FakeRedis:
        return cls(url)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
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

    async def aclose(self) -> None:
        self.close_calls += 1


class TrackingAsyncClient(httpx.AsyncClient):
    pass


def make_log_config(
    *,
    base_url: str = "https://loki.example.test",
    tenant_id: str | None = None,
    task_id_label: str | None = "task_id",
    correlation_id_label: str | None = "correlation_id",
    level_label: str | None = "level",
) -> LokiLogConfig:
    return LokiLogConfig(
        provider="loki",
        base_url=base_url,
        tenant_id=tenant_id,
        service_selector_labels={"app": "payments-api", "namespace": "prod"},
        task_id_label=task_id_label,
        correlation_id_label=correlation_id_label,
        level_label=level_label,
    )


def make_record(*, log_config: LokiLogConfig | None = None) -> ServiceRecord:
    return ServiceRecord(
        service_id="payments-api",
        name="Payments API",
        base_url="https://payments.example.test",
        environment="prod",
        tags=["core"],
        auth_mode="internal_network",
        status=ServiceStatus.HEALTHY,
        capabilities=None,
        last_seen_at=None,
        log_config=log_config,
    )


def install_service(app: FastAPI, record: ServiceRecord) -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(runtime.registry_store.create(record))


def loki_success_response() -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "app": "payments-api",
                        "namespace": "prod",
                        "task_id": "task-123",
                        "correlation_id": "corr-123",
                        "level": "info",
                    },
                    "values": [
                        ["1712797201000000000", "processed task-123"],
                        ["1712797200000000000", "received task-123"],
                    ],
                }
            ],
        },
    }


def loki_same_timestamp_response() -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "app": "payments-api",
                        "namespace": "prod",
                        "task_id": "task-123",
                        "correlation_id": "corr-123",
                        "level": "info",
                        "stream": "a",
                    },
                    "values": [
                        ["1712797201000000000", "stream-a first"],
                    ],
                },
                {
                    "stream": {
                        "app": "payments-api",
                        "namespace": "prod",
                        "task_id": "task-123",
                        "correlation_id": "corr-123",
                        "level": "info",
                        "stream": "b",
                    },
                    "values": [
                        ["1712797201000000000", "stream-b first"],
                        ["1712797200000000000", "older entry"],
                    ],
                },
            ],
        },
    }


def test_registry_service_accepts_lists_and_patches_log_config() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        service = ServiceRegistryService(store=store)
        created = await service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="https://payments.example.test",
                environment="prod",
                tags=["core"],
                auth_mode="internal_network",
                log_config=make_log_config(),
            )
        )

        assert created.log_config is not None
        assert created.log_config.provider == "loki"

        listed = await service.list_services()
        assert listed[0].log_config is not None
        assert listed[0].log_config.service_selector_labels["app"] == "payments-api"

        updated = await service.update_service(
            "payments-api",
            UpdateServiceRequest(log_config=make_log_config(level_label="severity")),
        )
        assert updated.log_config is not None
        assert updated.log_config.level_label == "severity"

        cleared = await service.update_service("payments-api", UpdateServiceRequest(log_config=None))
        assert cleared.log_config is None

    asyncio.run(scenario())


def test_loki_log_config_rejects_empty_selector_labels() -> None:
    with pytest.raises(ValidationError):
        LokiLogConfig(provider="loki", base_url="https://loki.example.test", service_selector_labels={})


def test_loki_provider_builds_expected_query_and_normalizes_entries() -> None:
    observed_query: dict[str, str] = {}
    observed_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_query["query"] = request.url.params["query"]
        observed_query["limit"] = request.url.params["limit"]
        observed_query["direction"] = request.url.params["direction"]
        observed_headers["tenant"] = request.headers.get("X-Scope-OrgID", "")
        return httpx.Response(200, json=loki_success_response())

    provider = LokiLogProvider(http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0))
    response = asyncio.run(
        provider.query_logs(
            service=make_record(log_config=make_log_config(tenant_id="tenant-a")),
            config=make_log_config(tenant_id="tenant-a"),
            query=StudioLogQuery(
                task_id="task-123",
                correlation_id="corr-123",
                level="info",
                query="processed",
                limit=1,
            ),
        )
    )

    assert observed_query["query"] == (
        '{app="payments-api",correlation_id="corr-123",level="info",namespace="prod",task_id="task-123"} |= "processed"'
    )
    assert observed_query["limit"] == "2"
    assert observed_query["direction"] == "backward"
    assert observed_headers["tenant"] == "tenant-a"
    assert response.count == 1
    assert response.items[0].message == "processed task-123"
    assert response.items[0].task_id == "task-123"
    assert response.items[0].correlation_id == "corr-123"
    assert response.items[0].fields["labels"] == {
        "app": "payments-api",
        "namespace": "prod",
        "task_id": "task-123",
        "correlation_id": "corr-123",
        "level": "info",
    }
    assert response.next_cursor == "1712797201000000000"


def test_loki_provider_omits_missing_task_and_correlation_label_filters() -> None:
    observed_query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_query["query"] = request.url.params["query"]
        return httpx.Response(200, json=loki_success_response())

    provider = LokiLogProvider(http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0))
    asyncio.run(
        provider.query_logs(
            service=make_record(log_config=make_log_config(task_id_label=None, correlation_id_label=None)),
            config=make_log_config(task_id_label=None, correlation_id_label=None),
            query=StudioLogQuery(task_id="task-123", correlation_id="corr-123", level="info", limit=10),
        )
    )

    assert observed_query["query"] == '{app="payments-api",level="info",namespace="prod"}'


def test_loki_provider_preserves_shared_timestamp_page_boundaries() -> None:
    responses = [loki_same_timestamp_response(), loki_same_timestamp_response()]
    observed_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(dict(request.url.params))
        return httpx.Response(200, json=responses.pop(0))

    provider = LokiLogProvider(http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0))
    first_page = asyncio.run(
        provider.query_logs(
            service=make_record(log_config=make_log_config()),
            config=make_log_config(),
            query=StudioLogQuery(limit=1),
        )
    )

    assert first_page.count == 1
    assert first_page.items[0].message in {"stream-a first", "stream-b first"}
    assert first_page.next_cursor == "loki:1712797201000000000:1"

    second_page = asyncio.run(
        provider.query_logs(
            service=make_record(log_config=make_log_config()),
            config=make_log_config(),
            query=StudioLogQuery(limit=1, before=first_page.next_cursor),
        )
    )

    assert observed_queries[0]["limit"] == "2"
    assert observed_queries[1]["limit"] == "3"
    assert observed_queries[1]["end"] == "1712797201000000000"
    assert second_page.count == 1
    assert second_page.items[0].message in {"stream-a first", "stream-b first"}
    assert second_page.items[0].message != first_page.items[0].message
    assert second_page.next_cursor == "1712797201000000000"


def test_studio_log_query_service_raises_for_missing_provider_mapping() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry = ServiceRegistryService(store=store)
        await store.create(make_record(log_config=make_log_config()))
        query_service = StudioLogQueryService(registry_service=registry, providers={})
        with pytest.raises(StudioLogConfigError):
            await query_service.query_service_logs("payments-api", StudioLogQuery())

    asyncio.run(scenario())


def test_service_log_route_returns_normalized_entries(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/loki/api/v1/query_range"
        return httpx.Response(200, json=loki_success_response())

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(log_config=make_log_config()))
        response = client.get("/studio/services/payments-api/logs", params={"limit": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["service_id"] == "payments-api"
    assert payload["items"][0]["message"] == "processed task-123"
    assert payload["next_cursor"] == "1712797201000000000"


def test_task_log_route_scopes_task_id_and_correlation_id(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_params["query"] = request.url.params["query"]
        observed_params["end"] = request.url.params["end"]
        return httpx.Response(200, json=loki_success_response())

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(log_config=make_log_config()))
        response = client.get(
            "/studio/tasks/payments-api/task-123/logs",
            params={"correlation_id": "corr-123", "before": "2026-04-11T10:00:00Z"},
        )

    assert response.status_code == 200
    assert 'task_id="task-123"' in observed_params["query"]
    assert 'correlation_id="corr-123"' in observed_params["query"]
    assert observed_params["end"].isdigit()


def test_service_log_route_returns_404_for_unknown_service(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    with TestClient(app) as client:
        response = client.get("/studio/services/missing/logs")

    assert response.status_code == 404


def test_service_log_route_returns_501_when_log_config_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    with TestClient(app) as client:
        install_service(app, make_record(log_config=None))
        response = client.get("/studio/services/payments-api/logs")

    assert response.status_code == 501


def test_service_log_route_returns_422_for_invalid_before(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    with TestClient(app) as client:
        install_service(app, make_record(log_config=make_log_config()))
        response = client.get("/studio/services/payments-api/logs", params={"before": "not-a-timestamp"})

    assert response.status_code == 422


def test_service_log_route_returns_502_for_provider_errors(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"status": "error"})

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(log_config=make_log_config()))
        response = client.get("/studio/services/payments-api/logs")

    assert response.status_code == 502


def test_custom_router_returns_422_for_unsupported_provider() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry = ServiceRegistryService(store=store)
        await store.create(make_record(log_config=make_log_config()))
        query_service = StudioLogQueryService(registry_service=registry, providers={})
        app = FastAPI()
        app.include_router(create_studio_logs_router(log_query_service=query_service))

        with TestClient(app) as client:
            response = client.get("/studio/services/payments-api/logs")

        assert response.status_code == 422

    asyncio.run(scenario())
