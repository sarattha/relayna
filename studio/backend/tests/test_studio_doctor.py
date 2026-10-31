from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from relayna_studio import create_studio_app, get_studio_runtime
from relayna_studio._diagnostic_observations import _record
from relayna_studio._doctor import _age, _bounded, _observation, _stamp
from relayna_studio.auth import StudioAuthMiddleware, StudioMember, StudioMemberStatus, StudioRole
from relayna_studio.events import StudioEventEnvelope
from relayna_studio.failed_task_notifications import FailedTaskEmailNotificationService
from relayna_studio.registry import ServiceRecord, ServiceStatus

from relayna.observability import RelaynaServiceEvent


def capability(routes: list[str] | None = None) -> dict:
    return {
        "relayna_version": "1.11.0",
        "topology_kind": "workflow",
        "alias_config_summary": {"enabled": False, "aliases": {}},
        "supported_routes": routes or [],
        "feature_flags": [],
        "service_metadata": {
            "compatibility": "capabilities_v1",
            "service_id": "payments",
            "name": "Payments",
            "capability_path": "/capabilities",
            "discovery_source": "test",
        },
    }


def record(**changes) -> ServiceRecord:
    return ServiceRecord(
        service_id="payments",
        name="Payments",
        base_url="https://payments.example.test",
        environment="production",
        auth_mode="internal_network",
        **changes,
    )


def checks(response) -> dict:
    assert response.status_code == 200, response.text
    return {check["source"]: check for check in response.json()["checks"]}


@pytest.fixture
def client(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_app.Redis, "from_url", lambda *args, **kwargs: redis)
    app = create_studio_app(
        redis_url="redis://doctor",
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    with TestClient(app) as client:
        rt = get_studio_runtime(app)
        client.portal.call(rt.registry_store.create, record())
        yield client, rt


def test_passive_diagnostics_separate_readiness_empty_missing_and_configuration(client):
    client, rt = client
    rt.http_client.get = AsyncMock(side_effect=AssertionError("GET diagnostics must not call upstream"))
    response = client.get("/studio/health")
    summary = checks(response)
    assert response.headers["cache-control"] == "no-store"
    assert summary["readiness"]["state"] == "ok"
    assert summary["outbox"]["state"] == "unsupported"
    assert summary["outbox"]["value"] is None
    assert summary["delivery"]["state"] == "unconfigured"
    assert summary["event_pull_loop"]["state"] == "disabled"
    response = client.get("/studio/services/payments/doctor")
    evidence = checks(response)
    assert response.json()["environment"] == "production"
    assert evidence["indexed_events"]["state"] == "empty"
    assert evidence["capabilities"]["state"] == "unknown"
    assert evidence["event_pull"]["state"] == "unknown"
    assert evidence["ingestion_lag"]["value"] is None
    assert evidence["logs"]["state"] == "unconfigured"
    assert evidence["retention"]["value"] == 86400
    assert "5000" in evidence["retention"]["detail"]
    assert client.get("/studio/services/missing/doctor").status_code == 404
    assert client.post("/studio/services/missing/doctor/check").status_code == 404
    assert client.post("/studio/services/payments/doctor/check?task_id=" + "x" * 256).status_code == 422


def test_event_freshness_and_ingestion_delay_do_not_fabricate_timezone_or_liveness(client):
    client, rt = client
    now = datetime.now(UTC)

    async def insert(timestamp: str, task: str):
        await rt.event_store.insert_event(
            StudioEventEnvelope(
                service_id="payments",
                ingest_method="push",
                event=RelaynaServiceEvent(
                    cursor=task,
                    task_id=task,
                    event_id=task,
                    event_type="observed",
                    source_kind="observation",
                    timestamp=timestamp,
                ),
            )
        )

    client.portal.call(insert, now.replace(tzinfo=None).isoformat(), "naive")
    evidence = checks(client.get("/studio/services/payments/doctor"))
    assert evidence["indexed_events"]["state"] == "unknown"
    assert evidence["ingestion_lag"]["value"] is None
    client.portal.call(insert, (now - timedelta(minutes=10)).isoformat(), "old")
    evidence = checks(client.get("/studio/services/payments/doctor"))
    assert evidence["indexed_events"]["state"] == "stale"
    assert evidence["ingestion_lag"]["value"] >= 600
    assert "not periodic liveness" in evidence["capabilities"]["detail"]
    client.portal.call(insert, now.isoformat(), "fresh")
    assert checks(client.get("/studio/services/payments/doctor"))["indexed_events"]["state"] == "ok"


def test_live_empty_samples_and_provider_failure_preserve_source_scope_and_policy(client, monkeypatch):
    client, rt = client
    service = record(
        capabilities=capability(["events.feed"]),
        status="healthy",
        last_seen_at=datetime.now(UTC),
        log_config={
            "provider": "loki",
            "base_url": "https://loki.example.test",
            "service_selector_labels": {"app": "payments"},
        },
    )
    client.portal.call(rt.registry_store.update, "payments", service)
    monkeypatch.setattr(
        type(rt.health_service),
        "refresh_health",
        AsyncMock(
            return_value=SimpleNamespace(
                capability_status=SimpleNamespace(last_successful_at=datetime.now(UTC)),
                http_status=SimpleNamespace(state="reachable"),
                last_checked_at=datetime.now(UTC),
            )
        ),
    )
    requested = []

    async def upstream(request):
        requested.append(request)
        return httpx.Response(200, json={"count": 0, "items": []})

    original = rt.http_client
    rt.http_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    rt.log_query_service.query_task_logs = AsyncMock(side_effect=RuntimeError("secret URL should not leak"))
    try:
        evidence = checks(client.post("/studio/services/payments/doctor/check?task_id=task-a"))
        assert evidence["feed_sample"]["state"] == "empty"
        assert evidence["indexed_events"]["state"] == "empty"
        assert evidence["capabilities"]["state"] == "ok"
        assert evidence["log_sample"]["state"] == "unavailable"
        assert "secret" not in str(evidence)
        assert len(requested) == 1 and requested[0].url.params["limit"] == "1"
        query = rt.log_query_service.query_task_logs.call_args.args[2]
        assert query.limit == 1 and _stamp(query.to_time) - _stamp(query.from_time) == timedelta(minutes=15)
        assert rt.log_query_service.query_task_logs.call_args.args[:2] == ("payments", "task-a")
        # Existing target policy is applied even to manually injected registry records.
        client.portal.call(
            rt.registry_store.update, "payments", service.model_copy(update={"base_url": "http://127.0.0.1"})
        )
        evidence = checks(client.post("/studio/services/payments/doctor/check"))
        assert evidence["feed_sample"]["state"] == "unavailable"
        assert len(requested) == 1
    finally:
        client.portal.call(rt.http_client.aclose)
        rt.http_client = original


def test_unsupported_disabled_and_oversized_feed_samples(client, monkeypatch):
    client, rt = client
    service = record(capabilities=capability(), status="disabled")
    client.portal.call(rt.registry_store.update, "payments", service)
    monkeypatch.setattr(
        type(rt.health_service), "refresh_health", AsyncMock(side_effect=AssertionError("Disabled service"))
    )
    evidence = checks(client.post("/studio/services/payments/doctor/check"))
    assert evidence["event_pull"]["state"] == "unsupported"
    assert evidence["capabilities"]["state"] == "disabled"
    assert "feed_sample" not in evidence
    service = service.model_copy(update={"status": ServiceStatus.HEALTHY, "capabilities": capability(["events.feed"])})
    client.portal.call(rt.registry_store.update, "payments", service)
    original = rt.http_client
    rt.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 65537))
    )
    try:
        evidence = checks(client.post("/studio/services/payments/doctor/check"))
        assert evidence["feed_sample"]["state"] == "unavailable"
        assert "ValueError" in evidence["feed_sample"]["detail"]
    finally:
        client.portal.call(rt.http_client.aclose)
        rt.http_client = original


@pytest.mark.asyncio
async def test_observations_preserve_last_success_and_never_fail_primary_operations():
    redis = fakeredis.aioredis.FakeRedis()
    await _record(redis, "prefix", "pull:payments", succeeded=True)
    before = await _observation(redis, "prefix", "pull:payments", "payments", 60)
    await _record(redis, "prefix", "pull:payments", succeeded=False)
    after = await _observation(redis, "prefix", "pull:payments", "payments", 60)
    assert after.state == "unavailable" and after.last_successful_at == before.last_successful_at
    assert 0 < await redis.ttl("prefix:doctor:pull:payments:success") <= 604800
    await redis.set("prefix:doctor:pull:payments:attempt", "2020-01-01T00:00:00Z")
    assert (await _observation(redis, "prefix", "pull:payments", "payments", 60)).state == "stale"
    await _record(SimpleNamespace(set=AsyncMock(side_effect=RuntimeError)), "prefix", "source", succeeded=True)
    await redis.aclose()


@pytest.mark.asyncio
async def test_timeout_and_timestamp_boundaries(monkeypatch):
    assert _stamp("2026-01-01T00:00:00") is None
    assert _stamp("bad") is None
    assert _age(datetime.now(UTC) + timedelta(days=1)) is None
    assert _age(datetime.now(UTC)) is not None
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: real_timeout(0.01))

    async def slow():
        await asyncio.Event().wait()

    check = await _bounded("slow", "service=payments", slow)
    assert check.state == "unavailable" and check.value is None
    assert "TimeoutError" in check.detail


def test_existing_role_csrf_and_audit_boundaries_are_applied(client):
    client, rt = client
    app = FastAPI()
    from relayna_studio._doctor import _create_doctor_router

    app.include_router(_create_doctor_router(rt))
    member = StudioMember(
        user_id="reader",
        tenant_id="tenant",
        object_id="object",
        email="reader@test",
        display_name="Reader",
        role=StudioRole.READONLY,
        status=StudioMemberStatus.ACTIVE,
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    session = SimpleNamespace(csrf_token="csrf")
    service = SimpleNamespace(session_context=AsyncMock(return_value=(session, member)))
    app.add_middleware(StudioAuthMiddleware, service=service)
    with TestClient(app) as reader:
        assert reader.get("/studio/health").status_code == 200
        assert reader.get("/studio/services/payments/doctor").status_code == 200
        assert reader.post("/studio/services/payments/doctor/check").status_code == 403
        assert reader.get("/studio/admin/audit").status_code == 403
        service.session_context.return_value = (session, member.model_copy(update={"role": StudioRole.ADMIN}))
        assert reader.post("/studio/services/payments/doctor/check").status_code == 403
        service.session_context.return_value = None
        assert reader.get("/studio/health").status_code == 401


@pytest.mark.asyncio
async def test_email_delivery_observes_acceptance_and_failure():
    redis = fakeredis.aioredis.FakeRedis()
    service = FailedTaskEmailNotificationService(
        federation_service=None,
        redis=redis,
        email_client=SimpleNamespace(send=AsyncMock()),
        config=SimpleNamespace(redis_prefix="email", receivers=("operator@test",)),
        settings_store=None,
    )
    await service._send_email(title="test", body="test")
    assert (await _observation(redis, "email", "delivery", "email", 60)).state == "ok"
    service.email_client.send.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        await service._send_email(title="test", body="test")
    check = await _observation(redis, "email", "delivery", "email", 60)
    assert check.state == "unavailable" and check.last_successful_at
    await redis.aclose()


def test_configured_email_disabled_and_delivery_failure_are_separate(client):
    client, rt = client
    rt.failed_task_email_configured = True
    rt.failed_task_email_settings_store = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(enabled=False)))
    assert checks(client.get("/studio/health"))["delivery"]["state"] == "disabled"
    rt.failed_task_email_settings_store.get.return_value.enabled = True
    rt.failed_task_email_worker = SimpleNamespace(
        notification_service=SimpleNamespace(config=SimpleNamespace(redis_prefix="email")), stop=lambda: None
    )
    assert checks(client.get("/studio/health"))["delivery"]["state"] == "unknown"
    client.portal.call(partial(_record, rt.redis, "email", "delivery", succeeded=False))
    evidence = checks(client.get("/studio/health"))["delivery"]
    assert evidence["state"] == "unavailable" and evidence["last_successful_at"] is None
    assert "provider accepted" in evidence["detail"]


def test_live_loki_service_sample_empty_and_matching_task_evidence(client, monkeypatch):
    client, rt = client
    service = record(
        capabilities=capability(),
        status="healthy",
        last_seen_at=datetime.now(UTC),
        log_config={
            "provider": "loki",
            "base_url": "https://loki.example.test",
            "service_selector_labels": {"app": "payments"},
        },
    )
    client.portal.call(rt.registry_store.update, "payments", service)
    monkeypatch.setattr(
        type(rt.health_service),
        "refresh_health",
        AsyncMock(
            return_value=SimpleNamespace(
                capability_status=SimpleNamespace(last_successful_at=datetime.now(UTC)),
                http_status=SimpleNamespace(state="reachable"),
                last_checked_at=datetime.now(UTC),
            )
        ),
    )
    rt.log_query_service.query_service_logs = AsyncMock(return_value=SimpleNamespace(items=[]))
    evidence = checks(client.post("/studio/services/payments/doctor/check"))
    assert evidence["feed_sample"]["state"] == "unsupported"
    assert evidence["log_sample"]["state"] == "empty"
    assert "service selector only" in evidence["log_sample"]["scope"]
    assert "does not establish" in evidence["log_sample"]["detail"]
    rt.log_query_service.query_task_logs = AsyncMock(
        return_value=SimpleNamespace(items=[SimpleNamespace(timestamp=datetime.now(UTC).isoformat())])
    )
    evidence = checks(client.post("/studio/services/payments/doctor/check?task_id=known-task"))
    assert evidence["log_sample"]["state"] == "ok"
    assert evidence["log_sample"]["evidence_at"] is not None
    assert "task=known-task" in evidence["log_sample"]["scope"]

    event = RelaynaServiceEvent(
        cursor="known-event",
        task_id="known-task",
        event_id="known-event",
        event_type="status.changed",
        source_kind="status",
        timestamp=datetime.now(UTC).isoformat(),
    )
    service = service.model_copy(update={"capabilities": capability(["events.feed"])})
    client.portal.call(rt.registry_store.update, "payments", service)
    client.portal.call(
        rt.event_ingest_service.ingest_events,
        [StudioEventEnvelope(service_id="payments", ingest_method="push", event=event)],
    )
    original = rt.http_client
    rt.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"count": 1, "items": [event.model_dump(mode="json")]})
        )
    )
    try:
        response = client.post("/studio/services/payments/doctor/check?task_id=known-task")
        evidence = checks(response)
        assert response.json()["environment"] == "production"
        assert evidence["feed_sample"]["state"] == "ok"
        assert evidence["indexed_events"]["state"] == "ok"
        assert evidence["log_sample"]["state"] == "ok"
    finally:
        client.portal.call(rt.http_client.aclose)
        rt.http_client = original


def test_registry_timeouts_and_invalid_capability_documents(client, monkeypatch):
    client, rt = client
    client.portal.call(rt.registry_store.update, "payments", record(capabilities={"invalid": True}))
    evidence = checks(client.get("/studio/services/payments/doctor"))
    assert evidence["supported_routes"]["state"] == "unknown"
    monkeypatch.setattr(type(rt.registry_service), "get_service", AsyncMock(side_effect=TimeoutError))
    assert client.get("/studio/services/payments/doctor").status_code == 504
    assert client.post("/studio/services/payments/doctor/check").status_code == 504


@pytest.mark.asyncio
@pytest.mark.parametrize("history_cap", [0, -1, 1, 5000])
async def test_doctor_enforces_sample_bound_for_nonpositive_history_caps(history_cap):
    from relayna_studio._doctor import _Doctor
    from relayna_studio.events import RedisStudioEventStore, StudioControlPlaneEvent

    redis = fakeredis.aioredis.FakeRedis()
    store = RedisStudioEventStore(redis, history_maxlen=history_cap)
    for i in range(30):
        item = StudioControlPlaneEvent(
            service_id="payments",
            task_id=str(i),
            dedupe_key=str(i),
            ingest_method="push",
            ingested_at=datetime.now(UTC).isoformat(),
            source_kind="status",
            event_type="status.changed",
        )
        await redis.set(store.event_key(str(i)), item.model_dump_json())
        await redis.lpush(store.service_history_key("payments"), str(i))
    reads = []
    original_mget = redis.mget

    async def mget(keys):
        reads.append(keys)
        return await original_mget(keys)

    redis.mget = mget
    doctor = _Doctor(SimpleNamespace(database=None, redis=redis, event_store=store))
    sample = await doctor._recent("payments")
    assert len(sample) == min(20, max(1, history_cap))
    assert len(reads[0]) <= 20
    await redis.aclose()


@pytest.mark.parametrize("source_offset,index_offset", [(3600, 7200), (-60, 3600), (3600, -60)])
def test_future_source_or_index_timestamps_cannot_establish_ingestion_delay(
    client, monkeypatch, source_offset, index_offset
):
    from relayna_studio._doctor import _Doctor
    from relayna_studio.events import StudioControlPlaneEvent

    client, _ = client
    now = datetime.now(UTC)
    item = StudioControlPlaneEvent(
        service_id="payments",
        task_id="task",
        dedupe_key="future",
        ingest_method="push",
        ingested_at=(now + timedelta(seconds=index_offset)).isoformat(),
        timestamp=(now + timedelta(seconds=source_offset)).isoformat(),
        source_kind="status",
        event_type="status.changed",
    )
    monkeypatch.setattr(_Doctor, "_recent", AsyncMock(return_value=[item]))
    evidence = checks(client.get("/studio/services/payments/doctor"))
    assert evidence["ingestion_lag"]["state"] == "unknown"
    assert evidence["ingestion_lag"]["value"] is None
    assert evidence["indexed_events"]["observed_at"] == evidence["ingestion_lag"]["observed_at"]


def test_cached_provider_outage_is_visible_without_additional_queries(client):
    from relayna_studio.logs import StudioLogProviderError
    from relayna_studio.registry import LokiLogConfig

    client, rt = client
    configured = record(
        log_config=LokiLogConfig(base_url="https://loki.example.test", service_selector_labels={"app": "payments"})
    )
    client.portal.call(rt.registry_store.update, "payments", configured)
    provider = rt.log_query_service._providers["loki"]
    provider.query_logs = AsyncMock(side_effect=StudioLogProviderError("Loki offline"))
    assert client.get("/studio/services/payments/logs").status_code == 502
    evidence = checks(client.get("/studio/services/payments/doctor"))
    assert evidence["logs"]["state"] == "unavailable"
    assert evidence["logs"]["checked_at"] is not None
    assert "paused" in evidence["logs"]["detail"]
    assert client.get("/studio/services/payments/logs").status_code == 502
    provider.query_logs.assert_awaited_once()
