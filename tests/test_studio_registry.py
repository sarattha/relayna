from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import relayna.studio.app as studio_app
from relayna.studio import (
    CreateServiceRequest,
    RedisServiceRegistryStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    UpdateServiceRequest,
    create_service_registry_router,
    create_studio_app,
    normalize_base_url,
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


def make_record(*, service_id: str = "payments-api", base_url: str = "https://payments.example.test") -> ServiceRecord:
    return ServiceRecord(
        service_id=service_id,
        name="Payments API",
        base_url=base_url,
        environment="prod",
        tags=["core"],
        auth_mode="internal_network",
        status=ServiceStatus.REGISTERED,
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


def test_service_registry_router_supports_crud_duplicate_handling_and_refresh_placeholder() -> None:
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
    assert refresh_response.status_code == 501
    assert refresh_response.json() == {
        "detail": "Capability refresh is blocked until feature 2 adds GET /relayna/capabilities.",
        "blocked_by": "capability_discovery",
    }

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
