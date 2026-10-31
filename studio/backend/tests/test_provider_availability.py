from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from relayna_studio import app as studio_app
from relayna_studio._provider_availability import _provider_key, _ProviderAvailability
from relayna_studio.app import create_studio_app, get_studio_runtime
from relayna_studio.logs import StudioLogProviderError, StudioLogQuery, StudioLogQueryService
from test_studio_logs import FakeRedis, make_log_config, make_record


class ProviderFailure(Exception):
    pass


@pytest.fixture
def clock(monkeypatch):
    value = [100.0]
    monkeypatch.setattr("relayna_studio._provider_availability.monotonic", lambda: value[0])
    monkeypatch.setattr("relayna_studio._provider_availability.random.uniform", lambda *_: 1.0)
    return value


@pytest.mark.asyncio
async def test_empty_success_is_green_and_evidence_expires(clock):
    guard = _ProviderAvailability("Loki")
    key = ("svc", "target")
    assert guard.describe(key)["state"] == "unknown"
    assert await guard.run(key, AsyncMock(return_value=[]), ProviderFailure) == []
    assert guard.describe(key)["state"] == "ok"
    assert guard.describe(key)["last_successful_at"] is not None
    clock[0] += 31
    assert guard.describe(key)["state"] == "unknown"
    await guard.run(key, AsyncMock(return_value=[]), ProviderFailure)
    assert guard.describe(key)["state"] == "ok"


@pytest.mark.asyncio
async def test_failure_pauses_queries_and_backoff_recovers(clock):
    guard = _ProviderAvailability("Loki")
    key = ("svc", "target")
    failure = AsyncMock(side_effect=ProviderFailure("connection refused"))
    with pytest.raises(ProviderFailure, match="connection refused"):
        await guard.run(key, failure, ProviderFailure)
    for _ in range(10):
        with pytest.raises(ProviderFailure, match="paused"):
            await guard.run(key, failure, ProviderFailure)
    assert failure.await_count == 1
    assert guard.describe(key)["state"] == "unavailable"
    for expected in [60, 120, 240, 300, 300]:
        clock[0] = guard._states[key].retry_at + 1
        with pytest.raises(ProviderFailure):
            await guard.run(key, failure, ProviderFailure)
        assert guard._states[key].retry_at - clock[0] == expected
    clock[0] = guard._states[key].retry_at + 1
    assert await guard.run(key, AsyncMock(return_value="recovered"), ProviderFailure) == "recovered"
    assert guard.describe(key)["state"] == "ok"
    assert guard._states[key].failures == 0
    with pytest.raises(ProviderFailure):
        await guard.run(key, failure, ProviderFailure)
    assert guard._states[key].retry_at - clock[0] == 30


@pytest.mark.asyncio
async def test_concurrent_initial_failure_uses_one_probe(clock):
    guard = _ProviderAvailability("Prometheus")
    calls = 0

    async def unavailable():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise ProviderFailure("offline")

    results = await asyncio.gather(
        *(guard.run(("svc", "target"), unavailable, ProviderFailure) for _ in range(20)), return_exceptions=True
    )
    assert calls == 1
    assert all(isinstance(result, ProviderFailure) for result in results)
    assert sum("busy" in str(result) for result in results) == 3
    assert guard._active == guard._waiting == 0


@pytest.mark.asyncio
async def test_only_one_recovery_probe_runs_before_other_work(clock):
    guard = _ProviderAvailability("Tempo")
    key = ("svc", "target")
    with pytest.raises(ProviderFailure):
        await guard.run(key, AsyncMock(side_effect=ProviderFailure()), ProviderFailure)
    clock[0] += 31
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def recovered():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return calls

    tasks = [asyncio.create_task(guard.run(key, recovered, ProviderFailure)) for _ in range(5)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        assert len(await asyncio.gather(*tasks)) == 5
        assert calls == 5
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_deadline_is_total_and_does_not_leak_slots():
    guard = _ProviderAvailability("Loki")
    guard._timeout = 0.01
    with pytest.raises(ProviderFailure, match="time budget"):
        await guard.run(("svc", "target"), asyncio.Event().wait, ProviderFailure)
    assert guard._active == 0
    assert guard.describe(("svc", "target"))["state"] == "unavailable"


@pytest.mark.asyncio
async def test_cancellation_of_probe_or_waiter_does_not_mark_provider_down(clock):
    guard = _ProviderAvailability("Loki")
    key = ("svc", "target")
    entered = asyncio.Event()

    async def pending():
        entered.set()
        await asyncio.Event().wait()

    probe = asyncio.create_task(guard.run(key, pending, ProviderFailure))
    await asyncio.wait_for(entered.wait(), 1)
    waiter = asyncio.create_task(guard.run(key, pending, ProviderFailure))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not probe.done()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert guard._active == 0
    assert guard.describe(key)["state"] == "unknown"
    assert await guard.run(key, AsyncMock(return_value="ok"), ProviderFailure) == "ok"


@pytest.mark.asyncio
async def test_waiting_for_probe_has_a_deadline(clock):
    guard = _ProviderAvailability("Loki")
    guard._timeout = 0.01
    key = ("svc", "target")
    state = guard._state(key, ProviderFailure)
    await state.lock.acquire()
    try:
        with pytest.raises(ProviderFailure, match="pending"):
            await guard.run(key, AsyncMock(), ProviderFailure)
    finally:
        state.lock.release()
    assert guard._active == 0 and state.failures == 0


@pytest.mark.asyncio
async def test_concurrency_limit_and_cache_bound_protect_active_work(clock):
    guard = _ProviderAvailability("Prometheus")
    guard._max_states = 2
    await guard.run(("0", "target"), AsyncMock(return_value="warm"), ProviderFailure)
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def pending():
        await entered.put(True)
        await release.wait()
        return "ok"

    tasks = [asyncio.create_task(guard.run((str(i), "target"), pending, ProviderFailure)) for i in range(2)]
    try:
        await asyncio.wait_for(entered.get(), 1)
        await asyncio.wait_for(entered.get(), 1)
        with pytest.raises(ProviderFailure, match="busy"):
            await guard.run(("0", "target"), AsyncMock(), ProviderFailure)
        for i in range(10):
            with pytest.raises(ProviderFailure, match="busy"):
                await guard.run((f"busy-{i}", "target"), AsyncMock(), ProviderFailure)
            assert len(guard._states) <= guard._max_states
        assert guard._active == 2
        release.set()
        assert await asyncio.gather(*tasks) == ["ok", "ok"]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    await guard.run(("new", "target"), AsyncMock(return_value="ok"), ProviderFailure)
    assert guard._active == 0


@pytest.mark.asyncio
async def test_old_inflight_success_does_not_clear_newer_failure(clock):
    guard = _ProviderAvailability("Loki")
    key = ("svc", "target")
    await guard.run(key, AsyncMock(return_value="ok"), ProviderFailure)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_success():
        entered.set()
        await release.wait()
        return "ok"

    old = asyncio.create_task(guard.run(key, slow_success, ProviderFailure))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with pytest.raises(ProviderFailure):
            await guard.run(key, AsyncMock(side_effect=ProviderFailure()), ProviderFailure)
        release.set()
        await old
        assert guard.describe(key)["state"] == "unavailable"
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_configuration_changes_and_other_providers_are_independent(clock):
    first = make_log_config(tenant_id="one")
    second = make_log_config(tenant_id="two")
    a, b = _provider_key("svc", first), _provider_key("svc", second)
    assert a != b and a != _provider_key("other-service", first)
    guard = _ProviderAvailability("Loki")
    with pytest.raises(ProviderFailure):
        await guard.run(a, AsyncMock(side_effect=ProviderFailure()), ProviderFailure)
    assert await guard.run(b, AsyncMock(return_value=[]), ProviderFailure) == []
    assert await _ProviderAvailability("Tempo").run(a, AsyncMock(return_value=[]), ProviderFailure) == []
    invalid = AsyncMock(side_effect=ValueError("invalid query"))
    with pytest.raises(ValueError):
        await guard.run(b, invalid, ProviderFailure)
    assert guard.describe(b)["state"] == "ok"


@pytest.mark.asyncio
async def test_log_service_suppresses_failed_provider_but_allows_new_configuration(clock):
    record = make_record(log_config=make_log_config())
    registry = SimpleNamespace(get_service=AsyncMock(return_value=record))
    provider = SimpleNamespace(query_logs=AsyncMock(side_effect=StudioLogProviderError("offline")))
    service = StudioLogQueryService(registry_service=registry, providers={"loki": provider})
    for _ in range(3):
        with pytest.raises(StudioLogProviderError):
            await service.query_task_logs(record.service_id, "task", StudioLogQuery())
    assert provider.query_logs.await_count == 1
    record.log_config = make_log_config(tenant_id="changed")
    provider.query_logs.side_effect = None
    provider.query_logs.return_value = "healthy result"
    assert await service.query_service_logs(record.service_id, StudioLogQuery()) == "healthy result"


def test_provider_clients_are_distinct_and_closed(monkeypatch):
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    clients = []

    def factory(timeout):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)), timeout=timeout)
        clients.append(client)
        return client

    app = create_studio_app(
        redis_url="redis://test",
        federation_client_factory=factory,
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    with TestClient(app):
        runtime = get_studio_runtime(app)
        assert len(clients) == 4 and len({id(client) for client in clients}) == 4
        assert runtime.log_query_service._providers["loki"]._http_client is clients[1]
        assert runtime.metrics_query_service._providers["prometheus"]._http_client is clients[2]
        assert runtime.trace_query_service._providers["tempo"]._http_client is clients[3]
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_stalled_loki_pool_does_not_block_core_client(monkeypatch):
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    entered, release = asyncio.Event(), asyncio.Event()
    handlers = set()

    async def handle(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            if b"/loki " in request:
                entered.set()
                await release.wait()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app = create_studio_app(
        redis_url="redis://test",
        federation_client_factory=lambda timeout: httpx.AsyncClient(
            timeout=timeout, trust_env=False, limits=httpx.Limits(max_connections=1)
        ),
        pull_sync_interval_seconds=None,
        health_refresh_interval_seconds=None,
        retention_prune_interval_seconds=None,
    )
    try:
        async with app.router.lifespan_context(app):
            runtime = get_studio_runtime(app)
            pending = asyncio.create_task(runtime._provider_http_clients[0].get(f"http://127.0.0.1:{port}/loki"))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert (
                    await asyncio.wait_for(runtime.http_client.get(f"http://127.0.0.1:{port}/core"), 1)
                ).status_code == 200
                assert not pending.done()
                release.set()
                await pending
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*list(handlers), return_exceptions=True)


@pytest.mark.asyncio
async def test_metrics_outage_skips_task_window_upstream_work(clock):
    from relayna_studio.metrics import StudioMetricsProviderError, StudioMetricsQuery, StudioMetricsQueryService
    from relayna_studio.registry import PrometheusMetricsConfig

    record = make_record().model_copy(
        update={"metrics_config": PrometheusMetricsConfig(base_url="https://metrics.test", namespace="production")}
    )
    provider = SimpleNamespace(query_metrics=AsyncMock(side_effect=StudioMetricsProviderError("offline")))
    service = StudioMetricsQueryService(
        registry_service=SimpleNamespace(get_service=AsyncMock(return_value=record)), providers={"prometheus": provider}
    )
    service._with_task_window = AsyncMock()
    with pytest.raises(StudioMetricsProviderError):
        await service.query_service_metrics(record.service_id, StudioMetricsQuery())
    with pytest.raises(StudioMetricsProviderError, match="paused"):
        await service.query_task_metrics(record.service_id, "task", StudioMetricsQuery())
    service._with_task_window.assert_not_awaited()
    provider.query_metrics.assert_awaited_once()


@pytest.mark.asyncio
async def test_tempo_outage_skips_trace_discovery_and_keeps_trace_path_available(clock):
    from relayna_studio.registry import TempoTraceConfig
    from relayna_studio.traces import StudioTraceProviderError, StudioTraceQueryService

    record = make_record().model_copy(update={"trace_config": TempoTraceConfig(base_url="https://tempo.test")})
    provider = SimpleNamespace(query_trace=AsyncMock(side_effect=StudioTraceProviderError("offline")))
    service = StudioTraceQueryService(
        registry_service=SimpleNamespace(get_service=AsyncMock(return_value=record)), providers={"tempo": provider}
    )
    service._discover_task_trace_ids = AsyncMock(return_value=(["a" * 32], []))
    with pytest.raises(StudioTraceProviderError):
        await service.query_task_traces(record.service_id, "task")
    with pytest.raises(StudioTraceProviderError, match="paused"):
        await service.query_task_traces(record.service_id, "task")
    service._discover_task_trace_ids.assert_awaited_once()
    path = await service.query_task_trace_path(record.service_id, "task")
    assert path.service_id == record.service_id
    assert any("paused" in warning for warning in path.warnings)


@pytest.mark.asyncio
async def test_failed_metrics_batch_cancels_siblings_and_queued_queries():
    from relayna_studio.metrics import PrometheusMetricsProvider, StudioMetricsProviderError, StudioMetricsQuery
    from relayna_studio.registry import PrometheusMetricsConfig

    active = 0
    started = 0

    async def query_group(**kwargs):
        nonlocal active, started
        started += 1
        active += 1
        try:
            if started == 1:
                await asyncio.sleep(0)
                raise StudioMetricsProviderError("Prometheus offline")
            await asyncio.Event().wait()
        finally:
            active -= 1

    async with httpx.AsyncClient() as client:
        provider = PrometheusMetricsProvider(http_client=client)
        provider._query_group = query_group
        with pytest.raises(StudioMetricsProviderError):
            await asyncio.wait_for(
                provider.query_metrics(
                    service=make_record(),
                    config=PrometheusMetricsConfig(base_url="https://metrics.test", namespace="production"),
                    query=StudioMetricsQuery(),
                ),
                1,
            )
        assert active == 0
        assert started < 10


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 413, 414, 422])
@pytest.mark.parametrize("family", ["loki", "prometheus", "pods", "tempo"])
async def test_query_rejection_returns_validation_error_and_allows_next_query(status, family):
    from fastapi import FastAPI
    from relayna_studio.logs import LokiLogProvider, create_studio_logs_router
    from relayna_studio.metrics import (
        PrometheusMetricsProvider,
        StudioMetricsQueryService,
        create_studio_metrics_router,
    )
    from relayna_studio.registry import PrometheusMetricsConfig, StudioOutboundUrlPolicy, TempoTraceConfig
    from relayna_studio.traces import StudioTraceQueryService, TempoTraceProvider, create_studio_traces_router

    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                status, json={"status": "error", "errorType": "bad_data", "error": "query range exceeds limit"}
            )
        if family == "loki":
            payload = {"status": "success", "data": {"resultType": "streams", "result": []}}
        elif family == "tempo":
            payload = {"batches": []}
        else:
            payload = {
                "status": "success",
                "data": {"resultType": "vector" if family == "pods" else "matrix", "result": []},
            }
        return httpx.Response(200, json=payload)

    policy = StudioOutboundUrlPolicy(allowed_hosts=["loki.example.test"])
    record = make_record(log_config=make_log_config()).model_copy(
        update={
            "metrics_config": PrometheusMetricsConfig(base_url="https://loki.example.test", namespace="production"),
            "trace_config": TempoTraceConfig(base_url="https://loki.example.test"),
        }
    )
    registry = SimpleNamespace(get_service=AsyncMock(return_value=record))
    app = FastAPI()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as outbound:
        if family == "loki":
            service = StudioLogQueryService(
                registry_service=registry,
                providers={"loki": LokiLogProvider(http_client=outbound, outbound_policy=policy)},
            )
            app.include_router(create_studio_logs_router(log_query_service=service))
            endpoint = f"/studio/tasks/{record.service_id}/task-a/logs"
            config = record.log_config
        elif family in {"prometheus", "pods"}:
            service = StudioMetricsQueryService(
                registry_service=registry,
                providers={"prometheus": PrometheusMetricsProvider(http_client=outbound, outbound_policy=policy)},
            )
            app.include_router(create_studio_metrics_router(metrics_query_service=service))
            endpoint = f"/studio/services/{record.service_id}/" + (
                "pods" if family == "pods" else "metrics?group=cpu_usage"
            )
            config = record.metrics_config
        else:
            service = StudioTraceQueryService(
                registry_service=registry,
                providers={"tempo": TempoTraceProvider(http_client=outbound, outbound_policy=policy)},
            )
            service._discover_task_trace_ids = AsyncMock(return_value=(["a" * 32], []))
            app.include_router(create_studio_traces_router(trace_query_service=service))
            endpoint = f"/studio/tasks/{record.service_id}/task-a/traces"
            config = record.trace_config
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://studio.test") as client:
            params = {"from": "2020-01-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"}
            if family == "prometheus":
                params["group"] = "cpu_usage"
            invalid = await client.get(endpoint, params=params)
            assert invalid.status_code == 422, invalid.text
            assert service._availability.describe(_provider_key(record.service_id, config))["state"] != "unavailable"
            valid = await client.get(endpoint.replace("/task-a/", "/task-b/"))
            assert valid.status_code == 200, valid.text
            assert len(calls) == 2
            assert service._availability.describe(_provider_key(record.service_id, config))["state"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 408, 429, 500, 503])
async def test_provider_auth_throttle_and_availability_errors_still_back_off(status):
    from relayna_studio.logs import LokiLogProvider

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status)

    record = make_record(log_config=make_log_config())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = StudioLogQueryService(
            registry_service=SimpleNamespace(get_service=AsyncMock(return_value=record)),
            providers={"loki": LokiLogProvider(http_client=client)},
        )
        for _ in range(2):
            with pytest.raises(StudioLogProviderError, match="paused"):
                await service.query_service_logs(record.service_id, StudioLogQuery())
        assert len(calls) == 1
