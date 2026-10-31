from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from relayna_studio._observability_profiles import (
    _check_binding,
    _cipher,
    _create_profile_router,
    _ProfileInput,
    _ProfileStore,
    _url,
)
from relayna_studio.database import PostgresServiceRegistryStore, StudioDatabase, audit_log, metadata, operator_settings
from relayna_studio.logs import LokiLogProvider, StudioLogProviderError, StudioLogQuery
from relayna_studio.metrics import (
    PrometheusMetricsProvider,
    StudioMetricGroup,
    StudioMetricsProviderError,
    StudioMetricsQuery,
)
from relayna_studio.registry import (
    LokiLogConfig,
    PrometheusMetricsConfig,
    ServiceRecord,
    StudioOutboundUrlPolicy,
    TempoTraceConfig,
)
from relayna_studio.traces import StudioTraceProviderError, TempoTraceProvider
from sqlalchemy import select, text

POLICY = StudioOutboundUrlPolicy(allowed_hosts=[".internal"])
MODELS = {"loki": LokiLogConfig, "prometheus": PrometheusMetricsConfig, "tempo": TempoTraceConfig}


def payload(**changes):
    return _ProfileInput.model_validate(
        {
            "name": "Shared",
            "environment": "prod",
            "provider": "loki",
            "base_url": "http://telemetry.internal/base",
            **changes,
        }
    )


def record(**changes):
    return ServiceRecord(
        service_id="orders",
        name="Orders",
        base_url="http://orders.internal",
        environment="prod",
        auth_mode="internal_network",
        **changes,
    )


def config(provider="loki", **changes):
    return MODELS[provider].model_validate(
        {
            "base_url": "http://stale.internal",
            "namespace": "payments",
            "service_selector_labels": {"app": "orders"},
            "profile_id": "profile",
            **changes,
        }
    )


def document(**changes):
    return {
        "id": "profile",
        "name": "Shared",
        "provider": "loki",
        "environment": "prod",
        "base_url": "http://telemetry.internal/base",
        "tenant_id": "tenant",
        "defaults": {},
        "auth_mode": "none",
        "revision": 1,
        "updated_at": "now",
        **changes,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@host/",
        "http://host/?token=secret",
        "http://host/#fragment",
        "ftp://host",
        "http://host:bad",
        "http://host/\nsecret",
        "http:///path",
    ],
)
def test_endpoint_rejects_secret_and_invalid_urls(url):
    with pytest.raises(ValueError):
        _url(url)


def test_input_validation_and_public_secrets(monkeypatch):
    assert _url("https://host/base/") == "https://host/base"
    assert payload(tenant_id="").tenant_id is None
    for changes in (
        {"tenant_id": "bad\r\nheader"},
        {"defaults": {"password": "private"}},
        {"defaults": {"pod_label": ""}},
        {"provider": "tempo", "defaults": {"public_base_url": "https://user:secret@host"}},
    ):
        with pytest.raises(ValidationError):
            payload(**changes)
    assert payload(defaults={"pod_label": "instance"}).defaults["pod_label"] == "instance"
    public = _ProfileStore.public(document(credential_cipher="ciphertext", token="plaintext"))
    assert public["credential_configured"] is True
    assert "ciphertext" not in json.dumps(public) and "plaintext" not in json.dumps(public)
    with pytest.raises(HTTPException):
        _check_binding(document(provider="tempo"), record(), "loki")
    monkeypatch.delenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", raising=False)
    with pytest.raises(HTTPException, match="503"):
        _cipher()
    with pytest.raises(HTTPException, match="503"):
        _ProfileStore(None, POLICY)._database()


@pytest.mark.asyncio
async def test_resolution_scope_secrets_and_key_loss(monkeypatch):
    store = _ProfileStore(None, POLICY)
    store.current = AsyncMock(return_value=document())
    original = config()
    resolved, headers = await store.resolve(original, "prod")
    assert original.base_url == "http://stale.internal"
    assert resolved.base_url == "http://telemetry.internal/base" and headers["X-Scope-OrgID"] == "tenant"
    for changes in ({"provider": "tempo"}, {"environment": "staging"}):
        store.current.return_value = document(**changes)
        with pytest.raises(HTTPException):
            await store.resolve(original, "prod")
    store.current.side_effect = HTTPException(404, "Missing")
    with pytest.raises(HTTPException, match="503"):
        await store.resolve(original, "prod")
    store.current.side_effect = HTTPException(503, "Unavailable")
    with pytest.raises(HTTPException, match="Unavailable"):
        await store.resolve(original, "prod")
    store.current.side_effect = None
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for mode, secret in [
        ("bearer", {"token": "synthetic-private"}),
        ("basic", {"username": "name", "password": "synthetic-private"}),
    ]:
        encrypted = _cipher().encrypt(json.dumps(secret).encode()).decode()
        store.current.return_value = document(auth_mode=mode, credential_cipher=encrypted)
        _, headers = await store.resolve(original, "prod")
        assert headers["Authorization"].startswith("Bearer " if mode == "bearer" else "Basic ")
        _, headers = await store.resolve(original, "prod", credentials=False)
        assert "Authorization" not in headers
    store.current.return_value = document(auth_mode="bearer", credential_cipher="broken")
    with pytest.raises(HTTPException, match="decrypt"):
        await store.resolve(original, "prod")
    assert (await store.record(record())).log_config is None
    store.current.return_value = document()
    assert (await store.record(record(log_config=original))).log_config.base_url.endswith("/base")
    _, headers = await store.resolve(config(profile_id=None, tenant_id=None), "prod")
    assert headers == {"Accept": "application/json"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", MODELS)
async def test_saved_connection_tests_are_bounded_and_sanitized(provider):
    store = _ProfileStore(None, POLICY)
    store.current = AsyncMock(return_value=document(provider=provider))
    responses = [
        httpx.Response(200, json={"status": "success", "tagNames": []}),
        httpx.Response(401, text="private"),
        httpx.Response(403),
        httpx.Response(302, headers={"Location": "http://outside.invalid"}),
        httpx.Response(500, text="private"),
        httpx.Response(200, text="private"),
        httpx.Response(200, json={}),
    ]
    requests = []

    def transport(request):
        requests.append(request)
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
        for expected in [
            "connected",
            "unauthorized",
            "unauthorized",
            "provider_error",
            "provider_error",
            "provider_error",
            "provider_error",
        ]:
            result = await store.test("profile", client)
            assert result["state"] == expected and "private" not in json.dumps(result)
        assert len(requests) == 7
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("private")))
    ) as client:
        assert (await store.test("profile", client))["state"] == "unreachable"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", MODELS)
async def test_provider_requests_resolve_credentials_and_reject_redirects(provider, monkeypatch):
    requests = []
    store = _ProfileStore(None, POLICY)
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encrypted = _cipher().encrypt(json.dumps({"token": "synthetic-private"}).encode()).decode()
    store.current = AsyncMock(return_value=document(provider=provider, auth_mode="bearer", credential_cipher=encrypted))

    async def send(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://other.internal"}, text="synthetic-private")

    async with httpx.AsyncClient(transport=httpx.MockTransport(send), follow_redirects=True) as client:
        cls = {"loki": LokiLogProvider, "prometheus": PrometheusMetricsProvider, "tempo": TempoTraceProvider}[provider]
        instance = cls(http_client=client, outbound_policy=POLICY, profile_store=store)

        async def query():
            if provider == "loki":
                return await instance.query_logs(service=record(), config=config(provider), query=StudioLogQuery())
            if provider == "tempo":
                return await instance.query_trace(service=record(), config=config(provider), trace_id="a" * 32)
            return await instance.query_service_pods(service=record(), config=config(provider))

        with pytest.raises((StudioLogProviderError, StudioMetricsProviderError, StudioTraceProviderError)) as exc:
            await query()
        assert "synthetic-private" not in str(exc.value)
        assert len(requests) == 1 and requests[0].url.host == "telemetry.internal"
        assert requests[0].headers["Authorization"] == "Bearer synthetic-private"
        assert requests[0].headers.get("X-Scope-OrgID") == (None if provider == "prometheus" else "tenant")
        if provider == "prometheus":
            with pytest.raises(StudioMetricsProviderError):
                await instance._query_group(
                    service=record(),
                    config=config(provider),
                    query=StudioMetricsQuery(),
                    group=StudioMetricGroup.CPU_USAGE,
                    start=datetime.now(UTC),
                    end=datetime.now(UTC),
                    step_seconds=30,
                )
        instance._profile_store = None
        with pytest.raises(
            (StudioLogProviderError, StudioMetricsProviderError, StudioTraceProviderError), match="unavailable"
        ):
            await query()


def test_routes_sanitize_validation_and_missing_storage():
    store = _ProfileStore(None, POLICY)
    app = FastAPI()
    app.include_router(_create_profile_router(store, httpx.AsyncClient()))
    with TestClient(app) as client:
        base = "/studio/admin/observability-profiles"
        for body in [
            {"token": "synthetic-private"},
            {
                "name": "Bad",
                "environment": "prod",
                "provider": "loki",
                "base_url": "http://user:synthetic-private@host",
            },
        ]:
            response = client.post(base, json=body)
            assert response.status_code == 422 and "synthetic-private" not in response.text
        assert client.post(base, content="not json").status_code == 422
        assert client.get(base).status_code == 503
        assert client.get(base + "/missing").status_code == 503
        assert client.post(base, json=payload().model_dump(mode="json")).status_code == 503


@pytest_asyncio.fixture
async def profile_database():
    url = os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL via RELAYNA_STUDIO_TEST_DATABASE_URL")
    database = StudioDatabase(url, pool_size=4, pool_max_overflow=4)
    await database.check_schema()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE "
                + ", ".join('"' + t.name + '"' for t in reversed(metadata.sorted_tables))
                + " RESTART IDENTITY CASCADE"
            )
        )
    yield database
    await database.dispose()


@pytest.mark.asyncio
async def test_durable_profiles_rotation_linking_audit_and_revision(profile_database, monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = _ProfileStore(profile_database, POLICY)
    saved = await store.save(payload(auth_mode="bearer", token="synthetic-original"))
    profile_id = saved["id"]
    registry = PostgresServiceRegistryStore(profile_database)
    for service_id in ["orders", "payments"]:
        await registry.create(
            record().model_copy(
                update={
                    "service_id": service_id,
                    "base_url": f"http://{service_id}.internal",
                    "log_config": config(profile_id=profile_id),
                }
            )
        )
    assert (await store.list_profiles())[0]["used_by"] == ["orders", "payments"]
    with pytest.raises(HTTPException, match="Detach"):
        await store.remove(profile_id, 1)
    updated = await store.save(payload(auth_mode="bearer", token="synthetic-rotated", revision=1), profile_id)
    restarted = _ProfileStore(profile_database, POLICY)
    effective, headers = await restarted.resolve(config(profile_id=profile_id), "prod")
    assert headers["Authorization"] == "Bearer synthetic-rotated"
    assert effective.service_selector_labels == {"app": "orders"}
    assert updated["revision"] == 2
    await store.save(payload(auth_mode="bearer", revision=2, name="Renamed"), profile_id)
    with pytest.raises(HTTPException, match="Reload"):
        await store.save(payload(revision=1), profile_id)
    with pytest.raises(HTTPException, match="Reload"):
        await store.remove(profile_id, 1)
    with pytest.raises(HTTPException, match="new credentials"):
        await store.save(payload(auth_mode="bearer", revision=3, base_url="http://changed.internal"), profile_id)
    for changes in [{"provider": "tempo"}, {"environment": "stage"}]:
        with pytest.raises(HTTPException, match="cannot change"):
            await store.save(payload(revision=3, **changes), profile_id)
    with pytest.raises(HTTPException, match="already exists"):
        await store.save(payload(name="RENAMED"))
    for field, changes in [("log_config", {"profile_id": "missing"}), ("trace_config", {"profile_id": profile_id})]:
        provider = "loki" if field == "log_config" else "tempo"
        with pytest.raises(HTTPException):
            await registry.update("orders", record(**{field: config(provider, **changes)}))
    with pytest.raises(HTTPException):
        await registry.update(
            "orders", record(log_config=config(profile_id=profile_id)).model_copy(update={"environment": "stage"})
        )
    async with profile_database.sessions() as session:
        stored = list(await session.scalars(select(operator_settings.c.value)))
        audit = list(await session.scalars(select(audit_log.c.details)))
    assert "synthetic-" not in json.dumps(stored) + json.dumps(audit) + json.dumps(saved)
    assert stored[0]["credential_cipher"]
    for service_id in ["orders", "payments"]:
        await registry.delete(service_id)
    await store.remove(profile_id, 3)
    assert await store.list_profiles() == []
    for action in [store.current(profile_id), store.remove(profile_id, 3), store.save(payload(revision=3), profile_id)]:
        with pytest.raises(HTTPException, match="404"):
            await action


@pytest.mark.asyncio
async def test_profile_write_conflicts_credentials_and_binding_delete_race(profile_database, monkeypatch):
    monkeypatch.setenv("RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = _ProfileStore(profile_database, POLICY)
    for changes in [
        {"auth_mode": "bearer"},
        {"auth_mode": "basic", "username": "u"},
        {"auth_mode": "basic", "username": "u:x", "password": "p"},
        {"auth_mode": "bearer", "token": "bad\nheader"},
        {"token": "unexpected"},
        {"auth_mode": "bearer", "token": ""},
        {"auth_mode": "bearer", "token": "unicode-ä"},
    ]:
        with pytest.raises(HTTPException, match="422"):
            await store.save(payload(**changes))
    saved = await store.save(payload(auth_mode="basic", username="user", password="synthetic-password"))
    profile_id = saved["id"]
    results = await asyncio.gather(
        store.save(payload(revision=1, name="First"), profile_id),
        store.save(payload(revision=1, name="Second"), profile_id),
        return_exceptions=True,
    )
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    registry = PostgresServiceRegistryStore(profile_database)
    bind, remove = await asyncio.gather(
        registry.create(record(log_config=config(profile_id=profile_id))),
        store.remove(profile_id, 2),
        return_exceptions=True,
    )
    assert isinstance(bind, HTTPException) != isinstance(remove, HTTPException)
    if not isinstance(bind, HTTPException):
        assert (await store.current(profile_id))["id"] == profile_id
    else:
        with pytest.raises(HTTPException):
            await store.current(profile_id)


@pytest.mark.asyncio
async def test_admin_api_roundtrip_and_linked_registry_behavior(profile_database):
    from relayna_studio.registry import CreateServiceRequest, ServiceRegistryService, UpdateServiceRequest

    store = _ProfileStore(profile_database, POLICY)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "success"}))
    ) as upstream:
        app = FastAPI()
        app.include_router(_create_profile_router(store, upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio") as client:
            base = "/studio/admin/observability-profiles"
            created = await client.post(base, json=payload().model_dump(mode="json"))
            assert created.status_code == 201
            profile = created.json()
            path = base + "/" + profile["id"]
            response = await client.get(path)
            assert response.headers["cache-control"] == "no-store"
            assert (await client.post(path + "/test")).json()["ok"]
            registry = ServiceRegistryService(
                store=PostgresServiceRegistryStore(profile_database), profile_store=store, outbound_policy=POLICY
            )
            service_payload = record(log_config=config(profile_id=profile["id"])).model_dump(
                exclude={"status", "capabilities", "last_seen_at", "health"}
            )
            saved = await registry.create_service(CreateServiceRequest.model_validate(service_payload))
            assert saved.log_config.base_url.endswith("/base")
            profile.update(base_url="http://updated.internal", revision=1)
            public_fields = {
                k: v for k, v in profile.items() if k not in {"id", "used_by", "updated_at", "credential_configured"}
            }
            assert (await client.put(path, json=public_fields)).status_code == 200
            assert (await registry.list_services())[0].log_config.base_url == "http://updated.internal"
            assert (await registry.get_service("orders")).log_config.base_url == "http://updated.internal"
            updated = await registry.update_service("orders", UpdateServiceRequest(name="Renamed"))
            assert updated.log_config.profile_id == profile["id"]
            assert (await client.delete(path + "?revision=2")).status_code == 409
            await registry.update_service("orders", UpdateServiceRequest(log_config=None))
            assert (await client.delete(path + "?revision=2")).status_code == 204
            assert (await client.get(path)).status_code == 404
            blocked = await client.post(base, json=payload(base_url="http://outside.invalid").model_dump(mode="json"))
            assert blocked.status_code == 422


@pytest.mark.asyncio
async def test_damaged_bindings_stay_editable_but_never_query_without_resolution():
    store = _ProfileStore(None, POLICY)
    bound = record(log_config=config())
    store.current = AsyncMock(side_effect=HTTPException(404, "Missing"))
    assert (await store.record(bound)).log_config.profile_id == "profile"
    with pytest.raises(HTTPException):
        await store.record(bound, strict=True)
    with pytest.raises(HTTPException):
        await store.resolve(bound.log_config, bound.environment)
