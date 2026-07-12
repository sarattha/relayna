from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from relayna_studio import events, federation, logs, metrics, registry, search, traces


def _endpoint(router, name: str):
    return next(route.endpoint for route in router.routes if isinstance(route, APIRoute) and route.name == name)


def _assert_http_status(awaitable, status_code: int) -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(awaitable)
    assert caught.value.status_code == status_code


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (registry.ServiceNotFoundError("missing"), 404),
        (metrics.StudioMetricsProviderNotConfiguredError("missing"), 501),
        (metrics.StudioMetricsConfigError("bad"), 422),
        (metrics.StudioMetricsProviderError("offline"), 502),
    ],
)
def test_metrics_router_translates_every_service_error(error: Exception, status_code: int) -> None:
    service = AsyncMock()
    router = metrics.create_studio_metrics_router(metrics_query_service=service)
    for route_name, method_name, args in [
        ("service_metrics", "query_service_metrics", ("svc",)),
        ("service_pods", "query_service_pods", ("svc",)),
        ("task_metrics", "query_task_metrics", ("svc", "task")),
    ]:
        getattr(service, method_name).side_effect = error
        endpoint = _endpoint(router, route_name)
        if route_name == "service_pods":
            awaitable = endpoint(*args)
        else:
            awaitable = endpoint(
                *args,
                from_time=None,
                to_time=None,
                step=None,
                group=[],
                pod=None,
                split_by_pod=False,
            )
        _assert_http_status(awaitable, status_code)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (registry.ServiceNotFoundError("missing"), 404),
        (traces.StudioTraceConfigError("bad"), 422),
        (traces.StudioTraceProviderError("offline"), 502),
    ],
)
def test_trace_router_translates_every_error(error: Exception, status_code: int) -> None:
    service = AsyncMock()
    router = traces.create_studio_traces_router(trace_query_service=service)
    for route_name, method_name in [
        ("task_traces", "query_task_traces"),
        ("task_trace_path", "query_task_trace_path"),
    ]:
        getattr(service, method_name).side_effect = error
        _assert_http_status(_endpoint(router, route_name)("svc", "task"), status_code)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (registry.ServiceNotFoundError("missing"), 404),
        (logs.StudioLogProviderNotConfiguredError("missing"), 501),
        (logs.StudioLogConfigError("bad"), 422),
        (logs.StudioLogProviderError("offline"), 502),
    ],
)
def test_log_router_translates_every_error(error: Exception, status_code: int) -> None:
    service = AsyncMock()
    router = logs.create_studio_logs_router(log_query_service=service)
    kwargs = {
        "correlation_id": None,
        "level": None,
        "source": None,
        "pod": None,
        "query": None,
        "before": None,
        "from_time": None,
        "to_time": None,
        "limit": 100,
    }
    service.query_service_logs.side_effect = error
    _assert_http_status(_endpoint(router, "service_logs")("svc", task_id=None, **kwargs), status_code)
    service.query_task_logs.side_effect = error
    _assert_http_status(_endpoint(router, "task_logs")("svc", "task", **kwargs), status_code)


def test_search_router_translates_invalid_queries() -> None:
    service = AsyncMock()
    service.search_tasks.side_effect = ValueError("bad cursor")
    service.search_services.side_effect = ValueError("bad cursor")
    router = search.create_studio_search_router(search_service=service)
    _assert_http_status(
        _endpoint(router, "search_tasks")(
            service_id=None,
            task_id=None,
            correlation_id=None,
            status=None,
            stage=None,
            from_timestamp=None,
            to_timestamp=None,
            limit=50,
            cursor="bad",
        ),
        422,
    )
    _assert_http_status(
        _endpoint(router, "search_services")(
            query=None,
            environment=None,
            status=None,
            health=None,
            tag=None,
            limit=50,
            cursor="bad",
        ),
        422,
    )


def test_registry_router_translates_all_mutation_errors() -> None:
    service = AsyncMock()
    router = registry.create_service_registry_router(service_registry=service)
    request = registry.CreateServiceRequest(
        service_id="svc",
        name="Service",
        base_url="https://service.example.test",
        environment="test",
        tags=[],
        auth_mode="internal_network",
    )
    update = registry.UpdateServiceRequest(name="Updated")

    service.create_service.side_effect = registry.DuplicateServiceError("duplicate")
    _assert_http_status(_endpoint(router, "create_service")(request), 409)
    service.create_service.side_effect = ValueError("bad")
    _assert_http_status(_endpoint(router, "create_service")(request), 422)

    service.get_service.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(_endpoint(router, "get_service")("svc"), 404)
    for error, status_code in [
        (registry.ServiceNotFoundError("missing"), 404),
        (registry.DuplicateServiceError("duplicate"), 409),
        (ValueError("bad"), 422),
    ]:
        service.update_service.side_effect = error
        _assert_http_status(_endpoint(router, "update_service")("svc", update), status_code)
    service.delete_service.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(_endpoint(router, "delete_service")("svc"), 404)
    service.refresh_service.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(_endpoint(router, "refresh_service")("svc"), 404)
    service.refresh_service.side_effect = registry.CapabilityRefreshError("offline")
    _assert_http_status(_endpoint(router, "refresh_service")("svc"), 502)


def test_event_router_translates_validation_and_missing_services() -> None:
    service = AsyncMock()
    service.registry_service = AsyncMock()
    stream = AsyncMock()
    router = events.create_studio_events_router(ingest_service=service, event_stream=stream)
    service.list_service_events.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(
        _endpoint(router, "service_events")(
            "svc",
            task_id=None,
            source_kind=None,
            event_type=None,
            from_time=None,
            to_time=None,
            before=None,
            limit=100,
        ),
        404,
    )
    _assert_http_status(
        _endpoint(router, "service_events")(
            "svc",
            task_id=None,
            source_kind=None,
            event_type=None,
            from_time="bad",
            to_time=None,
            before=None,
            limit=100,
        ),
        422,
    )
    _assert_http_status(
        _endpoint(router, "service_events")(
            "svc",
            task_id=None,
            source_kind=None,
            event_type=None,
            from_time="2026-01-02T00:00:00Z",
            to_time="2026-01-01T00:00:00Z",
            before=None,
            limit=100,
        ),
        422,
    )
    service.list_task_events.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(_endpoint(router, "task_events")("svc", "task", before=None, limit=100), 404)
    service.registry_service.get_service.side_effect = registry.ServiceNotFoundError("missing")
    _assert_http_status(_endpoint(router, "service_events_stream")("svc"), 404)
    _assert_http_status(_endpoint(router, "task_events_stream")("svc", "task"), 404)


def test_federation_router_translates_every_service_failure() -> None:
    service = AsyncMock()
    error = federation.StudioFederationError(
        status_code=502,
        detail="upstream offline",
        code="upstream_error",
    )
    router = federation.create_federation_router(federation_service=service)
    calls = [
        ("service_status", "get_service_status", ("svc", "task"), {}),
        (
            "service_history",
            "get_service_history",
            ("svc",),
            {"task_id": None, "start_offset": "first", "max_seconds": None, "max_scan": None},
        ),
        ("service_workflow_topology", "get_service_workflow_topology", ("svc",), {}),
        (
            "service_dlq_messages",
            "get_service_dlq_messages",
            ("svc",),
            {
                "queue_name": None,
                "task_id": None,
                "reason": None,
                "source_queue_name": None,
                "state": None,
                "cursor": None,
                "limit": 50,
            },
        ),
        (
            "service_broker_dlq_messages",
            "get_service_broker_dlq_messages",
            ("svc",),
            {"queue_name": None, "task_id": None, "limit": 50},
        ),
        (
            "failed_tasks",
            "list_failed_tasks",
            (),
            {
                "service_id": None,
                "service_name": None,
                "queue_name": None,
                "dlq_name": None,
                "error_type": None,
                "status": None,
                "task_id": None,
                "worker_id": None,
                "investigation_status": None,
                "failed_from": None,
                "failed_to": None,
                "cursor": None,
                "limit": 50,
            },
        ),
        ("failed_task_detail", "get_failed_task_detail", ("svc", "failure"), {}),
        (
            "mark_failed_task_investigated",
            "mark_failed_task_investigated",
            ("svc", "failure"),
            {"payload": {}},
        ),
        ("mark_failed_task_uninvestigated", "mark_failed_task_uninvestigated", ("svc", "failure"), {}),
        ("retry_failed_task", "retry_failed_task", ("svc", "failure"), {"payload": {}}),
        ("delete_failed_task", "delete_failed_task", ("svc", "failure"), {}),
        ("service_execution_graph", "get_service_execution_graph", ("svc", "task"), {}),
        ("service_runtime_backpressure", "get_service_runtime_backpressure", ("svc",), {}),
        ("task_detail", "get_task_detail", ("svc", "task"), {"join": federation.JoinMode.NONE}),
    ]
    for route_name, method_name, args, kwargs in calls:
        getattr(service, method_name).side_effect = error
        response = asyncio.run(_endpoint(router, route_name)(*args, **kwargs))
        assert response.status_code == 502
