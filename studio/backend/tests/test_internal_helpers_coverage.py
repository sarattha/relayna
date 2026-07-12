from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from relayna_studio import (
    events,
    failed_task_notifications,
    federation,
    health,
    logs,
    metrics,
    registry,
    search,
    traces,
)
from relayna_studio.federation import StudioJoinWarning
from test_studio_events import FakeRedis, make_capability_document
from test_studio_events import make_record as make_event_record
from test_studio_logs import TrackingAsyncClient
from test_studio_metrics import make_record
from test_studio_traces import make_record as make_trace_record
from test_studio_traces import tempo_trace_config

from relayna.observability import RelaynaServiceEvent, ServiceEventSourceKind, StudioEventIngestMethod


def test_federation_cursor_string_collection_and_payload_helpers() -> None:
    cursor = federation._encode_failed_task_cursor(12)
    assert federation._decode_failed_task_cursor(cursor) == 12
    for value in ("", "not-base64", federation._encode_failed_task_cursor(-1)):
        with pytest.raises(ValueError):
            federation._decode_failed_task_cursor(value)

    assert federation._normalize_string(" value ") == "value"
    assert federation._normalize_string(" ") is None
    assert federation._normalize_string(3) == "3"
    assert federation._first_distinct_string([None, "", "a", "b"], exclude="a") == "b"
    assert federation._first_distinct_string([], exclude=None) is None
    assert [item.task_id for item in federation._pointers_for_task_ids("svc", ["a", "a", None, " b "])] == ["a", "b"]
    assert federation._task_ref_from_payload(None) is None
    assert federation._task_ref_from_payload({}) is None
    task_ref = federation._task_ref_from_payload(
        {
            "task_ref": {
                "service_id": "svc",
                "task_id": "task",
                "correlation_id": "corr",
                "parent_refs": [{"service_id": "up", "task_id": "parent"}, "bad"],
                "child_refs": [{"service_id": "down", "task_id": "child"}],
            }
        }
    )
    assert task_ref is None
    task_ref = federation._task_ref_from_payload(
        {"task_ref": {"service_id": "svc", "task_id": "task", "correlation_id": "corr"}}
    )
    assert task_ref is not None and task_ref.correlation_id == "corr"
    assert federation._payload_task_id({"task_ref": {"task_id": "nested"}}) is None
    assert federation._payload_task_id({"task_id": "direct"}) == "direct"
    assert federation._payload_task_id(None) is None
    assert federation._history_events(None) == []
    assert federation._history_events({"events": [None, {"task_id": "one"}, "bad"]}) == [{"task_id": "one"}]
    assert federation._graph_related_task_ids(None) == []
    assert federation._graph_related_task_ids({"related_task_ids": ["a", "a", None]}) == ["a"]
    assert federation._workflow_lineage_values(None) == []
    assert federation._workflow_lineage_values(
        {
            "nodes": [
                {"kind": "workflow_message", "annotations": {"correlation_id": "wf"}},
                {"kind": "task", "annotations": {"correlation_id": "ignored"}},
            ]
        }
    ) == ["wf"]
    warning = StudioJoinWarning(code="partial", detail="partial", join_kind="correlation_id", matched_value="corr")
    assert federation._dedupe_join_warnings([warning, warning]) == [warning]
    with pytest.raises(IndexError):
        federation._latest_history_event([])
    with pytest.raises(federation.StudioFederationError):
        federation._latest_history_event(["bad"])
    assert (
        federation._latest_history_event(
            [None, {"timestamp": "2026-01-01T00:00:00Z"}, {"timestamp": "2026-01-02T00:00:00Z"}]
        )["timestamp"]
        == "2026-01-02T00:00:00Z"
    )
    assert federation._quote_path_segment("task/id") == "task%2Fid"


def test_trace_scalar_collection_timestamp_and_state_helpers() -> None:
    output: set[str] = set()
    encoded_trace = '{"trace_id":"' + "b" * 32 + '"}'
    traces._collect_trace_ids({"trace_id": "a" * 32, "nested": [encoded_trace]}, output)
    assert output == {"a" * 32, "b" * 32}
    assert traces._is_trace_id("a" * 32)
    assert not traces._is_trace_id("A" * 32)
    assert not traces._is_trace_id("xyz")
    assert traces._browser_safe_base_url("https://tempo.example.test/path/") == "https://tempo.example.test/path/"
    assert traces._browser_safe_base_url("http://host.docker.internal:3200") == "http://localhost:3200"
    assert traces._attributes(None) == {}
    assert traces._attributes(
        [{"key": "text", "value": {"stringValue": "value"}}, {"key": "count", "value": {"intValue": "3"}}, "bad"]
    ) == {"text": "value", "count": "3"}
    assert traces._attribute_value({"boolValue": True}) is True
    assert traces._attribute_value({"doubleValue": "1.5"}) == "1.5"
    assert traces._attribute_value({"arrayValue": {"values": [{"stringValue": "a"}]}}) == {
        "values": [{"stringValue": "a"}]
    }
    assert traces._attribute_value({"kvlistValue": {"values": [{"key": "a", "value": {"stringValue": "b"}}]}}) == {
        "values": [{"key": "a", "value": {"stringValue": "b"}}]
    }
    assert traces._attribute_value({"unknown": "value"}) == {"unknown": "value"}
    assert traces._tempo_optional_id_to_hex(None, byte_length=8) is None
    encoded_id = base64.b64encode(b"\x01" * 8).decode()
    assert traces._tempo_id_to_hex(encoded_id, byte_length=8) == "01" * 8
    assert traces._tempo_id_to_hex("01" * 8, byte_length=8) == "01" * 8
    assert traces._tempo_id_to_hex(1, byte_length=8) == "1"
    assert traces._tempo_id_to_hex("not-hex", byte_length=8) == "not-hex"
    assert traces._ns_to_iso(None) is None
    assert traces._ns_to_iso(0) == "1970-01-01T00:00:00Z"
    assert traces._int_or_none(True) == 1
    assert traces._int_or_none("3") == 3
    assert traces._int_or_none("bad") is None
    assert traces._float_or_none("1.5") == 1.5
    assert traces._float_or_none("bad") is None
    assert traces._string(" value ") == "value"
    assert traces._string(3) == "3"
    assert traces._first_string(None, "", " value ") == "value"
    assert traces._mapping_or_empty(None) == {}
    assert traces._mapping_or_empty({"a": 1}) == {"a": 1}
    assert traces._json_object({"a": 1}) == {"a": 1}
    assert traces._json_object("bad") == {}
    assert traces._int_counts({"ok": 2, "bool": True, "bad": "x"}) == {"ok": 2, "bool": 1}
    assert traces._list_of_mappings([{"a": 1}, "bad"]) == [{"a": 1}]
    assert traces._list_of_mappings("bad") == []
    assert traces._dedupe_strings(["a", "", "a", "b"]) == ["a", "", "b"]
    assert traces._timestamp_sort_key(None) == ""
    assert traces._earliest_timestamp(None, "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    assert traces._latest_timestamp(None, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z") == "2026-01-02T00:00:00Z"
    assert traces._duration_ms(None, None) is None
    assert traces._duration_ms("bad", "2026-01-01T00:00:00Z") is None
    assert traces._duration_ms("2026-01-01T00:00:01Z", "2026-01-01T00:00:00Z") is None
    assert traces._parse_iso_timestamp(None) is None
    assert traces._parse_iso_timestamp("bad") is None
    assert traces._state_from_status("completed") == "succeeded"
    assert traces._state_from_status("retrying") == "retrying"
    assert traces._state_from_status("DLQ") == "dead_lettered"
    assert traces._state_from_status("custom") == "unknown"


def test_trace_path_builders_cover_graph_history_events_dlq_and_spans() -> None:
    graph_detail = {
        "task_ref": {"correlation_id": "corr"},
        "execution_graph": {
            "nodes": [
                {
                    "id": "attempt",
                    "kind": "task_attempt",
                    "label": "attempt",
                    "task_id": "task",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:03Z",
                    "annotations": {"queue_name": "jobs", "stage": "run", "retry_attempt": 1},
                },
                {
                    "id": "status",
                    "kind": "status_event",
                    "label": "completed",
                    "task_id": "task",
                    "timestamp": "2026-01-01T00:00:04Z",
                },
                {
                    "id": "dlq",
                    "kind": "dlq_record",
                    "label": "DLQ",
                    "task_id": "task",
                    "annotations": {"queue_name": "dead"},
                },
            ],
            "edges": [
                {"source": "attempt", "target": "status", "kind": "completed"},
                {"source": None, "target": "status"},
            ],
            "summary": {"live_state_counts": {"completed": 1}},
        },
        "dlq_messages": {
            "items": [
                {
                    "dlq_id": "failure",
                    "task_id": "task",
                    "queue_name": "dead",
                    "reason": "exhausted",
                    "dead_lettered_at": "2026-01-01T00:00:05Z",
                }
            ]
        },
    }
    spans = [
        traces.StudioTraceSpan(
            trace_id="a" * 32,
            span_id="parent",
            name="consumer run",
            kind="SPAN_KIND_CONSUMER",
            start_time="2026-01-01T00:00:01Z",
            end_time="2026-01-01T00:00:02Z",
            attributes={"task_id": "task", "stage": "run", "queue_name": "jobs"},
        ),
        traces.StudioTraceSpan(
            trace_id="a" * 32,
            span_id="child",
            parent_span_id="parent",
            name="unmatched child",
            start_time="2026-01-01T00:00:02Z",
            end_time="2026-01-01T00:00:03Z",
            attributes={"task_id": "other"},
        ),
    ]
    response = traces._build_trace_path_response(
        service=make_record(),
        task_id="task",
        detail_payload=graph_detail,
        trace_response=traces.StudioTraceResponse(
            service_id="payments-api", task_id="task", trace_ids=["a" * 32], spans=spans
        ),
        event_items=[
            {
                "dedupe_key": "event-status",
                "event_type": "status.completed",
                "task_id": "task",
                "timestamp": "2026-01-01T00:00:04Z",
                "payload": {"trace_id": "a" * 32, "span_id": "child"},
            },
            {
                "event_id": "event-stage",
                "event_type": "task.running",
                "task_id": "task",
                "payload": {"stage": "run", "queue_name": "jobs"},
            },
            {"event_type": "dlq.received", "task_id": "task", "payload": {"queue_name": "dead"}},
        ],
        warnings=["same", "same"],
    )
    assert response.summary.node_count >= 4
    assert response.summary.span_count == 2
    assert response.summary.dlq_count == 1
    assert response.warnings == ["same"]
    assert any(edge.kind == "span_child" for edge in response.edges)

    history_response = traces._build_trace_path_response(
        service=make_record(),
        task_id="task",
        detail_payload={
            "latest_status": {"status": "queued"},
            "history": {
                "events": [
                    {"event_id": "one", "status": "queued", "timestamp": "2026-01-01T00:00:00Z"},
                    {"event_id": "two", "status": "failed", "timestamp": "2026-01-01T00:00:01Z"},
                ]
            },
            "dlq_messages": {
                "items": [
                    {
                        "task_id": "task",
                        "queue_name": "dead",
                        "reason": "failure",
                        "dead_lettered_at": "2026-01-01T00:00:02Z",
                    }
                ]
            },
        },
        trace_response=traces.StudioTraceResponse(service_id="payments-api", task_id="task"),
        event_items=[],
        warnings=[],
    )
    assert history_response.summary.status == "failed"
    assert any(node.kind == "dlq_record" for node in history_response.nodes)
    assert any(edge.kind == "dead_lettered_to" for edge in history_response.edges)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (" value ", "value"),
    ],
)
def test_metrics_optional_string(value: object, expected: str | None) -> None:
    assert metrics._normalize_optional_string(value) == expected


def test_metrics_timestamp_promql_and_unit_helpers() -> None:
    assert metrics._parse_iso_timestamp("2026-01-01T00:00:00Z").tzinfo is not None
    assert metrics._iso(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00Z"
    assert metrics._escape_promql_string('a\\b"c\n') == 'a\\\\b\\"c\n'
    assert metrics._kube_pod_label_metric_name("app.kubernetes.io/name") == "label_app_kubernetes_io_name"
    candidates = metrics._kube_pod_label_metric_name_candidates("app")
    assert candidates[0] == "label_app"
    assert candidates[-1] == "label_app_conflict9"
    assert metrics._timestamp_from_value(None) is None
    assert metrics._timestamp_from_value(datetime(2026, 1, 1)) is None
    assert metrics._timestamp_from_value("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    assert metrics._timestamp_from_value("bad") is None
    assert metrics._timestamp_from_record({"timestamp": "bad", "created_at": "2026-01-01T00:00:00Z"}) == datetime(
        2026, 1, 1, tzinfo=UTC
    )
    assert metrics._timestamp_from_record({}) is None
    for group in metrics.StudioMetricGroup:
        assert metrics._metric_unit(group)


def test_event_search_log_and_notification_scalar_helpers() -> None:
    assert events._normalize_string(" value ") == "value"
    assert events._normalize_string(3) == "3"
    assert events._parse_timestamp(None) is None
    assert events._parse_timestamp("bad") is None
    assert events._parse_timestamp("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    assert events._timestamp_key(None)[0] == 0
    assert events._timestamp_key("bad")[0] == 0
    assert events._timestamp_key("2026-01-01T00:00:00Z")[0] == 1
    assert events._decode_optional(None) is None
    assert events._decode_optional(b"value") == "value"
    assert events._decode_optional("value") == "value"
    with pytest.raises(ValueError, match="timestamp"):
        events._parse_required_timestamp("bad", field_name="timestamp")

    assert search._normalize_text(" Hello  WORLD ") == "hello  world"
    assert search._tokenize("hello hello world") == ["hello", "hello", "world"]
    assert search._build_prefix_tokens("ab cd") == {"a", "ab", "c", "cd"}
    assert search._parse_datetime(None) is None
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert search._parse_datetime(aware) == aware
    assert search._later_iso(None, "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    assert search._earlier_iso("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    cursor = search._encode_cursor({"id": "one"})
    assert search._decode_cursor(cursor) == {"id": "one"}
    with pytest.raises(ValueError):
        search._decode_cursor("bad")
    assert search._decode_members({b"one", "two"}) == {"one", "two"}
    assert search._normalize_optional_string(" ") is None
    assert search._json_log_fields('{"details": {"task_id": "one"}}') == {"details": {"task_id": "one"}}
    assert search._json_log_fields("[]") == {}
    assert search._message_field({"task_id": "one"}, "task_id") == "one"
    assert search._message_field({"details": {"task_id": "two"}}, "task_id") is None
    assert search._message_field({}, "task_id") is None

    assert logs._parse_cursor_timestamp("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        logs._parse_cursor_timestamp("bad")
    assert logs._logql_matcher_operator("regex") == "=~"
    assert logs._logql_matcher_operator("exact") == "="
    page = logs._encode_page_cursor(cursor="123", skip_count=2)
    assert logs._parse_page_cursor(page) == ("123", 2)
    with pytest.raises(ValueError):
        logs._parse_page_cursor("bad")

    assert failed_task_notifications._string_field({"value": " text "}, "value") == "text"
    assert failed_task_notifications._string_field({"value": 3}, "value") == "3"
    assert failed_task_notifications._string_payload_field({"value": "text"}, "value") == "text"
    assert failed_task_notifications._item_key({"service_id": "svc", "failure_id": "fail"}) == "svc:fail"
    assert failed_task_notifications._normalize_batch_wait_seconds(None) == 0
    assert failed_task_notifications._normalize_batch_wait_seconds("bad") == 0
    assert failed_task_notifications._normalize_batch_wait_seconds(-1) == 0
    assert failed_task_notifications._normalize_batch_wait_seconds(999999999) == 604800
    assert failed_task_notifications._parse_datetime("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_event_store_stream_paging_and_capability_edge_cases() -> None:
    redis = FakeRedis()
    store = events.RedisStudioEventStore(redis, prefix="coverage", ttl_seconds=60, history_maxlen=5)
    redis.lists[store.service_history_key("svc")] = ["missing", "invalid"]
    redis.values[store.event_key("invalid")] = "not-json"
    assert await store._load_history(store.service_history_key("svc")) == []

    relayna_event = RelaynaServiceEvent(
        cursor="cursor",
        task_id="task",
        event_type="status.completed",
        source_kind=ServiceEventSourceKind.STATUS,
        component="status",
        timestamp="2026-01-01T00:00:00Z",
        payload={"status": "completed"},
    )
    key = events._event_dedupe_key("svc", relayna_event)
    assert key.startswith("svc:status:cursor:")
    envelope = events.StudioEventEnvelope(
        service_id="svc",
        ingest_method=StudioEventIngestMethod.PUSH,
        event=relayna_event,
    )
    assert await store.insert_event(envelope)
    items = (await store.list_task_events("svc", "task")).items
    paged = events._page_events(items + items, before=items[0].dedupe_key, limit=1)
    assert paged.count == 1

    assert not events._supports_events_feed(make_event_record(service_id="svc", capabilities=None))
    assert not events._supports_events_feed(make_event_record(service_id="svc", capabilities={"bad": True}))
    incompatible = make_capability_document(supported_routes=["events.feed"])
    incompatible["service_metadata"]["compatibility"] = "legacy"  # type: ignore[index]
    assert not events._supports_events_feed(make_event_record(service_id="svc", capabilities=incompatible))
    assert not events._supports_events_feed(
        make_event_record(service_id="svc", capabilities=make_capability_document(supported_routes=[]))
    )

    stream = events.StudioEventStream(event_store=store, keepalive_interval_seconds=0.001)
    pubsub = AsyncMock()
    pubsub.get_message.return_value = {"type": "subscribe"}

    async def empty_iterator():
        await asyncio.Event().wait()
        yield {}

    assert await stream._next_message(pubsub, empty_iterator()) == {"type": "subscribe"}
    del pubsub.get_message
    assert await stream._next_message(pubsub, empty_iterator()) is None


@pytest.mark.asyncio
async def test_event_ingest_counts_missing_services_and_sync_skips_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_service = AsyncMock()
    registry_service.get_service.side_effect = events.ServiceNotFoundError("missing")
    store = AsyncMock()
    ingest = events.StudioEventIngestService(
        registry_service=registry_service,
        event_store=store,
        http_client=AsyncMock(),
    )
    envelope = events.StudioEventEnvelope(
        service_id="missing",
        ingest_method=StudioEventIngestMethod.PUSH,
        event=RelaynaServiceEvent(
            cursor="cursor",
            task_id="task",
            event_type="status.completed",
            source_kind=ServiceEventSourceKind.STATUS,
            component="status",
            timestamp="2026-01-01T00:00:00Z",
            payload={},
        ),
    )
    response = await ingest.ingest_events([envelope])
    assert response.invalid == 1

    healthy = make_event_record(
        service_id="healthy",
        capabilities=make_capability_document(supported_routes=["events.feed"]),
    )
    registry_service.list_services.return_value = [
        make_event_record(service_id="disabled", status=events.ServiceStatus.DISABLED),
        make_event_record(service_id="unsupported", capabilities=None),
        healthy,
    ]
    sync_service = AsyncMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(events.StudioEventIngestService, "sync_service", sync_service)
    await ingest.sync_registered_services()
    sync_service.assert_awaited_once_with(healthy)


@pytest.mark.parametrize(
    "payload",
    [
        {"base_url": "https://loki.example.test", "service_selector_labels": None},
        {"base_url": "https://loki.example.test", "service_selector_labels": []},
        {"base_url": "https://loki.example.test", "service_selector_labels": {"": "value"}},
        {"base_url": "https://loki.example.test", "service_selector_labels": {}},
        {"base_url": "https://loki.example.test", "service_selector_labels": {"app": "svc"}, "pod_label": 3},
        {"base_url": "https://loki.example.test", "service_selector_labels": {"app": "svc"}, "pod_label": ""},
    ],
)
def test_registry_log_config_rejects_every_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        registry.LokiLogConfig.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"base_url": "https://prom.example.test", "namespace": 3, "service_selector_labels": {"app": "svc"}},
        {"base_url": "https://prom.example.test", "namespace": "", "service_selector_labels": {"app": "svc"}},
        {"base_url": "https://prom.example.test", "namespace": "task_id", "service_selector_labels": {"app": "svc"}},
        {"base_url": "https://prom.example.test", "namespace": "prod", "service_selector_labels": None},
        {"base_url": "https://prom.example.test", "namespace": "prod", "service_selector_labels": []},
        {"base_url": "https://prom.example.test", "namespace": "prod", "service_selector_labels": {"": "svc"}},
    ],
)
def test_registry_metrics_config_rejects_every_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        metrics.PrometheusMetricsConfig.model_validate(payload)


def test_registry_record_trace_url_gateway_and_policy_edge_cases() -> None:
    with pytest.raises(ValueError):
        registry._normalize_optional_string(3)
    for payload in [
        {"service_id": 3},
        {"service_id": ""},
        {"base_url": 3},
        {"tags": "bad"},
        {"tags": [3]},
    ]:
        base = {
            "service_id": "svc",
            "name": "Service",
            "base_url": "https://service.example.test",
            "environment": "test",
            "tags": [],
            "auth_mode": "internal_network",
        }
        base.update(payload)
        with pytest.raises(ValidationError):
            registry.ServiceRecord.model_validate(base)
    with pytest.raises(ValidationError):
        registry.UpdateServiceRequest()
    assert registry.UpdateServiceRequest(name=None).name is None
    assert registry.UpdateServiceRequest(base_url=None).base_url is None
    assert registry.UpdateServiceRequest(tags=None).tags is None

    for value in ["", "ftp://example.test", "https:///path", "https://example.test?a=1", "https://u:p@example.test"]:
        with pytest.raises(ValueError):
            registry.normalize_base_url(value)
    assert registry.normalize_base_url("HTTP://[::1]:8080/path/") == "http://[::1]:8080/path"

    with pytest.raises(ValidationError):
        registry.TempoTraceConfig(base_url="https://tempo.example.test", query_path=3)
    with pytest.raises(ValidationError):
        registry.TempoTraceConfig(base_url="https://tempo.example.test", query_path="/trace")
    trace = registry.TempoTraceConfig(
        base_url="https://tempo.example.test",
        public_base_url=" ",
        query_path="api/{trace_id}",
    )
    assert trace.public_base_url is None
    assert trace.query_path == "/api/{trace_id}"

    records = [
        make_event_record(service_id="A B"),
        make_event_record(service_id="a-b"),
        make_event_record(service_id="!!!"),
    ]
    records[0].health = {"overall_status": " degraded "}
    exports = registry.gateway_service_exports_from_records(records)
    assert len({item.name for item in exports}) == 3
    assert exports[0].status == "degraded"
    assert registry.gateway_service_export_from_record(records[2]).name.startswith("service-")

    with pytest.raises(ValueError):
        registry.StudioOutboundUrlPolicy(allowed_hosts=[""])
    policy = registry.StudioOutboundUrlPolicy(allowed_hosts=["*.example.test", "exact.test"])
    policy.validate_url("https://sub.example.test")
    policy.validate_url("https://exact.test")
    with pytest.raises(registry.OutboundUrlPolicyError):
        policy.validate_url("https://blocked.test")


def test_search_time_range_cursor_pagination_and_matching_edges() -> None:
    assert search._normalize_text(None) == ""
    assert search._tokenize(None) == []
    assert search._parse_datetime("") is None
    assert search._parse_datetime("bad") is None
    assert search._isoformat(None) is None
    assert search._later_iso("2026-01-01T00:00:00Z", None) == "2026-01-01T00:00:00Z"
    assert search._earlier_iso("2026-01-01T00:00:00Z", None) == "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        search._decode_cursor(base64.urlsafe_b64encode(b"[]").decode())
    with pytest.raises(ValueError):
        search._decode_cursor(base64.urlsafe_b64encode(b"\xff").decode())

    task_items = [
        search.StudioTaskSearchDocument(
            service_id="svc",
            service_name="Service",
            environment="test",
            task_id=f"task-{index}",
            last_seen_at=f"2026-01-0{index}T00:00:00Z",
            detail_path=f"/tasks/{index}",
        )
        for index in range(1, 4)
    ]
    first_page, cursor = search._paginate_task_documents(task_items, limit=1, cursor=None)
    assert len(first_page) == 1 and cursor is not None
    second_page, _ = search._paginate_task_documents(task_items, limit=1, cursor=cursor)
    assert second_page[0].task_id == "task-2"

    service_items = [
        search.StudioServiceSearchItem(
            service_id=f"svc-{index}",
            name=f"Service {index}",
            environment="test",
            status="healthy",
            base_url=f"https://svc-{index}.example.test",
            auth_mode="internal_network",
        )
        for index in range(1, 4)
    ]
    first_services, service_cursor = search._paginate_service_documents(service_items, limit=1, cursor=None)
    assert len(first_services) == 1 and service_cursor is not None
    second_services, _ = search._paginate_service_documents(service_items, limit=1, cursor=service_cursor)
    assert second_services[0].service_id == "svc-2"

    assert not search._is_not_earlier(None, "2026-01-01T00:00:00Z")
    assert search._is_not_earlier("2026-01-01T00:00:00Z", None)
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert not search._within_range(None, from_dt=aware, to_dt=None)
    assert not search._within_range(aware, from_dt=aware.replace(year=2027), to_dt=None)
    assert not search._within_range(aware, from_dt=None, to_dt=aware.replace(year=2025))
    assert search._within_range(aware, from_dt=aware, to_dt=aware)


@pytest.mark.asyncio
async def test_tempo_provider_and_trace_query_failure_edges() -> None:
    record = make_trace_record(trace_config=tempo_trace_config())
    invalid_provider = traces.TempoTraceProvider(http_client=AsyncMock())
    with pytest.raises(traces.StudioTraceConfigError):
        await invalid_provider.query_trace(service=record, config=tempo_trace_config(), trace_id="bad")

    invalid_payloads: list[object] = [[], {"batches": {"bad": True}}, {"batches": ["bad"]}]
    for payload in invalid_payloads:
        if payload == {"batches": ["bad"]}:
            assert invalid_provider._normalize_response(payload=payload, backend_url="https://tempo") == []
        else:
            with pytest.raises(traces.StudioTraceProviderError):
                invalid_provider._normalize_response(payload=payload, backend_url="https://tempo")
    assert (
        invalid_provider._normalize_response(
            payload={"batches": [{"scopeSpans": "bad"}, {"scopeSpans": ["bad", {"spans": "bad"}]}]},
            backend_url="https://tempo",
        )
        == []
    )

    for response_kind in ("http_error", "not_found", "status", "json"):

        def handler(request: httpx.Request, kind: str = response_kind) -> httpx.Response:
            if kind == "http_error":
                raise httpx.ConnectError("offline", request=request)
            if kind == "not_found":
                return httpx.Response(404)
            if kind == "status":
                return httpx.Response(503)
            return httpx.Response(200, text="not-json")

        provider = traces.TempoTraceProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=1.0)
        )
        if response_kind == "not_found":
            assert (
                await provider.query_trace(
                    service=record,
                    config=tempo_trace_config(),
                    trace_id="a" * 32,
                )
                == []
            )
        else:
            with pytest.raises(traces.StudioTraceProviderError):
                await provider.query_trace(service=record, config=tempo_trace_config(), trace_id="a" * 32)

    registry_service = AsyncMock()
    registry_service.get_service.return_value = record
    query_service = traces.StudioTraceQueryService(registry_service=registry_service, providers={})
    empty = await query_service._query_task_traces_for_service(
        service=record,
        task_id="task",
        detail_payload={},
    )
    assert "No trace identifiers" in empty.warnings[-1]
    with pytest.raises(traces.StudioTraceConfigError):
        await query_service._query_task_traces_for_service(
            service=record,
            task_id="task",
            detail_payload={"trace_id": "a" * 32},
        )

    no_integrations = traces.StudioTraceQueryService(registry_service=registry_service, providers={})
    path = await no_integrations.query_task_trace_path("payments-api", " task ")
    assert "No federation service" in path.warnings[0]
    assert any("No Studio event store" in warning for warning in path.warnings)


@pytest.mark.asyncio
async def test_federation_transport_service_and_failed_task_edges() -> None:
    service_record = make_event_record(service_id="svc")
    registry_service = AsyncMock()
    registry_service.get_service.return_value = service_record

    for response_kind in ("transport", "server", "client", "empty", "json", "shape"):

        def handler(request: httpx.Request, kind: str = response_kind) -> httpx.Response:
            if kind == "transport":
                raise httpx.ConnectError("offline", request=request)
            if kind == "server":
                return httpx.Response(503)
            if kind == "client":
                return httpx.Response(422, json={"detail": "invalid"})
            if kind == "empty":
                return httpx.Response(204)
            if kind == "json":
                return httpx.Response(200, text="bad-json")
            return httpx.Response(200, json=[])

        service = federation.StudioFederationService(
            registry_service=registry_service,
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=1.0),
        )
        if response_kind == "empty":
            assert await service._request_json(
                service_record,
                capability_id="status.latest",
                path="/status",
            ) == {"service_id": "svc"}
        else:
            with pytest.raises(federation.StudioFederationError):
                await service._request_json(service_record, capability_id="status.latest", path="/status")

    registry_service.get_service.side_effect = registry.ServiceNotFoundError("missing")
    service = federation.StudioFederationService(registry_service=registry_service, http_client=AsyncMock())
    with pytest.raises(federation.StudioFederationError, match="missing"):
        await service._get_proxyable_service("missing")
    registry_service.get_service.side_effect = None
    registry_service.get_service.return_value = make_event_record(
        service_id="disabled", status=registry.ServiceStatus.DISABLED
    )
    with pytest.raises(federation.StudioFederationError, match="disabled"):
        await service._get_proxyable_service("disabled")
    unsupported_auth = make_event_record(service_id="auth")
    unsupported_auth.auth_mode = "oauth"
    registry_service.get_service.return_value = unsupported_auth
    with pytest.raises(federation.StudioFederationError, match="auth_mode"):
        await service._get_proxyable_service("auth")

    success_client = TrackingAsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "items": [{"failure_id": "one", "failed_at": "2026-01-01T00:00:00Z"}],
                    "next_cursor": "next",
                },
            )
        ),
        timeout=1.0,
    )
    registry_service.get_service.return_value = service_record
    failed_tasks_service = federation.StudioFederationService(
        registry_service=registry_service,
        http_client=success_client,
    )
    result = await failed_tasks_service.list_failed_tasks(service_id="svc", limit=1)
    assert result["items"][0]["failure_id"] == "one"
    with pytest.raises(federation.StudioFederationError) as invalid_cursor:
        await failed_tasks_service.list_failed_tasks(cursor="bad")
    assert invalid_cursor.value.code == "invalid_cursor"


def test_federation_alias_history_lineage_and_response_detail_edges() -> None:
    service = make_event_record(service_id="svc", capabilities={"bad": True})
    assert federation._event_correlation_id(service, "bad") is None
    assert federation._event_correlation_id(service, {"correlation_id": "corr"}) == "corr"
    assert federation._event_parent_task_ids(service, "bad") == []
    assert federation._event_parent_task_ids(service, {}) == []
    assert federation._event_parent_task_ids(service, {"meta": {"parent_task_id": "parent"}}) == ["parent"]
    assert federation._history_events({"events": "bad"}) == []
    assert federation._workflow_lineage_values({"nodes": "bad"}) == []
    assert (
        federation._workflow_lineage_values(
            {
                "nodes": [
                    "bad",
                    {"kind": "workflow_message", "annotations": "bad"},
                    {"kind": "workflow_message", "annotations": {"correlation_id": ""}},
                ]
            }
        )
        == []
    )
    assert federation.StudioFederationService._response_detail(httpx.Response(400, text=" plain ")) == "plain"
    assert federation.StudioFederationService._response_detail(httpx.Response(400, json=[])) is None


def test_failed_task_email_config_and_client_validation_edges() -> None:
    base = {
        "service_url": "https://email.example.test/send",
        "api_key": "key",
        "receivers": ("ops@example.test",),
    }
    invalid = [
        {"service_url": ""},
        {"api_key": ""},
        {"receivers": ()},
        {"interval_seconds": -1},
        {"timeout_seconds": 0},
        {"dedupe_ttl_seconds": 0},
        {"scan_limit": 0},
        {"default_batch_wait_seconds": -1},
    ]
    for changes in invalid:
        with pytest.raises(ValueError):
            failed_task_notifications.FailedTaskEmailNotificationConfig(**(base | changes))
    with pytest.raises(ValueError):
        failed_task_notifications.FailedTaskEmailClient(
            http_client=AsyncMock(),
            service_url="https://blocked.invalid-host",
            api_key="key",
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_failed_task_email_store_queue_and_send_failure_edges() -> None:
    redis = FakeRedis()
    store = failed_task_notifications.RedisFailedTaskEmailSettingsStore(redis)
    redis.values["studio:failed_task_email:settings"] = "bad-json"
    assert not (await store.get()).enabled

    config = failed_task_notifications.FailedTaskEmailNotificationConfig(
        service_url="https://email.example.test/send",
        api_key="key",
        receivers=("ops@example.test",),
    )
    federation_service = AsyncMock()
    email_client = AsyncMock()
    service = failed_task_notifications.FailedTaskEmailNotificationService(
        federation_service=federation_service,
        redis=redis,
        email_client=email_client,
        config=config,
        settings_store=AsyncMock(),
    )
    pending = {"started_at": "", "items": []}
    assert not await service._should_queue_item({}, pending)
    redis.values[service._notified_key("svc", "one")] = "yes"
    assert not await service._should_queue_item({"service_id": "svc", "failure_id": "one"}, pending)
    pending["items"] = [{"service_id": "svc", "failure_id": "two"}]
    assert not await service._should_queue_item({"service_id": "svc", "failure_id": "two"}, pending)
    assert await service._should_queue_item({"service_id": "svc", "failure_id": "three"}, pending)
    assert pending["started_at"]

    email_client.send.side_effect = RuntimeError("offline")
    sent = await service._send_immediate_notifications(
        [{"bad": True}, {"service_id": "svc", "failure_id": "four", "error_message": "boom"}]
    )
    assert sent == 0
    assert await service._send_batch_notification([{"bad": True}]) == 0
    assert (
        await service._send_batch_notification([{"service_id": "svc", "failure_id": "five", "error_message": "boom"}])
        == 0
    )

    redis.values[service._pending_key()] = "bad-json"
    assert (await service._load_pending_batch())["items"] == []
    federation_service.list_failed_tasks.side_effect = [
        {"items": [{"failure_id": "one"}, "bad"], "next_cursor": "repeat"},
        {"items": [{"failure_id": "two"}], "next_cursor": "repeat"},
    ]
    assert len(await service._list_unreviewed_failed_task_items()) == 2

    client = failed_task_notifications.FailedTaskEmailClient(
        http_client=AsyncMock(post=AsyncMock(return_value=httpx.Response(503))),
        service_url="https://email.example.test/send",
        api_key="key",
        timeout_seconds=1,
    )
    with pytest.raises(failed_task_notifications.FailedTaskEmailNotificationError):
        await client.send(receivers=("ops@example.test",), title="title", body="body")


def test_federation_normalizers_and_join_candidate_edges() -> None:
    service_record = make_event_record(service_id="svc")
    service = federation.StudioFederationService(registry_service=AsyncMock(), http_client=AsyncMock())
    history = service._normalize_history_payload(
        service_record,
        {"events": ["raw", {"task_id": "task", "correlation_id": "corr"}]},
        requested_task_id=None,
    )
    assert history["events"][0] == "raw"
    assert (
        service._normalize_dlq_messages_payload(service_record, {"items": ["raw", {"correlation_id": "corr"}]})[
            "items"
        ][0]
        == "raw"
    )
    assert (
        service._normalize_broker_dlq_messages_payload(service_record, {"items": ["raw", {"correlation_id": "corr"}]})[
            "items"
        ][0]
        == "raw"
    )
    assert (
        service._normalize_failed_tasks_payload(service_record, {"items": ["raw", {"failure_id": "one"}]})["items"][0]
        == "raw"
    )
    graph = service._normalize_execution_graph_payload(
        service_record,
        {
            "nodes": [
                "raw",
                {"id": "task", "task_id": "task", "annotations": {"correlation_id": "corr"}},
            ]
        },
        requested_task_id="task",
    )
    assert graph["nodes"][0] == "raw"
    primary = service._build_primary_task_ref(
        service_record,
        task_id="task",
        latest_status=None,
        history={"events": [{"task_ref": {"service_id": "svc", "task_id": "other"}}]},
        execution_graph={
            "related_task_ids": ["child"],
            "nodes": [
                "raw",
                {"kind": "task"},
                {
                    "kind": "aggregation_child",
                    "task_ref": {"service_id": "svc", "task_id": "aggregate-child"},
                },
            ],
        },
    )
    assert {item.task_id for item in primary.child_refs} == {"child", "aggregate-child"}
    task_ref = federation.build_task_ref(
        service_id="svc",
        task_id="task",
        correlation_id="corr",
        parent_refs=[federation.build_task_pointer("svc", "parent")],
        child_refs=[federation.build_task_pointer("svc", "parent")],
    )
    candidates = service._collect_join_candidates(
        task_ref,
        {
            "nodes": [
                {"kind": "workflow_message", "annotations": {"correlation_id": "parent"}},
                {"kind": "workflow_message", "annotations": {"correlation_id": "workflow"}},
            ]
        },
        join=federation.JoinMode.ALL,
    )
    assert ("correlation_id", "corr") in candidates
    assert ("workflow_lineage", "workflow") in candidates


@pytest.mark.asyncio
async def test_federation_search_handles_matches_errors_and_fallback_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [make_event_record(service_id="one"), make_event_record(service_id="two")]
    registry_service = AsyncMock()
    registry_service.list_services.return_value = records
    service = federation.StudioFederationService(registry_service=registry_service, http_client=AsyncMock())

    async def latest_status(self, record, task_id):
        del self
        if record.service_id == "two":
            raise federation.StudioFederationError(status_code=502, detail="offline", code="offline")
        return {"task_id": task_id, "event": {"status": "running"}}

    monkeypatch.setattr(federation.StudioFederationService, "_search_latest_status", latest_status)
    result = await service.search_tasks(" task ")
    assert result.count == 1
    assert len(result.errors) == 1

    async def unsupported_status(self, record, task_id):
        del self, record, task_id
        raise federation.StudioFederationError(status_code=501, detail="unsupported", code="unsupported_route")

    async def history(self, record, **kwargs):
        del self, record, kwargs
        return {"events": [{"task_id": "task", "status": "completed"}]}

    monkeypatch.setattr(federation.StudioFederationService, "_fetch_status", unsupported_status)
    monkeypatch.setattr(federation.StudioFederationService, "_fetch_history", history)
    fallback = await service._search_latest_status(records[0], "task")
    assert fallback is not None


@pytest.mark.asyncio
async def test_health_parsing_store_materialization_and_overall_edges() -> None:
    assert health._parse_datetime("") is None
    assert health._parse_datetime("bad") is None
    assert health._latest_datetime() is None
    redis = FakeRedis()
    store = health.RedisStudioHealthStore(redis)
    redis.values[store._key("svc")] = "bad-json"
    assert await store.get_health("svc") is None

    service = health.StudioHealthRefreshService(
        registry_service=AsyncMock(),
        health_store=AsyncMock(),
        activity_reader=AsyncMock(),
        http_client=AsyncMock(),
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    reachable = health.HttpStatusSummary(state=health.StudioHttpReachability.REACHABLE, error_detail="old")
    assert service._materialize_http_status(reachable).error_detail is None
    record = make_event_record(service_id="svc", capabilities={"bad": True})
    missing = service._materialize_capability_status(record, health.CapabilityHealthSummary(), now)
    assert missing.state == health.CapabilityHealthState.MISSING
    errored = service._materialize_capability_status(
        record,
        health.CapabilityHealthSummary(error_detail="bad"),
        now,
    )
    assert errored.state == health.CapabilityHealthState.ERROR
    snapshot = events.StudioServiceActivitySnapshot(service_id="svc")
    assert service._materialize_observation_freshness(snapshot, now).state == health.ObservationFreshnessState.MISSING
    assert (
        service._materialize_worker_health(record, health.WorkerHealthSummary(), now).state
        == health.WorkerHealthState.UNKNOWN
    )

    document = health.StudioServiceHealthDocument(service_id="svc", registry_status=registry.ServiceStatus.REGISTERED)
    assert service._derive_overall_status(document) == health.StudioOverallHealthStatus.DEGRADED
    document.registry_status = registry.ServiceStatus.DISABLED
    assert service._derive_overall_status(document) == health.StudioOverallHealthStatus.DISABLED
    document.registry_status = registry.ServiceStatus.REGISTERED
    document.http_status.state = health.StudioHttpReachability.UNREACHABLE
    assert service._derive_overall_status(document) == health.StudioOverallHealthStatus.UNREACHABLE


def test_loki_cursor_template_filter_and_payload_edges() -> None:
    assert logs._parse_cursor_timestamp("123") == "123"
    with pytest.raises(logs.StudioLogConfigError):
        logs._logql_matcher_operator("bad")
    with pytest.raises(ValueError):
        logs._parse_page_cursor("loki:v1:bad")
    assert logs._encode_page_cursor(cursor="123", skip_count=0) == "123"
    provider = logs.LokiLogProvider(http_client=AsyncMock(), query_path="query")
    record = make_record(
        metrics_config=metrics.PrometheusMetricsConfig(
            base_url="https://prom.example.test", namespace="prod", service_selector_labels={"app": "svc"}
        )
    )
    config = registry.LokiLogConfig(
        base_url="https://loki.example.test",
        service_selector_labels={"app": "svc"},
        pod_value_template="{namespace}/{pod}",
    )
    assert provider._render_pod_value_template(service=record, config=config, pod="one") == "prod/one"
    missing_config = config.model_copy(update={"pod_value_template": "{missing}"})
    with pytest.raises(logs.StudioLogConfigError):
        provider._render_pod_value_template(service=record, config=missing_config, pod="one")
    query = logs.StudioLogQuery(task_id="task")
    assert provider._build_task_filter(config=config, query=query) is None
    structured = config.model_copy(update={"task_match_mode": "structured_metadata"})
    assert provider._build_task_filter(config=structured, query=query) == ("|", "task", "task_id")
    contains = config.model_copy(update={"task_match_mode": "contains", "task_match_template": "id={task_id}"})
    assert provider._build_task_filter(config=contains, query=query) == ("|=", "id=task", None)
    regex = config.model_copy(update={"task_match_mode": "regex"})
    assert provider._build_task_filter(config=regex, query=query) == ("|~", "task", None)
    unsupported = config.model_copy(update={"task_match_mode": "bad"})
    with pytest.raises(logs.StudioLogConfigError):
        provider._build_task_filter(config=unsupported, query=query)
    for payload in [
        {},
        {"status": "success", "data": []},
        {"status": "success", "data": {"resultType": "streams", "result": {}}},
    ]:
        with pytest.raises(logs.StudioLogProviderError):
            provider._normalize_response(service=record, config=config, payload=payload)
    assert (
        provider._normalize_response(
            service=record,
            config=config,
            payload={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        "bad",
                        {"stream": "bad", "values": []},
                        {"stream": {}, "values": [["bad"], ["cursor", "line"]]},
                    ],
                },
            },
        )
        == []
    )
    assert provider._skip_boundary_entries([], cursor="1", skip_count=0) == []


@pytest.mark.asyncio
async def test_metrics_query_service_configuration_and_window_fallback_edges() -> None:
    registry_service = AsyncMock()
    without_config = make_record(metrics_config=None)
    registry_service.get_service.return_value = without_config
    service = metrics.StudioMetricsQueryService(registry_service=registry_service, providers={})
    with pytest.raises(metrics.StudioMetricsProviderNotConfiguredError):
        await service.query_service_pods("svc")
    with pytest.raises(metrics.StudioMetricsProviderNotConfiguredError):
        await service._query_metrics(service=without_config, query=metrics.StudioMetricsQuery())

    configured = make_record(
        metrics_config=metrics.PrometheusMetricsConfig(
            base_url="https://prom.example.test", namespace="prod", service_selector_labels={"app": "svc"}
        )
    )
    registry_service.get_service.return_value = configured
    with pytest.raises(metrics.StudioMetricsConfigError):
        await service.query_service_pods("svc")
    with pytest.raises(metrics.StudioMetricsConfigError):
        await service._query_metrics(service=configured, query=metrics.StudioMetricsQuery())

    explicit = metrics.StudioMetricsQuery(from_time="2026-01-01T00:00:00Z")
    assert (await service._with_task_window("svc", "task", explicit))[0] is explicit
    fallback_query, warnings = await service._with_task_window("svc", "task", metrics.StudioMetricsQuery())
    assert fallback_query.from_time and fallback_query.to_time and warnings

    federation_service = AsyncMock()
    federation_service.get_task_detail.side_effect = federation.StudioFederationError(
        status_code=502, detail="offline", code="offline"
    )
    with_federation = metrics.StudioMetricsQueryService(
        registry_service=registry_service,
        providers={},
        federation_service=federation_service,
    )
    derived, derived_warnings = await with_federation._with_task_window("svc", "task", metrics.StudioMetricsQuery())
    assert derived.from_time and len(derived_warnings) == 2


@pytest.mark.asyncio
async def test_trace_path_integration_failures_and_selection_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    record = make_trace_record(trace_config=tempo_trace_config())
    registry_service = AsyncMock()
    registry_service.get_service.return_value = record
    federation_service = AsyncMock()
    federation_service.get_task_detail.side_effect = federation.StudioFederationError(
        status_code=502, detail="offline", code="offline"
    )
    event_service = AsyncMock()
    event_service.list_task_events.side_effect = RuntimeError("offline")
    service = traces.StudioTraceQueryService(
        registry_service=registry_service,
        providers={},
        federation_service=federation_service,
        event_ingest_service=event_service,
    )

    async def broken_trace_query(self, **kwargs):
        del self, kwargs
        raise traces.StudioTraceProviderError("bad provider")

    monkeypatch.setattr(traces.StudioTraceQueryService, "_query_task_traces_for_service", broken_trace_query)
    path = await service.query_task_trace_path("payments-api", "task")
    assert any("task detail" in warning for warning in path.warnings)
    assert any("provider spans" in warning for warning in path.warnings)
    assert any("event history" in warning for warning in path.warnings)

    log_service = AsyncMock()
    log_service.query_task_logs.side_effect = RuntimeError("offline")
    record.log_config = registry.LokiLogConfig(
        base_url="https://loki.example.test",
        service_selector_labels={"app": "payments-api"},
    )
    discovery_service = traces.StudioTraceQueryService(
        registry_service=registry_service,
        providers={},
        federation_service=federation_service,
        log_query_service=log_service,
    )
    ids, warnings = await discovery_service._discover_task_trace_ids(record, "task")
    assert ids == [] and len(warnings) == 2

    nodes = [
        traces.StudioTracePathNode(
            id="one", kind="task", label="one", task_id="task", started_at="2026-01-02T00:00:00Z"
        ),
        traces.StudioTracePathNode(id="two", kind="stage", label="two", task_id="other", stage="run"),
    ]
    assert traces._best_node_for_event(nodes, task_id="missing", event_type="event", payload={}).id == "one"
    assert traces._best_node_for_event(nodes, task_id="other", event_type="event", payload={"stage": "run"}).id == "two"
    assert traces._first_node(nodes, stage="missing") is None
    assert traces._latest_non_dlq_node([], task_id="task", before=None) is None
    assert traces._state_from_status("running") == "running"


@pytest.mark.asyncio
async def test_federation_search_join_builder_materializes_joined_items(monkeypatch: pytest.MonkeyPatch) -> None:
    source_service = make_event_record(service_id="source")
    joined_service = make_event_record(service_id="joined")
    source_ref = federation.build_task_ref(service_id="source", task_id="task", correlation_id="corr")
    source_item = federation.StudioTaskSearchItem(
        service_id="source",
        task_id="task",
        task_ref=source_ref,
        service_name="Source",
        environment="prod",
        latest_status={},
        detail_path="/source/task",
    )
    joined_ref = federation.build_task_ref(service_id="joined", task_id="corr")
    joined_item = federation.StudioTaskSearchItem(
        service_id="joined",
        task_id="corr",
        task_ref=joined_ref,
        service_name="Joined",
        environment="prod",
        latest_status={},
        detail_path="/joined/corr",
    )
    bundle = federation._TaskDetailBundle(
        service=source_service,
        task_id="task",
        task_ref=source_ref,
        latest_status=None,
        history=None,
        dlq_messages=None,
        execution_graph=None,
        errors=[],
    )

    async def build_bundle(self, service, task_id):
        del self, service, task_id
        return bundle

    async def resolve(self, services, **kwargs):
        del self, services, kwargs
        return federation._SearchTaskMatch(service=joined_service, item=joined_item), None

    monkeypatch.setattr(federation.StudioFederationService, "_build_task_detail_bundle", build_bundle)
    monkeypatch.setattr(federation.StudioFederationService, "_resolve_join_candidate", resolve)
    service = federation.StudioFederationService(registry_service=AsyncMock(), http_client=AsyncMock())
    items, warnings = await service._build_search_joins(
        [federation._SearchTaskMatch(service=source_service, item=source_item)],
        [source_service, joined_service],
        join=federation.JoinMode.CORRELATION,
    )
    assert items[0].join_kind == "correlation_id"
    assert warnings == []


@pytest.mark.asyncio
async def test_registry_store_update_delete_duplicate_and_indexer_edges() -> None:
    base = make_event_record(service_id="one")
    assert registry.ServiceRecord.model_validate(base.model_dump() | {"tags": None}).tags == []
    assert (
        registry.UpdateServiceRequest(base_url="https://updated.example.test").base_url
        == "https://updated.example.test"
    )
    assert registry.UpdateServiceRequest(tags=[" a ", "a"]).tags == ["a"]
    policy = registry.StudioOutboundUrlPolicy()
    with pytest.raises(registry.OutboundUrlPolicyError):
        policy.validate_url("not-a-url")
    fetcher = registry.HttpCapabilityFetcher(capability_path="capabilities")
    assert fetcher._capability_path == "/capabilities"

    redis = FakeRedis()
    store = registry.RedisServiceRegistryStore(redis)
    with pytest.raises(registry.ServiceNotFoundError):
        await store.update("missing", base)
    with pytest.raises(registry.ServiceNotFoundError):
        await store.delete("missing")
    await store.create(base)
    with pytest.raises(registry.DuplicateServiceError):
        await store.create(base)
    conflicting = make_event_record(service_id="two", base_url=base.base_url)
    with pytest.raises(registry.DuplicateServiceError):
        await store.create(conflicting)
    second = make_event_record(service_id="two")
    await store.create(second)
    with pytest.raises(registry.DuplicateServiceError):
        await store.update("one", base.model_copy(update={"base_url": second.base_url}))
    updated = base.model_copy(update={"base_url": "https://changed.example.test"})
    await store.update("one", updated)
    assert await store.get("one") == updated
    redis.values[store._env_url_key("prod", updated.base_url)] = b"one"
    assert await store._get_env_url_owner("prod", updated.base_url) == "one"

    indexer = AsyncMock()
    service = registry.ServiceRegistryService(store=store, search_indexer=indexer)
    await service.update_service("one", registry.UpdateServiceRequest(name="Updated"))
    indexer.upsert_service_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_service_filter_expiry_and_fallback_edges() -> None:
    assert search._earlier_iso(None, "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
    redis_store = search.RedisStudioSearchStore(FakeRedis())
    assert await redis_store.list_service_document_ids_for_filter("status", "healthy") == set()

    registry_service = AsyncMock()
    event_store = AsyncMock()
    store = AsyncMock()
    store.task_index_is_empty.return_value = False
    service = search.StudioSearchService(registry_service=registry_service, event_store=event_store, store=store)
    await service.initialize()
    registry_service.list_services.assert_not_awaited()

    store.list_service_document_ids_for_filter.side_effect = [{"svc", "missing"}, {"svc", "missing"}]
    document = search.StudioServiceSearchDocument(
        service_id="svc",
        name="Payments API",
        environment="prod",
        tags=["core"],
        status="healthy",
        health_status="healthy",
        base_url="https://svc.example.test",
        auth_mode="internal_network",
    )
    store.get_service_document.side_effect = lambda service_id: document if service_id == "svc" else None
    store.list_service_document_ids.return_value = {"svc", "missing"}
    store.list_service_document_ids_for_token.return_value = {"svc", "missing"}
    response = await service.search_services(
        search.StudioServiceSearchQuery(environment="prod", status="healthy", query="payments", limit=10)
    )
    assert response.count == 1
    no_match = await service.search_services(search.StudioServiceSearchQuery(query="missing", limit=10))
    assert no_match.count == 0

    expired = search.StudioTaskSearchDocument(
        service_id="svc",
        service_name="Service",
        environment="prod",
        task_id="task",
        detail_path="/task",
        expires_at="2020-01-01T00:00:00Z",
    )
    store.get_task_document.return_value = expired
    loaded = await service._load_task_documents([expired.document_id, "missing"])
    assert loaded == []
    assert any(call.args == (expired.document_id,) for call in store.delete_task_document.await_args_list)

    registry_service.list_services.return_value = [make_event_record(service_id="svc")]
    fallback = search.StudioSearchService(
        registry_service=registry_service,
        event_store=event_store,
        store=store,
        log_query_service=AsyncMock(),
    )
    assert await fallback._search_task_logs(search.StudioTaskSearchQuery(task_id="task")) == []
    assert (
        await fallback._query_loki_fallback_logs(
            make_event_record(service_id="svc"), search.StudioTaskSearchQuery(task_id="task")
        )
        is not None
    )


@pytest.mark.asyncio
async def test_remaining_provider_policy_json_and_notification_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    record = make_record(
        metrics_config=metrics.PrometheusMetricsConfig(
            base_url="https://blocked.invalid-host", namespace="prod", service_selector_labels={"app": "svc"}
        )
    )
    metrics_provider = metrics.PrometheusMetricsProvider(http_client=AsyncMock())
    with pytest.raises(metrics.StudioMetricsProviderError):
        await metrics_provider.query_service_pods(service=record, config=record.metrics_config)
    with pytest.raises(metrics.StudioMetricsProviderError):
        await metrics_provider._query_group(
            service=record,
            config=record.metrics_config,
            query=metrics.StudioMetricsQuery(),
            group=metrics.StudioMetricGroup.CPU_USAGE,
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
            step_seconds=30,
        )

    log_record = make_event_record(service_id="svc")
    log_config = registry.LokiLogConfig(base_url="https://loki.example.test", service_selector_labels={"app": "svc"})
    for response_kind in ("transport", "json"):

        def handler(request: httpx.Request, kind: str = response_kind) -> httpx.Response:
            if kind == "transport":
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, text="bad-json")

        provider = logs.LokiLogProvider(
            http_client=TrackingAsyncClient(transport=httpx.MockTransport(handler), timeout=1.0)
        )
        with pytest.raises(logs.StudioLogProviderError):
            await provider.query_logs(service=log_record, config=log_config, query=logs.StudioLogQuery())
    bad_template = log_config.model_copy(update={"pod_value_template": "{pod:invalid}"})
    with pytest.raises(logs.StudioLogConfigError):
        logs.LokiLogProvider(http_client=AsyncMock())._render_pod_value_template(
            service=log_record, config=bad_template, pod="one"
        )

    redis = FakeRedis()
    settings = failed_task_notifications.RedisFailedTaskEmailSettingsStore(redis)
    redis.values["studio:failed_task_email:settings"] = '{"enabled":true,"batch_wait_seconds":10}'
    assert (await settings.get()).enabled
    assert failed_task_notifications._parse_datetime("bad").tzinfo is not None
    assert failed_task_notifications._parse_datetime("2026-01-01T00:00:00").tzinfo == UTC

    notification = failed_task_notifications.FailedTaskEmailNotificationService(
        federation_service=AsyncMock(),
        redis=redis,
        email_client=AsyncMock(),
        config=failed_task_notifications.FailedTaskEmailNotificationConfig(
            service_url="https://email.example.test", api_key="key", receivers=("ops@example.test",)
        ),
        settings_store=settings,
    )
    monkeypatch.setattr(
        notification.federation_service, "list_failed_tasks", AsyncMock(return_value={"items": ["bad"]})
    )
    assert (
        await notification._notify_new_failed_tasks(
            failed_task_notifications.FailedTaskEmailRuntimeSettings(enabled=True, batch_wait_seconds=0)
        )
        == 0
    )


def test_trace_span_matching_parent_edge_and_federation_alias_edges() -> None:
    nodes = [
        traces.StudioTracePathNode(id="attempt", kind="task_attempt", label="attempt", task_id="task"),
        traces.StudioTracePathNode(id="message", kind="workflow_message", label="message", task_id="task"),
    ]
    consumer = traces.StudioTraceSpan(
        trace_id="a" * 32,
        span_id="consumer",
        name="consumer",
        kind="SPAN_KIND_CONSUMER",
        attributes={"task_id": "task"},
    )
    producer = traces.StudioTraceSpan(
        trace_id="a" * 32,
        span_id="producer",
        name="publish",
        kind="SPAN_KIND_PRODUCER",
        attributes={"task_id": "task"},
    )
    assert traces._best_node_for_span(nodes, consumer, fallback_task_id="task").id == "attempt"
    assert traces._best_node_for_span(nodes, producer, fallback_task_id="task").id == "message"
    edges = [traces.StudioTracePathEdge(id="existing", source="attempt", target="message", kind="span_child")]
    child = producer.model_copy(update={"parent_span_id": "missing"})
    traces._attach_spans(
        nodes=nodes, edges=edges, nodes_by_id={node.id: node for node in nodes}, spans=[child], task_id="task"
    )
    assert len(edges) == 1

    capabilities = make_capability_document(supported_routes=[])
    capabilities["alias_config_summary"]["payload_aliases"] = {  # type: ignore[index]
        "correlation_id": "corr_alias",
        "parent_task_id": "parent_alias",
    }
    record = make_event_record(service_id="svc", capabilities=capabilities)
    assert federation._event_correlation_id(record, {"corr_alias": "corr"}) == "corr"
    assert federation._event_parent_task_ids(record, {"meta": {"parent_alias": "parent"}}) == ["parent"]


@pytest.mark.asyncio
async def test_federation_join_resolution_warning_and_empty_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    source_ref = federation.build_task_ref(service_id="source", task_id="task")
    service = federation.StudioFederationService(registry_service=AsyncMock(), http_client=AsyncMock())

    async def with_error(self, *args, **kwargs):
        del self, args, kwargs
        return [], [federation.FederatedError(detail="offline", code="offline")]

    monkeypatch.setattr(federation.StudioFederationService, "_find_task_matches", with_error)
    match, warning = await service._resolve_join_candidate(
        [], source_task_ref=source_ref, join_kind="correlation_id", matched_value="corr"
    )
    assert match is None and warning is not None and warning.code == "incomplete_join_candidate_scan"

    async def empty(self, *args, **kwargs):
        del self, args, kwargs
        return [], []

    monkeypatch.setattr(federation.StudioFederationService, "_find_task_matches", empty)
    assert await service._resolve_join_candidate(
        [], source_task_ref=source_ref, join_kind="correlation_id", matched_value="corr"
    ) == (None, None)


@pytest.mark.asyncio
async def test_service_event_stream_keepalive_and_invalid_message_edges() -> None:
    store = events.RedisStudioEventStore(FakeRedis(), prefix="stream")
    stream = events.StudioEventStream(event_store=store, keepalive_interval_seconds=0.001)
    generator = stream.stream_service("svc")
    assert await anext(generator) == b"event: ready\ndata: {}\n\n"
    assert await anext(generator) == b": keepalive\n\n"
    await generator.aclose()

    payload = events.StudioControlPlaneEvent(
        service_id="svc",
        ingest_method=StudioEventIngestMethod.PUSH,
        ingested_at="2026-01-01T00:00:00Z",
        dedupe_key="one",
        out_of_order=False,
        task_id="task",
        event_type="status.completed",
        source_kind=ServiceEventSourceKind.STATUS,
        component="status",
        timestamp="2026-01-01T00:00:00Z",
        payload={},
    )
    pubsub = store.redis.pubsub()
    pubsub._messages.extend(
        [
            {"type": "subscribe", "data": "ignored"},
            {"type": "message", "data": ""},
            {"type": "message", "data": "bad-json"},
            {"type": "message", "data": payload.model_dump_json()},
        ]
    )
    store.redis.pubsub = lambda: pubsub
    chunk_generator = stream._stream("channel")
    assert b"status.completed" in await anext(chunk_generator)
    await chunk_generator.aclose()
