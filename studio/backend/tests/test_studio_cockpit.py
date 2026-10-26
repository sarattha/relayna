from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import relayna_studio.app as studio_app
from fastapi.testclient import TestClient
from relayna_studio import ServiceStatus, create_studio_app, get_studio_runtime
from relayna_studio.cockpit import _drain, _freshness, _queues, _workers
from test_studio_federation import (
    FakeRedis,
    TrackingAsyncClient,
    install_service,
    make_capability_document,
    make_record,
)

from relayna.api import WorkerHeartbeatListResponse
from relayna.observability import RuntimePressureSnapshot, RuntimeQueueObservation

NOW = datetime.now(UTC)


def queue(**updates):
    return RuntimeQueueObservation.model_validate(
        {
            "queue_name": "orders",
            "source": "broker",
            "observed_at": NOW,
            "ready": 90,
            "unacknowledged": 10,
            "consumers": 2,
            "arrival_rate": 2,
            "completion_rate": 4,
            "rate_window_seconds": 300,
        }
        | updates
    )


@pytest.mark.parametrize(
    "arrival,completion,state,seconds",
    [
        (2, 4, "draining", 50),
        (4, 4, "not_draining", None),
        (5, 4, "not_draining", None),
        (0, 0, "not_draining", None),
        (0, 2, "draining", 50),
    ],
)
def test_drain_requires_net_capacity(arrival, completion, state, seconds):
    result = _drain(queue(arrival_rate=arrival, completion_rate=completion), "fresh")
    assert result["state"] == state
    assert result["seconds"] == seconds


@pytest.mark.parametrize("field", ["ready", "unacknowledged", "arrival_rate", "completion_rate", "rate_window_seconds"])
def test_drain_never_substitutes_missing_data(field):
    assert _drain(queue(**{field: None}), "fresh")["state"] == "unknown"


def test_stale_empty_and_overflow_drain():
    assert _drain(queue(), "stale")["state"] == "unknown"
    assert _drain(queue(ready=0, unacknowledged=0), "fresh")["seconds"] == 0
    assert _drain(queue(arrival_rate=0, completion_rate=5e-324), "fresh")["state"] == "unknown"


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (None, "missing"),
        (NOW.replace(tzinfo=None), "missing"),
        (NOW, "fresh"),
        (NOW - timedelta(seconds=91), "stale"),
        (NOW + timedelta(seconds=31), "stale"),
    ],
)
def test_freshness(timestamp, expected):
    assert _freshness(timestamp, NOW, 90) == expected


def test_queue_normalization_preserves_sources_legacy_zero_and_freshness():
    snapshot = RuntimePressureSnapshot.model_validate(
        {
            "reported_at": NOW,
            "queues": [
                queue().model_dump(),
                queue(source="other", observed_at=NOW - timedelta(minutes=5)).model_dump(),
            ],
            "signals": [
                {"scope": "queue", "scope_id": "legacy", "kind": "queue_depth_high", "severity": "normal", "value": 0},
                {
                    "scope": "queue",
                    "scope_id": "legacy",
                    "kind": "consumer_count_zero",
                    "severity": "critical",
                    "value": 0,
                },
                {
                    "scope": "queue",
                    "scope_id": "unknown",
                    "kind": "queue_inspection_unavailable",
                    "severity": "unknown",
                },
            ],
        }
    )
    rows = _queues(snapshot, NOW, 90)
    assert len(rows) == 4
    by_source = {row["source"]: row for row in rows}
    assert by_source["broker"]["drain"]["seconds"] == 50
    assert by_source["other"]["freshness"] == "stale" and by_source["other"]["drain"]["state"] == "unknown"
    assert rows[2]["ready"] == 0 and rows[2]["consumers"] == 0
    assert rows[2]["unacknowledged"] is None
    assert rows[3]["ready"] is None and rows[3]["consumers"] is None
    snapshot.reported_at = NOW - timedelta(minutes=5)
    assert all(q["drain"]["state"] == "unknown" for q in _queues(snapshot, NOW, 90))


def test_workers_use_individual_heartbeats_and_actual_lease_expiry():
    payload = WorkerHeartbeatListResponse.model_validate(
        {
            "reported_at": NOW,
            "workers": [
                {"worker_name": "live", "running": True, "last_heartbeat_at": NOW},
                {"worker_name": "old", "running": True, "last_heartbeat_at": NOW - timedelta(minutes=5)},
                {
                    "worker_name": "stopped",
                    "running": False,
                    "active_leases": [
                        {
                            "lease_id": "lease",
                            "task_id": "same-task",
                            "owner_id": "owner",
                            "consumer_name": "worker",
                            "heartbeat_at": NOW - timedelta(minutes=2),
                            "expires_at": NOW - timedelta(seconds=1),
                            "acquired_at": NOW - timedelta(minutes=10),
                            "expired": False,
                        }
                    ],
                },
            ],
        }
    )
    workers = _workers(payload, NOW, 90)
    assert [w["state"] for w in workers] == ["fresh", "stale", "stopped"]
    lease = workers[2]["active_leases"][0]
    assert lease["expired"] and lease["age_seconds"] == 600
    assert lease["owner_id"] == "owner" and lease["freshness"] == "stale"
    payload.reported_at = NOW - timedelta(minutes=10)
    assert _workers(payload, NOW, 90)[0]["freshness"] == "stale"


@contextmanager
def client_for(monkeypatch, handler, routes=None, metrics_config=None):
    monkeypatch.setattr(studio_app, "Redis", FakeRedis)
    app = create_studio_app(
        redis_url="redis://test/0",
        push_ingest_enabled=True,
        health_refresh_interval_seconds=None,
        pull_sync_interval_seconds=None,
        federation_client_factory=lambda timeout: TrackingAsyncClient(
            transport=httpx.MockTransport(handler), timeout=timeout
        ),
    )
    record = make_record(
        service_id="orders",
        base_url="https://orders.example.test",
        capabilities=make_capability_document(
            supported_routes=routes if routes is not None else ["runtime.backpressure", "health.workers"]
        ),
    )
    record.metrics_config = metrics_config
    with TestClient(app) as client:
        install_service(app, record)
        yield client, app


def test_endpoint_keeps_partial_pressure_and_unavailable_workers(monkeypatch):
    def handler(request):
        if request.url.path.endswith("backpressure"):
            return httpx.Response(
                200,
                json={
                    "reported_at": datetime.now(UTC).isoformat(),
                    "queues": [queue(observed_at=datetime.now(UTC)).model_dump(mode="json")],
                    "signals": [
                        {"scope": "collector", "kind": "pressure_collector_unavailable", "severity": "unknown"}
                    ],
                },
            )
        return httpx.Response(503)

    with client_for(monkeypatch, handler) as (client, app):
        result = client.get("/studio/services/orders/runtime/cockpit")
    assert result.status_code == 200
    data = result.json()
    assert data["service_id"] == "orders" and len(data["queues"]) == 1
    assert data["sources"]["pressure"]["state"] == "partial"
    assert data["sources"]["workers"]["state"] == "unavailable"
    assert data["sources"]["metrics"]["state"] == "not_configured"


@pytest.mark.parametrize(
    "payload",
    [{}, {"reported_at": "invalid"}, {"reported_at": datetime.now(UTC).isoformat(), "queues": [{"ready": -1}]}],
)
def test_invalid_pressure_does_not_hide_worker_evidence(monkeypatch, payload):
    def handler(request):
        if request.url.path.endswith("backpressure"):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"reported_at": datetime.now(UTC).isoformat(), "workers": []})

    with client_for(monkeypatch, handler) as (client, app):
        data = client.get("/studio/services/orders/runtime/cockpit").json()
    assert data["sources"]["pressure"]["state"] == "unavailable"
    assert data["sources"]["workers"]["state"] == "fresh"
    assert data["queues"] == [] and data["workers"] == []


def test_capabilities_disabled_and_missing_services(monkeypatch):
    def handler(request):
        raise AssertionError("Unsupported reads must not reach upstream")

    with client_for(monkeypatch, handler, routes=[]) as (client, app):
        data = client.get("/studio/services/orders/runtime/cockpit").json()
        assert data["sources"]["pressure"]["state"] == "unsupported"
        assert data["sources"]["workers"]["state"] == "unsupported"
        assert client.get("/studio/services/missing/runtime/cockpit").status_code == 404
        runtime = get_studio_runtime(app)
        record = asyncio.run(runtime.registry_store.get("orders"))
        record.status = ServiceStatus.DISABLED
        asyncio.run(runtime.registry_store.update("orders", record))
        assert client.get("/studio/services/orders/runtime/cockpit").status_code == 503


def test_metrics_partial_group_failure_preserves_other_series(monkeypatch):
    from test_studio_metrics import make_metrics_config

    def handler(request):
        return httpx.Response(404)

    from relayna_studio.metrics import StudioMetricsResponse

    async def metrics(service_id, query):
        metric = query.groups[0]
        if metric.value == "tasks_dlq_rate":
            raise RuntimeError("unavailable")
        return StudioMetricsResponse.model_validate(
            {
                "service_id": service_id,
                "from": query.from_time,
                "to": query.to_time,
                "step_seconds": 30,
                "series": [{"metric": metric, "unit": "count", "points": [{"timestamp": query.to_time, "value": 0}]}],
            }
        )

    with client_for(monkeypatch, handler, metrics_config=make_metrics_config()) as (client, app):
        runtime = get_studio_runtime(app)
        monkeypatch.setattr(runtime.metrics_query_service, "query_service_metrics", metrics)
        data = client.get("/studio/services/orders/runtime/cockpit").json()
    assert data["sources"]["metrics"]["state"] == "partial"
    assert len(data["metrics"]["series"]) == 3
    assert data["metrics"]["series"][0]["points"][0]["value"] == 0
    assert data["metrics"]["rate_window_seconds"] == 300
    assert "tasks_dlq_rate" in data["metrics"]["warnings"][0]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("fresh", "fresh"),
        ("empty", "missing"),
        ("stale", "stale"),
        ("invalid_timestamp", "stale"),
        ("nan", "missing"),
        ("failed", "unavailable"),
    ],
)
def test_metric_source_freshness_and_missing_samples(monkeypatch, mode, expected):
    from relayna_studio.metrics import StudioMetricsResponse
    from test_studio_metrics import make_metrics_config

    def handler(request):
        if request.url.path.endswith("workers"):
            return httpx.Response(
                200,
                json={
                    "workers": [
                        {"worker_name": "live", "running": True, "last_heartbeat_at": datetime.now(UTC).isoformat()}
                    ],
                    "reported_at": datetime.now(UTC).isoformat(),
                },
            )
        return httpx.Response(
            200,
            json={
                "reported_at": datetime.now(UTC).isoformat(),
                "queues": [queue(consumers=0, observed_at=datetime.now(UTC)).model_dump(mode="json")],
            },
        )

    async def metrics(service_id, query):
        if mode == "failed":
            raise RuntimeError("unreachable")
        points = (
            []
            if mode == "empty"
            else [
                {
                    "timestamp": "not-a-timestamp"
                    if mode == "invalid_timestamp"
                    else (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
                    if mode == "stale"
                    else query.to_time,
                    "value": float("nan") if mode == "nan" else 0,
                }
            ]
        )
        return StudioMetricsResponse.model_validate(
            {
                "service_id": service_id,
                "from": query.from_time,
                "to": query.to_time,
                "step_seconds": 30,
                "series": [{"metric": query.groups[0], "unit": "count", "points": points}],
            }
        )

    with client_for(monkeypatch, handler, metrics_config=make_metrics_config()) as (client, app):
        monkeypatch.setattr(get_studio_runtime(app).metrics_query_service, "query_service_metrics", metrics)
        data = client.get("/studio/services/orders/runtime/cockpit").json()
    assert data["sources"]["metrics"]["state"] == expected
    if mode == "invalid_timestamp":
        assert data["sources"]["pressure"]["state"] == "fresh"
        assert data["sources"]["workers"]["state"] == "fresh"
    if mode == "nan":
        assert data["metrics"]["series"][0]["points"][0]["value"] is None


def test_invalid_workers_do_not_discard_valid_pressure(monkeypatch):
    def handler(request):
        if request.url.path.endswith("workers"):
            return httpx.Response(200, json={"reported_at": "invalid"})
        return httpx.Response(
            200,
            json={
                "reported_at": datetime.now(UTC).isoformat(),
                "queues": [queue(arrival_rate=1, observed_at=datetime.now(UTC)).model_dump(mode="json")],
            },
        )

    with client_for(monkeypatch, handler) as (client, app):
        data = client.get("/studio/services/orders/runtime/cockpit").json()
    assert data["sources"]["workers"]["state"] == "unavailable"
    assert data["sources"]["pressure"]["state"] == "fresh"
    assert data["queues"][0]["drain"]["state"] == "draining"


def test_backlog_without_compatible_rates_has_no_capacity_verdict():
    rows = _queues(RuntimePressureSnapshot(reported_at=NOW, queues=[queue(arrival_rate=None)]), NOW, 90)
    assert rows[0]["drain"]["state"] == "unknown"
    assert "Ready work is waiting" in rows[0]["diagnosis"]


@pytest.mark.parametrize(
    "timestamp", [NOW + timedelta(minutes=1), NOW - timedelta(minutes=5), NOW.replace(tzinfo=None)]
)
def test_legacy_signal_timestamps_cannot_be_masked_by_a_fresh_envelope(timestamp):
    snapshot = RuntimePressureSnapshot.model_validate(
        {
            "reported_at": NOW,
            "signals": [
                {
                    "scope": "queue",
                    "scope_id": "orders",
                    "kind": "consumer_count_zero",
                    "severity": "critical",
                    "value": 0,
                    "observed_at": timestamp,
                },
                {
                    "scope": "queue",
                    "scope_id": "orders",
                    "kind": "queue_depth_high",
                    "severity": "warning",
                    "value": 12,
                    "observed_at": NOW,
                },
            ],
        }
    )
    row = _queues(snapshot, NOW, 90)[0]
    assert row["freshness"] == "stale"
    assert "stale" in row["diagnosis"]
    assert row["drain"]["state"] == "unknown"


def test_nonfinite_pressure_and_stale_envelope_signal_states(monkeypatch):
    mode = "nonfinite"

    def handler(request):
        if request.url.path.endswith("workers"):
            return httpx.Response(200, json={"reported_at": datetime.now(UTC).isoformat(), "workers": []})
        now = datetime.now(UTC)
        if mode == "nonfinite":
            return httpx.Response(
                200,
                content='{"reported_at":"'
                + now.isoformat()
                + '","signals":[{"scope":"queue","kind":"custom","severity":"normal","value":NaN}]}',
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(
            200,
            json={
                "reported_at": (now - timedelta(minutes=5)).isoformat(),
                "signals": [{"scope": "queue", "kind": "custom", "severity": "normal", "observed_at": now.isoformat()}],
            },
        )

    with client_for(monkeypatch, handler) as (client, app):
        data = client.get("/studio/services/orders/runtime/cockpit").json()
        assert data["sources"]["pressure"]["state"] == "unavailable"
        assert data["sources"]["workers"]["state"] == "fresh"
        mode = "stale"
        data = client.get("/studio/services/orders/runtime/cockpit").json()
        assert data["sources"]["pressure"]["state"] == "stale"
        assert data["signals"][0]["freshness"] == "stale"


def test_worker_snapshot_age_is_independent_of_configured_heartbeat_tolerance():
    payload = WorkerHeartbeatListResponse.model_validate(
        {
            "reported_at": NOW - timedelta(minutes=5),
            "workers": [
                {"worker_name": "worker", "running": True, "last_heartbeat_at": NOW},
            ],
        }
    )
    assert _workers(payload, NOW, 600)[0]["freshness"] == "stale"


@pytest.mark.parametrize("source_ids", [(None, None), ("primary/production", "secondary/production")])
def test_distinct_amqp_sources_with_same_queue_name_cannot_hide_waiting_work(source_ids):
    from relayna.observability import QueuePressureCollector, RuntimePressureService
    from relayna.rabbitmq import QueueInspection

    async def blocked(name):
        return QueueInspection(queue_name=name, message_count=500, consumer_count=0)

    async def idle(name):
        return QueueInspection(queue_name=name, message_count=0, consumer_count=4)

    async def scenario():
        return await RuntimePressureService(
            collectors=[
                QueuePressureCollector(queue_names=["orders"], inspect_queue=blocked, source_id=source_ids[0]),
                QueuePressureCollector(queue_names=["orders"], inspect_queue=idle, source_id=source_ids[1]),
            ]
        ).snapshot()

    rows = _queues(asyncio.run(scenario()), datetime.now(UTC), 90)
    assert len(rows) == 2
    assert len({row["source"] for row in rows}) == 2
    assert sorted((row["ready"], row["consumers"]) for row in rows) == [(0, 4), (500, 0)]
    assert "No consumers observed" in next(row["diagnosis"] for row in rows if row["ready"] == 500)


@pytest.mark.parametrize("heartbeat", [None, NOW - timedelta(hours=1)])
def test_idle_or_long_running_activity_does_not_establish_worker_absence(heartbeat):
    payload = WorkerHeartbeatListResponse.model_validate(
        {
            "reported_at": NOW,
            "workers": [
                {"worker_name": "worker", "running": True, "last_heartbeat_at": heartbeat},
            ],
        }
    )
    worker = _workers(payload, NOW, 90)[0]
    assert worker["running"] is True
    assert worker["liveness"] == "unknown"
    assert worker["freshness"] in {"missing", "stale"}
