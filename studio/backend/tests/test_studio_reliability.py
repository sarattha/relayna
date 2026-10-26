from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from relayna_studio import _reliability as reliability
from relayna_studio._reliability import (
    _Alert,
    _create_reliability_router,
    _deliver,
    _export,
    _Goal,
    _GoalInput,
    _preview,
    _queries,
    _sample,
    _Store,
)
from relayna_studio.auth import StudioMemberStatus, StudioRole
from relayna_studio.database import PostgresFailedTaskEmailSettingsStore, StudioDatabase, metadata, services
from relayna_studio.registry import (
    PrometheusMetricsConfig,
    ServiceNotFoundError,
    ServiceRecord,
    StudioOutboundUrlPolicy,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def goal(**changes):
    return _Goal.model_validate(
        {
            "id": "goal-1",
            "revision": "version1",
            "created_at": "2026-10-25T12:00:00Z",
            "service_id": "orders",
            "environment": "production",
            "name": "Orders on time",
            "owner": 'orders "team"',
            "kind": "end_to_end_latency",
            "threshold": 99,
            "window_seconds": 86400,
            "runtime_scope_confirmed": True,
            "receivers": ["orders@example.com"],
            **changes,
        }
    )


def runtime(handler=None, database=None):
    service = ServiceRecord(
        service_id="orders",
        auth_mode="none",
        name="Orders",
        base_url="https://orders.example.com",
        environment="production",
        metrics_config=PrometheusMetricsConfig(
            base_url="https://prometheus.example.com",
            namespace="production",
            service_selector_labels={"service": "orders-api"},
        ),
    )
    return SimpleNamespace(
        database=database,
        registry_service=SimpleNamespace(get_service=AsyncMock(return_value=service)),
        outbound_policy=StudioOutboundUrlPolicy(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda request: httpx.Response(500)))),
        failed_task_email_settings_store=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(enabled=True))),
        failed_task_email_configured=True,
        failed_task_email_worker=SimpleNamespace(
            notification_service=SimpleNamespace(email_client=SimpleNamespace(send=AsyncMock()))
        ),
    )


def metric_handler(values):
    def respond(request):
        expression = request.url.params["query"]
        value = next((value for key, value in values.items() if key in expression.split("{", 1)[0]), None)
        rows = [] if value is None else [{"metric": {}, "value": [NOW.timestamp(), str(value)]}]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": rows}})

    return respond


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({}, "breaching"),
        ({"good_work": 100}, "observed"),
        ({"eligible_work": 0}, "no_data"),
        ({"good_work": None}, "no_data"),
        ({"good_work": "NaN"}, "no_data"),
        ({"evidence_timestamp": NOW.timestamp() - 181}, "stale"),
        ({"evidence_timestamp": NOW.timestamp() + 1}, "stale"),
        ({"good_work": 101}, "insufficient"),
        ({"window_start": 0}, "insufficient"),
        ({"window_end": NOW.timestamp()}, "insufficient"),
    ],
)
@pytest.mark.asyncio
async def test_business_preview_requires_positive_denominator_complete_mature_window_and_freshness(
    monkeypatch, changes, state
):
    monkeypatch.setattr(reliability, "_now", lambda: NOW)
    values = {
        "good_work": 98,
        "eligible_work": 100,
        "window_start": NOW.timestamp() - 120 - 86400,
        "window_end": NOW.timestamp() - 120,
        "evidence_timestamp": NOW.timestamp(),
        **changes,
    }
    rt = runtime(metric_handler(values))
    result = await _preview(rt, goal())
    assert result["state"] == state
    assert "environment=production" in result["investigation_url"]
    if state == "breaching":
        assert result["value"] == 98
        assert result["eligible"] == 100
        assert 'deadline_seconds="120"' in result["queries"]["eligible"]
    await rt.http_client.aclose()


@pytest.mark.parametrize("kind", ["backlog_age", "stale_leases", "missing_worker", "retry_activity"])
@pytest.mark.asyncio
async def test_early_signal_preview_and_no_data(monkeypatch, kind):
    monkeypatch.setattr(reliability, "_now", lambda: NOW)
    rt = runtime(
        metric_handler(
            {
                "sum(increase": 12,
                "min(timestamp": NOW.timestamp(),
                "time() -": 120,
                "backlog_oldest": 180,
                "stale_leases": 2,
                "evidence_timestamp": NOW.timestamp(),
            }
        )
    )
    result = await _preview(rt, goal(kind=kind, threshold=1))
    assert result["state"] == "breaching"
    rt.registry_service.get_service.return_value.metrics_config = None
    assert (await _preview(rt, goal(kind=kind)))["state"] == "unsupported"
    await rt.http_client.aclose()


@pytest.mark.asyncio
async def test_preview_never_substitutes_failed_provider_or_unconfirmed_scope_for_health():
    rt = runtime()
    assert (await _preview(rt, goal()))["state"] == "unavailable"
    assert (await _preview(rt, goal(kind="retry_activity", runtime_scope_confirmed=False)))["state"] == "unsupported"
    await rt.http_client.aclose()


@pytest.mark.asyncio
async def test_preview_rejects_a_removed_service_before_querying_its_provider():
    def unexpected_query(request):
        raise AssertionError("A removed service must not query its former provider")

    rt = runtime(unexpected_query)
    rt.registry_service.get_service.side_effect = ServiceNotFoundError("Service no longer exists")
    try:
        with pytest.raises(HTTPException) as failure:
            await _preview(rt, goal())
        assert failure.value.status_code == 404
        assert failure.value.detail == "Service no longer exists"
    finally:
        await rt.http_client.aclose()


@pytest.mark.asyncio
async def test_sample_rejects_ambiguous_series_partial_results_and_wrong_timestamp():
    for data in [
        {"status": "success", "warnings": ["partial"], "data": {}},
        {"status": "success", "data": {"resultType": "vector", "result": [{"value": [NOW.timestamp(), "1"]}] * 2}},
        {"status": "success", "data": {"resultType": "vector", "result": [{"value": [0, "1"]}]}},
    ]:
        rt = runtime(lambda request, data=data: httpx.Response(200, json=data))
        with pytest.raises(ValueError):
            await _sample(rt, "https://prometheus.example.com", "metric", NOW)
        await rt.http_client.aclose()


def test_export_guards_evidence_and_escapes_routing_and_keeps_credentials_private():
    exported = _export(goal(), "orders-api")
    rules = exported["prometheus"]["groups"][0]["rules"]
    assert "eligible_work" in rules[0]["expr"]
    assert "window_start" in rules[0]["expr"]
    assert "count(" in rules[0]["expr"]
    assert "< Inf" in rules[0]["expr"]
    assert rules[1]["alert"] == "RelaynaReliabilityEvidenceMissing"
    assert rules[1]["expr"].startswith("absent(")
    assert 'owner="orders \\"team\\""' in exported["alertmanager_route"]["matchers"]
    assert "credentials_file" in str(exported["receiver"])
    assert "task_duration" not in str(exported)
    assert 'service="orders-api"' in _queries(goal(kind="retry_activity"), "orders-api")["value"]


@pytest.mark.parametrize(
    "change",
    [{"threshold": 101}, {"threshold": float("inf")}, {"receivers": ["bad\r\naddress"]}, {"window_seconds": 2592001}],
)
def test_goal_validation(change):
    with pytest.raises(ValidationError):
        _GoalInput.model_validate({**goal().model_dump(exclude={"id", "revision", "created_at"}), **change})


@pytest.mark.asyncio
async def test_routes_require_admin_and_independent_webhook_credential():
    rt = runtime()
    app = FastAPI()
    app.include_router(_create_reliability_router(rt, "private-webhook-token"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio") as client:
        assert (await client.get("/studio/reliability/goals")).json()["configured"] is False
        assert (await client.post("/studio/reliability/goals", json={})).status_code == 401
        assert (await client.post("/studio/ingest/reliability-alerts", json={})).status_code == 401
        assert (
            await client.post(
                "/studio/ingest/reliability-alerts",
                content="bad",
                headers={"Authorization": "Bearer private-webhook-token"},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/studio/ingest/reliability-alerts",
                content="x" * 262145,
                headers={"Authorization": "Bearer private-webhook-token"},
            )
        ).status_code == 413
    await rt.http_client.aclose()


@pytest.mark.asyncio
async def test_readonly_cannot_write_goals():
    app = FastAPI()
    rt = runtime()

    @app.middleware("http")
    async def readonly(request, call_next):
        request.state.studio_member = SimpleNamespace(role=StudioRole.READONLY, status=StudioMemberStatus.ACTIVE)
        return await call_next(request)

    app.include_router(_create_reliability_router(rt, None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio") as client:
        assert (await client.delete("/studio/reliability/goals/test")).status_code == 403
    await rt.http_client.aclose()


@pytest.mark.skipif(not os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL"), reason="Requires disposable PostgreSQL")
@pytest.mark.asyncio
async def test_postgres_goal_receipts_concurrent_dedupe_failure_retry_scope_and_delete():
    database = StudioDatabase(os.environ["RELAYNA_STUDIO_TEST_DATABASE_URL"], pool_size=1, pool_max_overflow=0)
    async with database.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    rt = runtime(database=database)
    rt.failed_task_email_settings_store = PostgresFailedTaskEmailSettingsStore(database, default_enabled=True)
    store = _Store(database)
    item = goal(id="integration-" + os.urandom(4).hex())
    from sqlalchemy import delete, insert, update

    item = item.model_copy(update={"service_id": item.id})
    async with database.transaction() as session:
        await session.execute(
            insert(services).values(
                service_id=item.service_id,
                name="Orders",
                base_url="https://orders.example.com",
                environment="production",
                tags=[],
                auth_mode="none",
                status="registered",
            )
        )
    await store.save(item)
    labels = _export(item, "orders-api")["prometheus"]["groups"][0]["rules"][0]["labels"]
    labels = {**labels, "alertname": "RelaynaReliabilityGoal"}
    alert = _Alert(status="firing", labels=labels, startsAt="2026-10-25T10:00:00Z")
    send = rt.failed_task_email_worker.notification_service.email_client.send
    try:
        outcomes = await asyncio.gather(_deliver(rt, store, alert), _deliver(rt, store, alert))
        assert {value["state"] for value in outcomes} == {"sent", "duplicate"}
        send.assert_awaited_once()
        assert send.call_args.kwargs["receivers"] == ("orders@example.com",)
        assert "environment=production" in send.call_args.kwargs["body"]
        receipt = (await store.get(item.id))["delivery"]
        assert receipt["sent"] == 1 and receipt["duplicates"] == 1
        resolved = alert.model_copy(update={"status": "resolved"})
        await _deliver(rt, store, resolved)
        assert (await _deliver(rt, store, alert))["state"] == "duplicate"
        next_alert = alert.model_copy(update={"startsAt": "2026-10-25T11:00:00Z"})
        await rt.failed_task_email_settings_store.update(enabled=False)
        with pytest.raises(HTTPException) as disabled:
            await _deliver(rt, store, next_alert)
        assert disabled.value.status_code == 503
        assert (await store.get(item.id))["delivery"]["state"] == "disabled"
        await rt.failed_task_email_settings_store.update(enabled=True)
        send.side_effect = RuntimeError("transport error with sensitive data")
        with pytest.raises(HTTPException) as failed:
            await _deliver(rt, store, next_alert)
        assert failed.value.status_code == 502
        assert (await store.get(item.id))["delivery"]["state"] == "failed"
        send.side_effect = None
        assert (await _deliver(rt, store, next_alert))["state"] == "sent"
        bad = alert.model_copy(update={"labels": {**labels, "environment": "staging"}})
        with pytest.raises(HTTPException) as mismatch:
            await _deliver(rt, store, bad)
        assert mismatch.value.status_code == 409
        async with database.transaction() as session:
            await session.execute(
                update(services).where(services.c.service_id == item.service_id).values(environment="staging")
            )
        with pytest.raises(HTTPException):
            await _deliver(rt, store, alert)
    finally:
        await store.remove(item.id)
        async with database.transaction() as session:
            await session.execute(delete(services).where(services.c.service_id == item.service_id))
        await rt.http_client.aclose()
        await database.engine.dispose()


@pytest.mark.parametrize("mode", ["operator", "entra"])
@pytest.mark.asyncio
async def test_machine_callback_bypasses_browser_auth_but_still_requires_its_own_secret(monkeypatch, mode):
    from relayna_studio._operator_auth import _install_operator_auth
    from relayna_studio.auth import StudioAuthMiddleware
    from relayna_studio.config import StudioBackendSettings

    rt = runtime()
    app = FastAPI()
    app.include_router(_create_reliability_router(rt, "dedicated-secret"))
    if mode == "operator":
        _install_operator_auth(
            app,
            StudioBackendSettings(
                redis_url="redis://test", auth_mode="operator", operator_token="op_live_" + "test-operator-credential"
            ),
        )
    else:
        app.add_middleware(StudioAuthMiddleware, service=SimpleNamespace(session_context=AsyncMock(return_value=None)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio") as client:
        response = await client.post(
            "/studio/ingest/reliability-alerts", content="bad", headers={"Authorization": "Bearer dedicated-secret"}
        )
        assert response.status_code == 422
        response = await client.post("/studio/ingest/reliability-alerts", json={})
        assert response.status_code == 401
    await rt.http_client.aclose()


def test_public_origin_is_explicit_and_cannot_contain_credentials_or_paths():
    rt = runtime()
    for origin in ["https://user:secret@example.com", "https://example.com/path", "javascript:alert(1)"]:
        with pytest.raises(ValueError):
            _create_reliability_router(rt, None, origin)
    exported = _export(goal(), "orders-api", "https://studio.example.com")
    rule = exported["prometheus"]["groups"][0]["rules"][0]
    assert rule["annotations"]["studio_url"].startswith("https://studio.example.com/reliability?")
    assert "from=" not in rule["annotations"]["studio_url"]  # Export time is not the future alert's time.


@pytest.mark.skipif(not os.getenv("RELAYNA_STUDIO_TEST_DATABASE_URL"), reason="Requires disposable PostgreSQL")
@pytest.mark.asyncio
async def test_goal_http_lifecycle_filters_export_preview_and_retirement():
    from sqlalchemy import delete, insert

    database = StudioDatabase(os.environ["RELAYNA_STUDIO_TEST_DATABASE_URL"])
    rt = runtime(database=database)
    service_id = "http-" + os.urandom(4).hex()
    rt.registry_service.get_service.return_value.service_id = service_id
    async with database.transaction() as session:
        await session.execute(
            insert(services).values(
                service_id=service_id,
                name="Orders",
                base_url="https://orders.example.com",
                environment="production",
                tags=[],
                auth_mode="none",
                status="registered",
            )
        )
    app = FastAPI()

    @app.middleware("http")
    async def admin(request, call_next):
        request.state.studio_member = SimpleNamespace(role=StudioRole.ADMIN, status=StudioMemberStatus.ACTIVE)
        return await call_next(request)

    app.include_router(_create_reliability_router(rt, "secret", "https://studio.example.com"))
    saved_id = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio") as client:
            body = goal(service_id=service_id).model_dump(exclude={"id", "revision", "created_at"})
            created = await client.post("/studio/reliability/goals", json=body)
            assert created.status_code == 201
            saved_id = created.json()["id"]
            path = "/studio/reliability/goals/" + saved_id
            listed = await client.get(
                "/studio/reliability/goals", params={"service_id": service_id, "environment": "production"}
            )
            assert [item["goal"]["id"] for item in listed.json()["items"]] == [saved_id]
            assert (await client.get("/studio/reliability/goals", params={"environment": "missing"})).json()[
                "items"
            ] == []
            exported = await client.get(path + "/export")
            assert exported.status_code == 200
            assert "https://studio.example.com/reliability?" in exported.text
            assert (await client.get(path + "/preview")).json()["state"] == "unavailable"
            rt.registry_service.get_service.return_value.metrics_config = None
            assert (await client.get(path + "/export")).status_code == 409
            assert (await client.delete(path)).json() == {"deleted": True}
            assert (await client.delete(path)).status_code == 404
            assert (await client.get(path + "/preview")).status_code == 404
            assert (await client.get(path + "/export")).status_code == 404
            saved_id = None
            rt.registry_service.get_service.return_value.environment = "staging"
            assert (await client.post("/studio/reliability/goals", json=body)).status_code == 409
    finally:
        if saved_id:
            await _Store(database).remove(saved_id)
        async with database.transaction() as session:
            await session.execute(delete(services).where(services.c.service_id == service_id))
        await rt.http_client.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_invalid_provider_payloads_and_negative_measurements_remain_unknown(monkeypatch):
    monkeypatch.setattr(reliability, "_now", lambda: NOW)
    for payload in [
        [],
        {"status": "success", "data": []},
        {"status": "success", "data": {"resultType": "vector", "result": "bad"}},
        {"status": "success", "data": {"resultType": "vector", "result": [1]}},
        {"status": "success", "data": {"resultType": "vector", "result": [{"value": []}]}},
    ]:
        rt = runtime(lambda request, payload=payload: httpx.Response(200, json=payload))
        assert (await _preview(rt, goal()))["state"] == "unavailable"
        await rt.http_client.aclose()
    rt = runtime(metric_handler({"backlog_oldest": -1, "evidence_timestamp": NOW.timestamp()}))
    assert (await _preview(rt, goal(kind="backlog_age")))["state"] == "insufficient"
    await rt.http_client.aclose()
    with pytest.raises(ValidationError):
        _Alert(status="firing", labels={}, startsAt="2026-10-25T12:00:00")
    with pytest.raises(HTTPException) as required:
        await _Store(None).get("unknown")
    assert required.value.status_code == 503


@pytest.mark.asyncio
async def test_callback_disabled_and_truncated_payloads_cannot_be_acknowledged():
    rt = runtime()
    for token, expected in [(None, 503), ("secret", 422)]:
        app = FastAPI()
        app.include_router(_create_reliability_router(rt, token))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://studio") as client:
            response = await client.post(
                "/studio/ingest/reliability-alerts",
                json={
                    "truncatedAlerts": 1,
                    "alerts": [{"status": "firing", "labels": {}, "startsAt": "2026-10-25T12:00:00Z"}],
                },
                headers={"Authorization": "Bearer secret"},
            )
            assert response.status_code == expected
    await rt.http_client.aclose()


@pytest.mark.parametrize(
    ("pulse_age", "watermark_age", "state"),
    [
        (30, 0, "observed"),
        (120, 0, "breaching"),
        (120, 181, "stale"),
        (None, 0, "no_data"),
        (30, None, "no_data"),
        (-5, 0, "insufficient"),
    ],
)
@pytest.mark.asyncio
async def test_missing_worker_requires_periodic_liveness_independent_of_task_activity(
    monkeypatch, pulse_age, watermark_age, state
):
    monkeypatch.setattr(reliability, "_now", lambda: NOW)
    seen = []

    def respond(request):
        query = request.url.params["query"]
        seen.append(query)
        if "relayna_reliability_worker_heartbeat_timestamp_seconds" in query:
            value = pulse_age
        elif "relayna_reliability_evidence_timestamp_seconds" in query:
            value = None if watermark_age is None else NOW.timestamp() - watermark_age
        else:
            # A fresh scrape of the old SDK gauge can still contain a task
            # heartbeat from hours ago. It must not drive this goal.
            raise AssertionError(f"Unexpected liveness source: {query}")
        rows = [] if value is None else [{"metric": {}, "value": [NOW.timestamp(), str(value)]}]
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": rows}})

    rt = runtime(respond)
    item = goal(kind="missing_worker", threshold=90, runtime_scope_confirmed=False)
    try:
        preview = await _preview(rt, item)
        assert preview["state"] == state
        if state in {"observed", "breaching"}:
            assert preview["value"] == pulse_age
        assert all('environment="production"' in query for query in seen)
        exported = _export(item, "orders-api")
        rules = exported["prometheus"]["groups"][0]["rules"]
        for rule in rules:
            assert "relayna_reliability_worker_heartbeat_timestamp_seconds" in rule["expr"]
            assert "relayna_reliability_evidence_timestamp_seconds" in rule["expr"]
            assert "relayna_worker_heartbeat_timestamp" not in rule["expr"]
        assert preview["queries"] == exported["queries"]
    finally:
        await rt.http_client.aclose()
