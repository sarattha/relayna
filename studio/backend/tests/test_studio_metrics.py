from __future__ import annotations

import asyncio

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from pydantic import ValidationError
from relayna_studio import (
    CreateServiceRequest,
    PrometheusMetricsConfig,
    PrometheusMetricsProvider,
    RedisServiceRegistryStore,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioMetricGroup,
    StudioMetricsQuery,
    UpdateServiceRequest,
    create_studio_app,
    get_studio_runtime,
)
from test_studio_logs import FakeRedis, TrackingAsyncClient


def make_metrics_config(
    *,
    base_url: str = "https://prometheus.example.test",
    service_selector_labels: dict[str, str] | None = None,
    runtime_service_label_value: str | None = None,
) -> PrometheusMetricsConfig:
    return PrometheusMetricsConfig(
        provider="prometheus",
        base_url=base_url,
        namespace="prod",
        service_selector_labels=service_selector_labels or {"app": "payments-api"},
        runtime_service_label_value=runtime_service_label_value,
    )


def make_record(*, metrics_config: PrometheusMetricsConfig | None = None) -> ServiceRecord:
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
        log_config=None,
        metrics_config=metrics_config,
    )


def install_service(app, record: ServiceRecord) -> None:
    runtime = get_studio_runtime(app)
    asyncio.run(runtime.registry_store.create(record))


def prometheus_success_response() -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"pod": "payments-abc", "container": "worker"},
                    "values": [[1712797200, "0.125"], [1712797230, "0.25"]],
                }
            ],
        },
    }


def test_metrics_config_rejects_empty_and_high_cardinality_selectors() -> None:
    with pytest.raises(ValidationError):
        PrometheusMetricsConfig(
            provider="prometheus",
            base_url="https://prometheus.example.test",
            namespace="prod",
            service_selector_labels={},
        )
    with pytest.raises(ValidationError):
        PrometheusMetricsConfig(
            provider="prometheus",
            base_url="https://prometheus.example.test",
            namespace="prod",
            service_selector_labels={"task_id": "task-123"},
        )


def test_registry_accepts_metrics_config_and_preserves_log_only_records() -> None:
    async def scenario() -> None:
        store = RedisServiceRegistryStore(FakeRedis())
        service = ServiceRegistryService(store=store)
        created = await service.create_service(
            CreateServiceRequest(
                service_id="payments-api",
                name="Payments API",
                base_url="https://payments.example.test",
                environment="prod",
                tags=[],
                auth_mode="internal_network",
                metrics_config=make_metrics_config(),
            )
        )
        assert created.metrics_config is not None
        assert created.metrics_config.provider == "prometheus"

        updated = await service.update_service("payments-api", UpdateServiceRequest(metrics_config=None))
        assert updated.metrics_config is None

    asyncio.run(scenario())


def test_prometheus_provider_builds_queries_and_normalizes_series() -> None:
    observed_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(request.url.params["query"])
        assert request.url.params["start"] == "1712782800.0"
        assert request.url.params["end"] == "1712782860.0"
        assert request.url.params["step"] == "30"
        return httpx.Response(200, json=prometheus_success_response())

    async def scenario() -> None:
        provider = PrometheusMetricsProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        )
        response = await provider.query_metrics(
            service=make_record(metrics_config=make_metrics_config()),
            config=make_metrics_config(),
            query=StudioMetricsQuery(
                from_time="2024-04-10T21:00:00Z",
                to_time="2024-04-10T21:01:00Z",
                groups=[StudioMetricGroup.CPU_USAGE],
            ),
        )
        assert response.series[0].metric == "cpu_usage"
        assert response.series[0].unit == "cores"
        assert response.series[0].points[-1].value == 0.25

    asyncio.run(scenario())
    assert observed_queries == [
        'sum(rate(container_cpu_usage_seconds_total{container!="",image!="",namespace="prod",pod=~".+"}[5m])'
        ' * on(namespace, pod) group_left() kube_pod_labels{label_app="payments-api",namespace="prod"})'
    ]


def test_prometheus_provider_builds_owned_pod_joins_for_platform_queries() -> None:
    observed_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(request.url.params["query"])
        return httpx.Response(200, json=prometheus_success_response())

    platform_groups = [
        StudioMetricGroup.CPU_USAGE,
        StudioMetricGroup.MEMORY_USAGE,
        StudioMetricGroup.CPU_REQUESTS,
        StudioMetricGroup.CPU_LIMITS,
        StudioMetricGroup.MEMORY_REQUESTS,
        StudioMetricGroup.MEMORY_LIMITS,
        StudioMetricGroup.RESTARTS,
        StudioMetricGroup.OOM_KILLED,
        StudioMetricGroup.POD_PHASE,
        StudioMetricGroup.READINESS,
        StudioMetricGroup.NETWORK_RECEIVE,
        StudioMetricGroup.NETWORK_TRANSMIT,
    ]

    async def scenario() -> None:
        provider = PrometheusMetricsProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        )
        await provider.query_metrics(
            service=make_record(metrics_config=make_metrics_config()),
            config=make_metrics_config(),
            query=StudioMetricsQuery(
                from_time="2024-04-10T21:00:00Z",
                to_time="2024-04-10T21:01:00Z",
                groups=platform_groups,
            ),
        )

    asyncio.run(scenario())
    assert len(observed_queries) == len(platform_groups)
    assert all(
        ' * on(namespace, pod) group_left() kube_pod_labels{label_app="payments-api",namespace="prod"}' in query
        for query in observed_queries
    )
    assert observed_queries[8].startswith("sum by (phase) (")
    assert observed_queries[9].startswith("sum(kube_pod_container_status_ready{")
    assert 'condition="true"' in observed_queries[9]


def test_prometheus_provider_translates_service_selectors_to_kube_pod_labels() -> None:
    observed_queries: list[str] = []
    config = make_metrics_config(
        service_selector_labels={
            "app.kubernetes.io/name": "payments",
            "service": "payments-api",
            "team/service": "payments-platform",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(request.url.params["query"])
        return httpx.Response(200, json=prometheus_success_response())

    async def scenario() -> None:
        provider = PrometheusMetricsProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        )
        await provider.query_metrics(
            service=make_record(metrics_config=config),
            config=config,
            query=StudioMetricsQuery(
                from_time="2024-04-10T21:00:00Z",
                to_time="2024-04-10T21:01:00Z",
                groups=[StudioMetricGroup.CPU_USAGE],
            ),
        )

    asyncio.run(scenario())
    assert observed_queries == [
        'sum(rate(container_cpu_usage_seconds_total{container!="",image!="",namespace="prod",pod=~".+"}[5m])'
        " * on(namespace, pod) group_left() kube_pod_labels{"
        'label_app_kubernetes_io_name="payments",label_service="payments-api",'
        'label_team_service="payments-platform",namespace="prod"})'
    ]


def test_prometheus_provider_builds_relayna_runtime_queries() -> None:
    observed_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_queries.append(request.url.params["query"])
        return httpx.Response(200, json=prometheus_success_response())

    async def scenario() -> None:
        provider = PrometheusMetricsProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        )
        await provider.query_metrics(
            service=make_record(metrics_config=make_metrics_config(runtime_service_label_value="relayna")),
            config=make_metrics_config(runtime_service_label_value="relayna"),
            query=StudioMetricsQuery(
                from_time="2024-04-10T21:00:00Z",
                to_time="2024-04-10T21:01:00Z",
                groups=[StudioMetricGroup.TASKS_STARTED_RATE, StudioMetricGroup.TASK_DURATION_P95],
            ),
        )

    asyncio.run(scenario())
    assert observed_queries == [
        'sum(rate(relayna_tasks_started_total{service="relayna"}[5m]))',
        'histogram_quantile(0.95, sum by (le) (rate(relayna_task_duration_seconds_bucket{service="relayna"}[5m])))',
    ]


def test_service_metrics_route_returns_series(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        return httpx.Response(200, json=prometheus_success_response())

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(metrics_config=make_metrics_config()))
        response = client.get(
            "/studio/services/payments-api/metrics",
            params={"from": "2024-04-10T21:00:00Z", "to": "2024-04-10T21:01:00Z", "group": "cpu_usage"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["service_id"] == "payments-api"
    assert payload["approximate"] is False
    assert payload["series"][0]["metric"] == "cpu_usage"


def test_task_metrics_route_derives_window_and_marks_approximate(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query_range":
            observed_params["start"] = request.url.params["start"]
            observed_params["end"] = request.url.params["end"]
            return httpx.Response(200, json=prometheus_success_response())
        if request.url.path == "/status/task-123":
            return httpx.Response(
                200,
                json={"task_id": "task-123", "event": {"task_id": "task-123", "status": "completed"}},
            )
        if request.url.path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "events": [
                        {"task_id": "task-123", "status": "queued", "timestamp": "2024-04-10T21:00:00Z"},
                        {"task_id": "task-123", "status": "completed", "timestamp": "2024-04-10T21:05:00Z"},
                    ],
                },
            )
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "pipeline",
                    "summary": {"started_at": "2024-04-10T21:00:00Z", "ended_at": "2024-04-10T21:05:00Z"},
                    "nodes": [],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": [],
                },
            )
        return httpx.Response(200, json={"count": 0, "items": []})

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(metrics_config=make_metrics_config()))
        response = client.get("/studio/tasks/payments-api/task-123/metrics", params={"group": "cpu_usage"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task-123"
    assert payload["approximate"] is True
    assert "approximate for long-running workers" in payload["warnings"][0]
    assert float(observed_params["start"]) == 1712782680.0
    assert float(observed_params["end"]) == 1712783220.0


def test_task_metrics_route_ignores_invalid_summary_timestamps(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    observed_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query_range":
            observed_params["start"] = request.url.params["start"]
            observed_params["end"] = request.url.params["end"]
            return httpx.Response(200, json=prometheus_success_response())
        if request.url.path == "/status/task-123":
            return httpx.Response(
                200,
                json={"task_id": "task-123", "event": {"task_id": "task-123", "status": "completed"}},
            )
        if request.url.path == "/history":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "events": [
                        {"task_id": "task-123", "status": "queued", "timestamp": "2024-04-10T21:00:00Z"},
                        {"task_id": "task-123", "status": "completed", "timestamp": "2024-04-10T21:05:00Z"},
                    ],
                },
            )
        if request.url.path == "/executions/task-123/graph":
            return httpx.Response(
                200,
                json={
                    "task_id": "task-123",
                    "topology_kind": "pipeline",
                    "summary": {"started_at": "not-a-date", "ended_at": "still-not-a-date"},
                    "nodes": [],
                    "edges": [],
                    "annotations": {},
                    "related_task_ids": [],
                },
            )
        return httpx.Response(200, json={"count": 0, "items": []})

    app = create_studio_app(
        redis_url="redis://studio-test/0",
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        ),
    )

    with TestClient(app) as client:
        install_service(app, make_record(metrics_config=make_metrics_config()))
        response = client.get("/studio/tasks/payments-api/task-123/metrics", params={"group": "cpu_usage"})

    assert response.status_code == 200
    assert float(observed_params["start"]) == 1712782680.0
    assert float(observed_params["end"]) == 1712783220.0


def test_metrics_routes_return_expected_error_codes(monkeypatch) -> None:
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(redis_url="redis://studio-test/0", pull_sync_interval_seconds=None)

    with TestClient(app) as client:
        install_service(app, make_record(metrics_config=None))
        response = client.get("/studio/services/payments-api/metrics")
        invalid_range = client.get(
            "/studio/services/payments-api/metrics",
            params={"from": "2024-04-10T22:00:00Z", "to": "2024-04-10T21:00:00Z"},
        )

    assert response.status_code == 501
    assert invalid_range.status_code == 422
