from __future__ import annotations

import asyncio

import httpx
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import (
    RedisServiceRegistryStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioOutboundUrlPolicy,
    StudioTraceQueryService,
    TempoTraceConfig,
    TempoTraceProvider,
    create_studio_app,
    get_studio_runtime,
)
from relayna_studio.traces import _collect_trace_ids
from test_studio_logs import FakeRedis, TrackingAsyncClient


def tempo_trace_config(
    base_url: str = "https://tempo.example.test",
    public_base_url: str | None = None,
) -> TempoTraceConfig:
    return TempoTraceConfig(provider="tempo", base_url=base_url, public_base_url=public_base_url, tenant_id="tenant-a")


def make_record(*, trace_config: TempoTraceConfig | None = None) -> ServiceRecord:
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
        trace_config=trace_config,
    )


def install_service(app, record: ServiceRecord) -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(runtime.registry_store.create(record))


def tempo_success_response() -> dict[str, object]:
    return {
        "batches": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "payments-worker"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "EREREREREREREREREREREQ==",
                                "spanId": "IiIiIiIiIiI=",
                                "parentSpanId": "AAAAAAAAAAE=",
                                "name": "handler",
                                "kind": "SPAN_KIND_CONSUMER",
                                "startTimeUnixNano": "1712797200000000000",
                                "endTimeUnixNano": "1712797200500000000",
                                "attributes": [
                                    {"key": "messaging.system", "value": {"stringValue": "rabbitmq"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


def test_tempo_provider_normalizes_spans_and_uses_tenant_header() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["tenant"] = request.headers.get("X-Scope-OrgID", "")
        return httpx.Response(200, json=tempo_success_response())

    async def scenario() -> None:
        provider = TempoTraceProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
            outbound_policy=StudioOutboundUrlPolicy(allowed_hosts=("tempo.example.test",)),
        )
        spans = await provider.query_trace(
            service=make_record(trace_config=tempo_trace_config()),
            config=tempo_trace_config(),
            trace_id="11111111111111111111111111111111",
        )
        assert spans[0].trace_id == "11111111111111111111111111111111"
        assert spans[0].span_id == "2222222222222222"
        assert spans[0].parent_span_id == "0000000000000001"
        assert spans[0].duration_ms == 500.0
        assert spans[0].service == "payments-worker"
        assert spans[0].backend_url == "https://tempo.example.test/api/traces/11111111111111111111111111111111"

    asyncio.run(scenario())
    assert observed == {"path": "/api/traces/11111111111111111111111111111111", "tenant": "tenant-a"}


def test_tempo_provider_uses_public_base_url_for_browser_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("http://host.docker.internal:3200/")
        return httpx.Response(200, json=tempo_success_response())

    async def scenario() -> None:
        provider = TempoTraceProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
            outbound_policy=StudioOutboundUrlPolicy(allowed_hosts=("host.docker.internal",)),
        )
        spans = await provider.query_trace(
            service=make_record(
                trace_config=tempo_trace_config(
                    base_url="http://host.docker.internal:3200",
                    public_base_url="http://localhost:3200",
                )
            ),
            config=tempo_trace_config(
                base_url="http://host.docker.internal:3200",
                public_base_url="http://localhost:3200",
            ),
            trace_id="11111111111111111111111111111111",
        )
        assert spans[0].backend_url == "http://localhost:3200/api/traces/11111111111111111111111111111111"

    asyncio.run(scenario())


def test_tempo_provider_normalizes_v2_trace_envelope() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        return httpx.Response(200, json={"trace": tempo_success_response()})

    async def scenario() -> None:
        provider = TempoTraceProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
            outbound_policy=StudioOutboundUrlPolicy(allowed_hosts=("tempo.example.test",)),
        )
        spans = await provider.query_trace(
            service=make_record(trace_config=tempo_trace_config()),
            config=TempoTraceConfig(
                provider="tempo",
                base_url="https://tempo.example.test",
                query_path="/api/v2/traces/{trace_id}",
            ),
            trace_id="11111111111111111111111111111111",
        )
        assert spans[0].trace_id == "11111111111111111111111111111111"
        assert spans[0].span_id == "2222222222222222"
        assert spans[0].name == "handler"

    asyncio.run(scenario())
    assert observed == {"path": "/api/v2/traces/11111111111111111111111111111111"}


def test_trace_query_returns_non_error_empty_state_without_trace_config() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        registry = ServiceRegistryService(store=store)
        await store.create(make_record(trace_config=None))
        service = StudioTraceQueryService(registry_service=registry, providers={})
        response = await service.query_task_traces("payments-api", "task-123")
        assert response.spans == []
        assert "No trace provider configured" in response.warnings[-1]

    asyncio.run(scenario())


def test_trace_discovery_reads_json_log_message_bodies() -> None:
    trace_ids: set[str] = set()

    _collect_trace_ids(
        {
            "items": [
                {
                    "message": (
                        '{"task_id":"order-1001",'
                        '"trace_id":"33333333333333333333333333333333",'
                        '"span_id":"4444444444444444"}'
                    )
                }
            ]
        },
        trace_ids,
    )

    assert trace_ids == {"33333333333333333333333333333333"}


def test_task_trace_route_queries_tempo_from_task_detail_trace_id(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/task-123":
            return httpx.Response(200, json={"task_id": "task-123", "event": {"task_id": "task-123"}})
        if request.url.path == "/history":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "count": 1,
                    "events": [
                        {
                            "task_id": "task-123",
                            "status": "completed",
                            "trace_id": "11111111111111111111111111111111",
                        }
                    ],
                },
            )
        if request.url.path == "/broker/dlq/messages":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={"task_id": "task-123", "topology_kind": "shared_tasks", "nodes": [], "edges": []},
            )
        if request.url.path == "/api/traces/11111111111111111111111111111111":
            return httpx.Response(200, json=tempo_success_response())
        return httpx.Response(404, json={"detail": "missing"})

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(trace_config=tempo_trace_config()))
        response = client.get("/studio/tasks/payments-api/task-123/traces")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_ids"] == ["11111111111111111111111111111111"]
    assert payload["spans"][0]["span_id"] == "2222222222222222"
