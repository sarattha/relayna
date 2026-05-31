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
from relayna_studio.traces import StudioTraceResponse, StudioTraceSpan, _build_trace_path_response, _collect_trace_ids
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


def tempo_task_path_response() -> dict[str, object]:
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
                                "name": "relayna.consumer.task_message",
                                "kind": "SPAN_KIND_CONSUMER",
                                "startTimeUnixNano": "1712797200000000000",
                                "endTimeUnixNano": "1712797200500000000",
                                "attributes": [
                                    {"key": "messaging.source.name", "value": {"stringValue": "payments.queue"}},
                                    {"key": "relayna.task_id", "value": {"stringValue": "task-123"}},
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


def test_trace_path_builder_covers_completed_status_event() -> None:
    response = _build_trace_path_response(
        service=make_record(trace_config=None),
        task_id="task-123",
        detail_payload={
            "task_ref": {"correlation_id": "corr-123"},
            "execution_graph": {
                "summary": {
                    "status": "completed",
                    "started_at": "2026-04-08T10:00:00Z",
                    "ended_at": "2026-04-08T10:00:01Z",
                    "duration_ms": 1000,
                    "graph_completeness": "full",
                    "live_state_counts": {"succeeded": 2},
                },
                "nodes": [
                    {"id": "task:task-123", "kind": "task", "task_id": "task-123", "label": "task-123"},
                    {
                        "id": "status:task-123:1",
                        "kind": "status_event",
                        "task_id": "task-123",
                        "label": "completed",
                        "timestamp": "2026-04-08T10:00:01Z",
                        "state": "succeeded",
                        "annotations": {"status": "completed"},
                    },
                ],
                "edges": [
                    {
                        "source": "task:task-123",
                        "target": "status:task-123:1",
                        "kind": "published_status",
                    }
                ],
            },
        },
        trace_response=StudioTraceResponse(service_id="payments-api", task_id="task-123"),
        event_items=[
            {
                "dedupe_key": "evt-completed",
                "task_id": "task-123",
                "event_type": "status.completed",
                "source_kind": "status",
                "timestamp": "2026-04-08T10:00:01Z",
                "payload": {"status": "completed"},
            }
        ],
        warnings=[],
    )

    assert response.summary.status == "completed"
    assert response.summary.event_count == 1
    status_node = next(item for item in response.nodes if item.id == "status:task-123:1")
    assert status_node.state == "succeeded"
    assert any(item.source == "studio_event" for item in status_node.evidence)


def test_trace_path_builder_covers_retry_path() -> None:
    response = _build_trace_path_response(
        service=make_record(trace_config=None),
        task_id="task-123",
        detail_payload={
            "execution_graph": {
                "summary": {
                    "status": "retrying",
                    "started_at": "2026-04-08T10:00:00Z",
                    "ended_at": "2026-04-08T10:00:03Z",
                    "duration_ms": 3000,
                    "graph_completeness": "full",
                },
                "nodes": [
                    {"id": "task:task-123", "kind": "task", "task_id": "task-123", "label": "task-123"},
                    {
                        "id": "attempt:1",
                        "kind": "task_attempt",
                        "task_id": "task-123",
                        "label": "attempt 1",
                        "timestamp": "2026-04-08T10:00:00Z",
                        "state": "retrying",
                        "annotations": {"queue_name": "payments.queue", "retry_attempt": 1},
                    },
                    {
                        "id": "retry:1",
                        "kind": "retry",
                        "task_id": "task-123",
                        "label": "retry 2",
                        "timestamp": "2026-04-08T10:00:02Z",
                        "state": "retrying",
                        "annotations": {"queue_name": "payments.queue.retry", "source_queue_name": "payments.queue"},
                    },
                    {
                        "id": "attempt:2",
                        "kind": "task_attempt",
                        "task_id": "task-123",
                        "label": "attempt 2",
                        "timestamp": "2026-04-08T10:00:03Z",
                        "state": "running",
                        "annotations": {"queue_name": "payments.queue", "retry_attempt": 2},
                    },
                ],
                "edges": [
                    {"source": "task:task-123", "target": "attempt:1", "kind": "received_by"},
                    {"source": "attempt:1", "target": "retry:1", "kind": "retried_as"},
                    {"source": "retry:1", "target": "attempt:2", "kind": "received_by"},
                ],
            }
        },
        trace_response=StudioTraceResponse(service_id="payments-api", task_id="task-123"),
        event_items=[
            {
                "dedupe_key": "evt-retry",
                "task_id": "task-123",
                "event_type": "ConsumerRetryScheduled",
                "source_kind": "observation",
                "timestamp": "2026-04-08T10:00:02Z",
                "payload": {"queue_name": "payments.queue.retry"},
            }
        ],
        warnings=[],
    )

    assert response.summary.status == "retrying"
    assert {edge.kind for edge in response.edges} >= {"retried_as", "received_by"}
    retry_node = next(item for item in response.nodes if item.id == "retry:1")
    assert any(item.source == "studio_event" for item in retry_node.evidence)


def test_trace_path_builder_covers_workflow_stage_span() -> None:
    response = _build_trace_path_response(
        service=make_record(trace_config=None),
        task_id="workflow-123",
        detail_payload={
            "execution_graph": {
                "summary": {
                    "status": "running",
                    "started_at": "2026-04-08T10:00:00Z",
                    "ended_at": "2026-04-08T10:00:04Z",
                    "duration_ms": 4000,
                    "graph_completeness": "full",
                },
                "nodes": [
                    {"id": "task:workflow-123", "kind": "task", "task_id": "workflow-123", "label": "workflow-123"},
                    {
                        "id": "workflow-message:msg-1",
                        "kind": "workflow_message",
                        "task_id": "workflow-123",
                        "label": "review",
                        "timestamp": "2026-04-08T10:00:01Z",
                        "annotations": {"stage": "review", "queue_name": "workflow.review"},
                    },
                    {
                        "id": "stage-attempt:workflow-123:review:1",
                        "kind": "stage_attempt",
                        "task_id": "workflow-123",
                        "label": "review attempt 1",
                        "timestamp": "2026-04-08T10:00:02Z",
                        "state": "running",
                        "annotations": {"stage": "review", "queue_name": "workflow.review"},
                    },
                ],
                "edges": [
                    {
                        "source": "workflow-message:msg-1",
                        "target": "stage-attempt:workflow-123:review:1",
                        "kind": "entered_stage",
                    }
                ],
            }
        },
        trace_response=StudioTraceResponse(
            service_id="payments-api",
            task_id="workflow-123",
            trace_ids=["11111111111111111111111111111111"],
            spans=[
                StudioTraceSpan(
                    trace_id="11111111111111111111111111111111",
                    span_id="2222222222222222",
                    name="relayna.consumer.workflow_message",
                    kind="SPAN_KIND_CONSUMER",
                    service="payments-worker",
                    start_time="2026-04-08T10:00:02Z",
                    end_time="2026-04-08T10:00:04Z",
                    duration_ms=2000,
                    attributes={"relayna.task_id": "workflow-123", "relayna.stage": "review"},
                )
            ],
        ),
        event_items=[],
        warnings=[],
    )

    stage_node = next(item for item in response.nodes if item.id == "stage-attempt:workflow-123:review:1")
    assert stage_node.trace_id == "11111111111111111111111111111111"
    assert stage_node.span_id == "2222222222222222"
    assert any(item.source == "span" for item in stage_node.evidence)


def test_trace_path_builder_attaches_dead_letter_event_to_created_dlq_node() -> None:
    response = _build_trace_path_response(
        service=make_record(trace_config=None),
        task_id="task-123",
        detail_payload={
            "execution_graph": {
                "summary": {
                    "status": "failed",
                    "started_at": "2026-04-08T10:00:00Z",
                    "ended_at": "2026-04-08T10:00:03Z",
                    "duration_ms": 3000,
                    "graph_completeness": "partial",
                },
                "nodes": [
                    {"id": "task:task-123", "kind": "task", "task_id": "task-123", "label": "task-123"},
                    {
                        "id": "attempt:task-123:1",
                        "kind": "task_attempt",
                        "task_id": "task-123",
                        "label": "attempt 1",
                        "timestamp": "2026-04-08T10:00:01Z",
                        "state": "failed",
                        "annotations": {"queue_name": "payments.queue"},
                    },
                ],
                "edges": [{"source": "task:task-123", "target": "attempt:task-123:1", "kind": "received_by"}],
            },
            "dlq_messages": {
                "items": [
                    {
                        "dlq_id": "dlq-1",
                        "queue_name": "payments.queue.dlq",
                        "source_queue_name": "payments.queue",
                        "task_id": "task-123",
                        "reason": "handler_failed",
                        "dead_lettered_at": "2026-04-08T10:00:03Z",
                        "state": "dead_lettered",
                    }
                ]
            },
        },
        trace_response=StudioTraceResponse(service_id="payments-api", task_id="task-123"),
        event_items=[
            {
                "dedupe_key": "evt-dlq",
                "task_id": "task-123",
                "event_type": "TaskDeadLettered",
                "source_kind": "observation",
                "timestamp": "2026-04-08T10:00:03Z",
                "payload": {"queue_name": "payments.queue.dlq", "reason": "handler_failed"},
            }
        ],
        warnings=[],
    )

    dlq_node = next(item for item in response.nodes if item.kind == "dlq_record")
    assert [item.source for item in dlq_node.evidence] == ["dlq", "studio_event"]
    attempt_node = next(item for item in response.nodes if item.id == "attempt:task-123:1")
    assert not any(item.source == "studio_event" and item.label == "TaskDeadLettered" for item in attempt_node.evidence)


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


def test_task_trace_path_route_reuses_task_detail_for_trace_ids(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    path_counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path_counts[request.url.path] = path_counts.get(request.url.path, 0) + 1
        if request.url.path == "/status/task-123":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "event": {
                        "task_id": "task-123",
                        "status": "completed",
                        "trace_id": "11111111111111111111111111111111",
                    },
                },
            )
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
                            "timestamp": "2024-04-10T21:00:02Z",
                            "trace_id": "11111111111111111111111111111111",
                        }
                    ],
                },
            )
        if request.url.path == "/dlq/messages":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks",
                    "summary": {"status": "completed"},
                    "nodes": [
                        {
                            "id": "task:task-123",
                            "kind": "task",
                            "task_id": "task-123",
                            "label": "task-123",
                            "annotations": {"trace_id": "11111111111111111111111111111111"},
                        }
                    ],
                    "edges": [],
                },
            )
        if request.url.path == "/api/traces/11111111111111111111111111111111":
            return httpx.Response(200, json=tempo_task_path_response())
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
        response = client.get("/studio/tasks/payments-api/task-123/trace-path")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["trace_ids"] == ["11111111111111111111111111111111"]
    assert payload["summary"]["span_count"] == 1
    assert path_counts["/status/task-123"] == 1
    assert path_counts["/history"] == 1
    assert path_counts["/dlq/messages"] == 1
    assert path_counts["/executions/task-123/graph"] == 1


def test_task_trace_path_route_merges_graph_spans_logs_and_dlq_evidence(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/task-123":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "event": {
                        "task_id": "task-123",
                        "status": "failed",
                        "correlation_id": "corr-123",
                    },
                },
            )
        if request.url.path == "/history":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "count": 2,
                    "events": [
                        {
                            "task_id": "task-123",
                            "status": "queued",
                            "timestamp": "2024-04-10T21:00:00Z",
                            "trace_id": "11111111111111111111111111111111",
                            "correlation_id": "corr-123",
                        },
                        {
                            "task_id": "task-123",
                            "status": "failed",
                            "timestamp": "2024-04-10T21:00:02Z",
                            "trace_id": "11111111111111111111111111111111",
                            "correlation_id": "corr-123",
                        },
                    ],
                },
            )
        if request.url.path == "/dlq/messages":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "dlq_id": "dlq-1",
                            "queue_name": "payments.queue.dlq",
                            "source_queue_name": "payments.queue",
                            "retry_queue_name": "payments.queue.retry",
                            "task_id": "task-123",
                            "reason": "handler_failed",
                            "retry_attempt": 3,
                            "max_retries": 3,
                            "dead_lettered_at": "2024-04-10T21:00:03Z",
                            "state": "stored",
                            "replay_count": 0,
                            "body_encoding": "json",
                        }
                    ]
                },
            )
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "shared_tasks",
                    "summary": {
                        "status": "failed",
                        "started_at": "2024-04-10T21:00:00Z",
                        "ended_at": "2024-04-10T21:00:03Z",
                        "duration_ms": 3000,
                        "graph_completeness": "full",
                        "live_state_counts": {"failed": 1, "dead_lettered": 1},
                    },
                    "nodes": [
                        {"id": "task:task-123", "kind": "task", "task_id": "task-123", "label": "task-123"},
                        {
                            "id": "attempt:task-123:1",
                            "kind": "task_attempt",
                            "task_id": "task-123",
                            "label": "task-123 attempt 1",
                            "timestamp": "2024-04-10T21:00:00Z",
                            "state": "failed",
                            "annotations": {"queue_name": "payments.queue", "retry_attempt": 3},
                        },
                        {
                            "id": "dlq:task-123:1",
                            "kind": "dlq_record",
                            "task_id": "task-123",
                            "label": "handler_failed",
                            "timestamp": "2024-04-10T21:00:03Z",
                            "state": "dead_lettered",
                            "annotations": {"queue_name": "payments.queue.dlq", "source_queue_name": "payments.queue"},
                        },
                    ],
                    "edges": [
                        {"source": "task:task-123", "target": "attempt:task-123:1", "kind": "received_by"},
                        {
                            "source": "attempt:task-123:1",
                            "target": "dlq:task-123:1",
                            "kind": "dead_lettered_to",
                            "timestamp": "2024-04-10T21:00:03Z",
                        },
                    ],
                },
            )
        if request.url.path == "/api/traces/11111111111111111111111111111111":
            return httpx.Response(200, json=tempo_task_path_response())
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
        response = client.get("/studio/tasks/payments-api/task-123/trace-path")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["status"] == "failed"
    assert payload["summary"]["trace_ids"] == ["11111111111111111111111111111111"]
    assert payload["summary"]["dlq_count"] == 1
    assert payload["log_metadata"]["task_id"] == "task-123"
    attempt_node = next(item for item in payload["nodes"] if item["id"] == "attempt:task-123:1")
    assert attempt_node["trace_id"] == "11111111111111111111111111111111"
    assert any(item["source"] == "span" for item in attempt_node["evidence"])
    dlq_node = next(item for item in payload["nodes"] if item["id"] == "dlq:task-123:1")
    assert any(item["source"] == "dlq" and item["source_id"] == "dlq-1" for item in dlq_node["evidence"])
