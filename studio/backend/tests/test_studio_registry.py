from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from relayna_studio import (
    CreateServiceRequest,
    HttpCapabilityFetcher,
    RedisServiceRegistryStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioOutboundUrlPolicy,
    UpdateServiceRequest,
    create_service_registry_router,
    create_studio_app,
    normalize_base_url,
)

from relayna.api import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata


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

    async def set(self, key: str, value: str, *, nx: bool = False) -> bool:
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


class FakeCapabilityFetcher:
    def __init__(self, result: CapabilityDocument | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def fetch(self, base_url: str) -> CapabilityDocument:
        self.calls.append(base_url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeAsyncClient:
    def __init__(self, result: httpx.Response | Exception) -> None:
        self.result = result
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def get(self, url: str) -> httpx.Response:
        self.requested_urls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_capability_document(
    *,
    discovery_source: str = "live",
    compatibility: str = "capabilities_v1",
    supported_routes: list[str] | None = None,
    service_title: str | None = "Payments API",
) -> CapabilityDocument:
    return CapabilityDocument(
        relayna_version="1.3.4",
        topology_kind="shared_tasks_shared_status",
        alias_config_summary=AliasConfigSummary(),
        supported_routes=["status.latest"] if supported_routes is None else supported_routes,
        feature_flags=[],
        service_metadata=CapabilityServiceMetadata(
            service_title=service_title,
            capability_path="/relayna/capabilities",
            discovery_source=discovery_source,
            compatibility=compatibility,
        ),
    )


def make_record(
    *,
    service_id: str = "payments-api",
    base_url: str = "https://payments.example.test",
    status: ServiceStatus = ServiceStatus.REGISTERED,
    capabilities: dict[str, object] | None = None,
    last_seen_at: datetime | None = None,
) -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name="Payments API",
        base_url=base_url,
        environment="prod",
        tags=["core"],
        auth_mode="internal_network",
        status=status,
        capabilities=capabilities,
        last_seen_at=last_seen_at,
    )


def test_normalize_base_url_trims_slashes_and_lowercases_scheme_and_host() -> None:
    assert normalize_base_url(" HTTPS://Example.TEST/api/ ") == "https://example.test/api"


def test_normalize_base_url_rejects_invalid_inputs() -> None:
    for value in ("ftp://example.test", "https://example.test/path?x=1", "https://user@example.test"):
        try:
            normalize_base_url(value)
        except ValueError:
            continue
        raise AssertionError(f"Expected ValueError for {value!r}")


def test_service_registry_rejects_disallowed_literal_ip_base_urls() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry_service = ServiceRegistryService(store=store)

        for base_url in (
            "http://169.254.169.254/latest/meta-data",
            "http://127.0.0.1:8000",
            "http://10.0.0.10:8000",
        ):
            with pytest.raises(ValueError, match="allowlist"):
                await registry_service.create_service(
                    CreateServiceRequest(
                        service_id=f"svc-{base_url.split('//', 1)[1].split(':', 1)[0].replace('.', '-')}",
                        name="Unsafe Service",
                        base_url=base_url,
                        environment="prod",
                        auth_mode="internal_network",
                    )
                )

    asyncio.run(scenario())


def test_service_registry_allows_private_ip_when_network_is_allowlisted() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry_service = ServiceRegistryService(
            store=store,
            outbound_policy=StudioOutboundUrlPolicy(allowed_networks=("10.0.0.0/8",)),
        )

        created = await registry_service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="http://10.0.0.10:8000",
                environment="prod",
                auth_mode="internal_network",
            )
        )

        assert created.base_url == "http://10.0.0.10:8000"

    asyncio.run(scenario())


def test_service_registry_allows_cluster_dns_suffix_when_host_is_allowlisted() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry_service = ServiceRegistryService(
            store=store,
            outbound_policy=StudioOutboundUrlPolicy(allowed_hosts=(".svc.cluster.local",)),
        )

        created = await registry_service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="http://payments.default.svc.cluster.local:8000",
                environment="prod",
                auth_mode="internal_network",
            )
        )

        assert created.base_url == "http://payments.default.svc.cluster.local:8000"

    asyncio.run(scenario())


def test_registry_store_enforces_duplicate_env_url_and_cleans_indexes_after_delete() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        record = make_record()

        await store.create(record)

        listed = await store.list_records()
        assert [item.service_id for item in listed] == ["payments-api"]

        try:
            await store.create(make_record(service_id="payments-copy", base_url="https://payments.example.test/"))
        except Exception as exc:
            assert "already registered" in str(exc)
        else:
            raise AssertionError("Expected duplicate registration failure")

        await store.delete("payments-api")

        recreated = await store.create(make_record(service_id="payments-copy"))
        assert recreated.service_id == "payments-copy"
        assert await store.get("payments-api") is None

    asyncio.run(scenario())


def test_service_registry_service_rejects_healthy_status_patch_and_preserves_disabled_state() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        service = ServiceRegistryService(store=store)

        await service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="https://payments.example.test",
                environment="prod",
                tags=["core"],
                auth_mode="internal_network",
            )
        )

        disabled = await service.update_service("payments-api", UpdateServiceRequest(status=ServiceStatus.DISABLED))
        assert disabled.status == ServiceStatus.DISABLED

        renamed = await service.update_service("payments-api", UpdateServiceRequest(name="Payments Control API"))
        assert renamed.status == ServiceStatus.DISABLED
        assert renamed.name == "Payments Control API"

        try:
            await service.update_service("payments-api", UpdateServiceRequest(status=ServiceStatus.HEALTHY))
        except ValueError as exc:
            assert "only be set by refresh" in str(exc)
        else:
            raise AssertionError("Expected healthy status patch to fail")

    asyncio.run(scenario())


def test_service_registry_service_refresh_stores_capabilities_and_sets_healthy_status() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        fetcher = FakeCapabilityFetcher(
            make_capability_document(supported_routes=["status.latest", "workflow.topology"])
        )
        service = ServiceRegistryService(store=store, capability_fetcher=fetcher)

        await service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="https://payments.example.test",
                environment="prod",
                tags=["core"],
                auth_mode="internal_network",
            )
        )

        refreshed = await service.refresh_service("payments-api")

        assert fetcher.calls == ["https://payments.example.test"]
        assert refreshed.status == ServiceStatus.HEALTHY
        assert refreshed.capabilities == make_capability_document(
            supported_routes=["status.latest", "workflow.topology"]
        ).model_dump(mode="json")
        assert refreshed.last_seen_at is not None

    asyncio.run(scenario())


def test_service_registry_service_refresh_preserves_disabled_status() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        fetcher = FakeCapabilityFetcher(make_capability_document())
        service = ServiceRegistryService(store=store, capability_fetcher=fetcher)

        await store.create(make_record(status=ServiceStatus.DISABLED))

        refreshed = await service.refresh_service("payments-api")

        assert refreshed.status == ServiceStatus.DISABLED
        assert refreshed.capabilities == make_capability_document().model_dump(mode="json")
        assert refreshed.last_seen_at is not None

    asyncio.run(scenario())


def test_service_registry_router_supports_crud_duplicate_handling_and_live_refresh() -> None:
    store = RedisServiceRegistryStore(FakeRedis())
    registry_service = ServiceRegistryService(
        store=store,
        capability_fetcher=FakeCapabilityFetcher(make_capability_document(supported_routes=["status.latest"])),
    )
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    create_response = client.post(
        "/studio/services",
        json={
            "service_id": "payments-api",
            "name": "Payments API",
            "base_url": "https://PAYMENTS.example.test/",
            "environment": "prod",
            "tags": ["core", "money"],
            "auth_mode": "internal_network",
        },
    )
    assert create_response.status_code == 201
    assert create_response.json()["base_url"] == "https://payments.example.test"
    assert create_response.json()["status"] == "registered"

    duplicate_response = client.post(
        "/studio/services",
        json={
            "service_id": "payments-copy",
            "name": "Payments Copy",
            "base_url": "https://payments.example.test",
            "environment": "prod",
            "tags": [],
            "auth_mode": "internal_network",
        },
    )
    assert duplicate_response.status_code == 409

    list_response = client.get("/studio/services")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1

    patch_response = client.patch("/studio/services/payments-api", json={"status": "unavailable"})
    assert patch_response.status_code == 200
    assert patch_response.json()["status"] == "unavailable"

    refresh_response = client.post("/studio/services/payments-api/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["status"] == "healthy"
    assert refresh_response.json()["capabilities"]["supported_routes"] == ["status.latest"]
    assert refresh_response.json()["last_seen_at"] is not None

    delete_response = client.delete("/studio/services/payments-api")
    assert delete_response.status_code == 204

    recreate_response = client.post(
        "/studio/services",
        json={
            "service_id": "payments-copy",
            "name": "Payments Copy",
            "base_url": "https://payments.example.test",
            "environment": "prod",
            "tags": [],
            "auth_mode": "internal_network",
        },
    )
    assert recreate_response.status_code == 201


def test_service_registry_router_exports_gateway_safe_service_catalog() -> None:
    store = RedisServiceRegistryStore(FakeRedis())
    registry_service = ServiceRegistryService(
        store=store,
        capability_fetcher=FakeCapabilityFetcher(make_capability_document(supported_routes=["status.latest"])),
    )
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    create_response = client.post(
        "/studio/services",
        json={
            "service_id": "Payments API++",
            "name": "Payments API",
            "base_url": "https://PAYMENTS.example.test/",
            "environment": "prod",
            "tags": ["core", "money", "core"],
            "auth_mode": "internal_network",
            "log_config": {
                "provider": "loki",
                "base_url": "https://loki.example.test",
                "tenant_id": "tenant-a",
                "service_selector_labels": {"service": "payments-api"},
            },
            "metrics_config": {
                "provider": "prometheus",
                "base_url": "https://prometheus.example.test",
                "namespace": "payments",
                "service_selector_labels": {"service": "payments-api"},
            },
            "trace_config": {
                "provider": "tempo",
                "base_url": "https://tempo.example.test",
                "tenant_id": "tenant-a",
                "query_path": "/api/traces/{trace_id}",
            },
        },
    )
    assert create_response.status_code == 201

    refresh_response = client.post("/studio/services/Payments%20API++/refresh")
    assert refresh_response.status_code == 200

    response = client.get("/studio/gateway/services")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["services"] == [
        {
            "studio_service_id": "Payments API++",
            "name": "payments-api",
            "display_name": "Payments API",
            "base_url": "https://payments.example.test",
            "environment": "prod",
            "tags": ["core", "money"],
            "auth_mode": "internal_network",
            "status": "healthy",
            "capabilities": make_capability_document(supported_routes=["status.latest"]).model_dump(mode="json"),
            "default_route_pattern": "/services/payments-api/*",
        }
    ]
    exported_service = payload["services"][0]
    assert "log_config" not in exported_service
    assert "metrics_config" not in exported_service
    assert "trace_config" not in exported_service
    assert "last_seen_at" not in exported_service


def test_service_registry_router_exports_unique_gateway_service_names_for_collisions() -> None:
    store = RedisServiceRegistryStore(FakeRedis())
    registry_service = ServiceRegistryService(store=store)
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    for service_id, base_url in (
        ("Payments API", "https://payments-name.example.test"),
        ("payments-api", "https://payments-slug.example.test"),
        ("!!!", "https://punctuation.example.test"),
    ):
        response = client.post(
            "/studio/services",
            json={
                "service_id": service_id,
                "name": service_id,
                "base_url": base_url,
                "environment": "prod",
                "tags": [],
                "auth_mode": "internal_network",
            },
        )
        assert response.status_code == 201

    response = client.get("/studio/gateway/services")

    assert response.status_code == 200
    services = response.json()["services"]
    names_by_studio_id = {service["studio_service_id"]: service["name"] for service in services}
    route_patterns = [service["default_route_pattern"] for service in services]
    assert len(set(names_by_studio_id.values())) == len(names_by_studio_id)
    assert len(set(route_patterns)) == len(route_patterns)
    assert names_by_studio_id["Payments API"].startswith("payments-api-")
    assert names_by_studio_id["payments-api"].startswith("payments-api-")
    assert names_by_studio_id["!!!"].startswith("service-")


def test_http_capability_fetcher_rejects_untrusted_refresh_target_before_request() -> None:
    async def scenario() -> None:
        fake_client = FakeAsyncClient(httpx.Response(status_code=200, json={"relayna_version": "1.3.4"}))
        fetcher = HttpCapabilityFetcher(client_factory=lambda timeout_seconds: fake_client)

        with pytest.raises(Exception, match="trusted host/network allowlist"):
            await fetcher.fetch("https://api.production.internal")

        assert fake_client.requested_urls == []

    asyncio.run(scenario())


def test_http_capability_fetcher_allows_explicit_trusted_host_patterns() -> None:
    async def scenario() -> None:
        fake_client = FakeAsyncClient(httpx.Response(status_code=404))
        fetcher = HttpCapabilityFetcher(
            client_factory=lambda timeout_seconds: fake_client,
            allowed_hosts=("*.cluster.local",),
        )

        await fetcher.fetch("https://studio-api.prod.cluster.local")

        assert fake_client.requested_urls == ["https://studio-api.prod.cluster.local/relayna/capabilities"]

    asyncio.run(scenario())


def test_http_capability_fetcher_allows_default_kubernetes_service_suffixes() -> None:
    async def scenario() -> None:
        fake_client = FakeAsyncClient(httpx.Response(status_code=404))
        fetcher = HttpCapabilityFetcher(client_factory=lambda timeout_seconds: fake_client)

        await fetcher.fetch("http://studio-api.default.svc.local:8000")
        await fetcher.fetch("http://studio-api.default.svc.cluster.local:8000")

        assert fake_client.requested_urls == [
            "http://studio-api.default.svc.local:8000/relayna/capabilities",
            "http://studio-api.default.svc.cluster.local:8000/relayna/capabilities",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("status_code", [404, 405, 501])
def test_http_capability_fetcher_treats_legacy_statuses_as_fallback(status_code: int) -> None:
    async def scenario() -> None:
        fake_client = FakeAsyncClient(httpx.Response(status_code=status_code))
        fetcher = HttpCapabilityFetcher(client_factory=lambda timeout_seconds: fake_client)

        document = await fetcher.fetch("https://payments.example.test")

        assert fake_client.requested_urls == ["https://payments.example.test/relayna/capabilities"]
        assert document.model_dump(mode="json") == make_capability_document(
            discovery_source="fallback",
            compatibility="legacy_no_capabilities_endpoint",
            supported_routes=[],
            service_title=None,
        ).model_dump(mode="json") | {"relayna_version": "unknown", "topology_kind": "unknown"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response_or_error", "expected_detail"),
    [
        (httpx.ReadTimeout("timed out"), "timed out"),
        (httpx.ConnectError("connect failed"), "failed"),
        (httpx.Response(status_code=200, text="not-json"), "invalid JSON"),
        (httpx.Response(status_code=200, json={"relayna_version": "1.3.4"}), "invalid capability document"),
    ],
)
def test_service_registry_refresh_returns_502_and_preserves_existing_data_on_fetch_failures(
    response_or_error: httpx.Response | Exception,
    expected_detail: str,
) -> None:
    initial_seen_at = datetime(2026, 4, 8, 12, 0, tzinfo=UTC)
    store = RedisServiceRegistryStore(FakeRedis())
    fake_client = FakeAsyncClient(response_or_error)
    registry_service = ServiceRegistryService(
        store=store,
        capability_fetcher=HttpCapabilityFetcher(client_factory=lambda timeout_seconds: fake_client),
    )
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    asyncio.run(
        store.create(
            make_record(
                status=ServiceStatus.UNAVAILABLE,
                capabilities={"supported_routes": ["status.latest"]},
                last_seen_at=initial_seen_at,
            )
        )
    )

    refresh_response = client.post("/studio/services/payments-api/refresh")

    assert refresh_response.status_code == 502
    assert expected_detail in refresh_response.json()["detail"]

    stored = asyncio.run(store.get("payments-api"))
    assert stored is not None
    assert stored.status == ServiceStatus.UNAVAILABLE
    assert stored.capabilities == {"supported_routes": ["status.latest"]}
    assert stored.last_seen_at == initial_seen_at


def test_service_registry_router_returns_422_for_invalid_payload_types() -> None:
    store = RedisServiceRegistryStore(FakeRedis())
    registry_service = ServiceRegistryService(store=store)
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    response = client.post(
        "/studio/services",
        json={
            "service_id": 123,
            "name": "Payments API",
            "base_url": "https://payments.example.test",
            "environment": "prod",
            "tags": ["core"],
            "auth_mode": "internal_network",
        },
    )

    assert response.status_code == 422


def test_service_registry_router_patch_accepts_service_id_with_surrounding_spaces() -> None:
    store = RedisServiceRegistryStore(FakeRedis())
    registry_service = ServiceRegistryService(store=store)
    app = FastAPI()
    app.include_router(create_service_registry_router(service_registry=registry_service))
    client = TestClient(app)

    create_response = client.post(
        "/studio/services",
        json={
            "service_id": "payments-api",
            "name": "Payments API",
            "base_url": "https://payments.example.test",
            "environment": "prod",
            "tags": [],
            "auth_mode": "internal_network",
        },
    )
    assert create_response.status_code == 201

    patch_response = client.patch("/studio/services/%20payments-api%20", json={"name": "Payments Control API"})

    assert patch_response.status_code == 200
    assert patch_response.json()["name"] == "Payments Control API"


def test_create_studio_app_mounts_registry_routes_and_closes_redis(monkeypatch) -> None:
    FakeRedis.instances.clear()
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    app = create_studio_app(redis_url="redis://studio-test/0")

    with TestClient(app) as client:
        response = client.get("/studio/services")
        assert response.status_code == 200
        assert response.json() == {"count": 0, "services": []}

    assert FakeRedis.instances[0].url == "redis://studio-test/0"
    assert FakeRedis.instances[0].close_calls == 1
