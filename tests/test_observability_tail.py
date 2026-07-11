from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from relayna.metrics import (
    RelaynaMetrics,
    sample_task_resources,
    start_metrics_http_server,
    validate_metric_label_names,
)
from relayna.observability.backpressure import (
    PressureSeverity,
    QueuePressureCollector,
    RuntimePressureService,
    RuntimePressureSignal,
    WorkerHealthPressureCollector,
)
from relayna.observability.log_contract import make_structlog_observation_sink, observation_to_studio_log_fields
from relayna.observability.tracing import _Getter, active_trace_fields, span_trace_fields


def test_metrics_error_recording_resource_and_server_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for labels in (("",), ("task_id",), ("custom",)):
        with pytest.raises(ValueError):
            validate_metric_label_names(labels)

    monkeypatch.setattr("relayna.metrics.time.process_time", lambda: (_ for _ in ()).throw(RuntimeError("cpu")))
    assert sample_task_resources() is None
    monkeypatch.setattr("relayna.metrics.time.process_time", lambda: 1.5)
    import resource

    monkeypatch.setattr(resource, "getrusage", lambda *_args: (_ for _ in ()).throw(RuntimeError("rss")))
    sample = sample_task_resources()
    assert sample is not None and sample.memory_rss_bytes is None

    metrics = RelaynaMetrics(service="tail")
    metrics.record_task_started(stage=None, queue=None, worker_type=None, retry_attempt=0)
    metrics.record_task_finished(
        outcome="failed",
        stage=None,
        queue=None,
        worker_type=None,
        duration_seconds=-1,
    )
    metrics.record_task_retry(stage=None, queue=None, worker_type=None)
    metrics.record_task_dlq(stage=None, queue=None, worker_type=None)
    assert "relayna_tasks_failed_total" in metrics.render().decode()

    sentinel = object()
    monkeypatch.setattr("relayna.metrics.start_http_server", lambda **kwargs: (sentinel, kwargs))
    result, kwargs = start_metrics_http_server(metrics, port=9999, addr="127.0.0.1")
    assert result is sentinel
    assert kwargs["port"] == 9999
    assert sys.platform


@pytest.mark.asyncio
async def test_backpressure_unavailable_metrics_empty_and_worker_state_paths() -> None:
    recorded: list[str] = []
    signal = RuntimePressureSignal(
        scope="test",
        kind="ready",
        severity=PressureSeverity.NORMAL,
    )

    class Collector:
        async def collect(self) -> list[RuntimePressureSignal]:
            return [signal]

    snapshot = await RuntimePressureService(
        collectors=[Collector()],
        metrics_recorder=lambda item: recorded.append(item.kind),
    ).snapshot()
    assert snapshot.signals == [signal]
    assert recorded == ["ready"]

    async def unavailable(_queue: str) -> None:
        return None

    queue_signals = list(
        await QueuePressureCollector(queue_names=["queue", "queue", ""], inspect_queue=unavailable).collect()
    )
    assert queue_signals[0].kind == "queue_inspection_unavailable"

    assert (await WorkerHealthPressureCollector(workers_provider=lambda: []).collect())[0].kind == "worker_count_zero"
    fresh_naive = datetime.now().replace(tzinfo=None)
    workers = [
        SimpleNamespace(worker_name="stale", last_heartbeat_at=None, running=True, active_leases=[]),
        SimpleNamespace(worker_name="stopped", last_heartbeat_at=fresh_naive, running=False, active_leases=[]),
    ]
    worker_signals = list(await WorkerHealthPressureCollector(workers_provider=lambda: workers).collect())
    assert {item.kind for item in worker_signals} == {"worker_heartbeat_stale", "worker_not_running"}


@pytest.mark.asyncio
async def test_log_contract_timestamp_mapping_warning_and_async_logger_paths() -> None:
    now_fields = observation_to_studio_log_fields(
        {"event": "Task Started", "timestamp": None},
        service="service",
        app="app",
    )
    assert now_fields["timestamp"].endswith("Z")
    string_fields = observation_to_studio_log_fields(
        {"event_type": "queue warning", "timestamp": 7},
        service="service",
        app="app",
    )
    assert string_fields["timestamp"] == "7"

    calls: list[tuple[str, dict[str, Any]]] = []

    class Logger:
        async def warning(self, event: str, **kwargs: Any) -> None:
            calls.append((event, kwargs))

        def info(self, event: str, **kwargs: Any) -> None:
            calls.append((event, kwargs))

    sink = make_structlog_observation_sink(Logger(), service="service", app="app")  # type: ignore[arg-type]
    await sink({"event": "queue warning", "timestamp": datetime.now(UTC)})
    assert calls[0][0] == "queue_warning"


def test_tracing_valid_and_invalid_span_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(is_valid=True, trace_id=1, span_id=2)
    span = SimpleNamespace(get_span_context=lambda: context)
    monkeypatch.setattr("relayna.observability.tracing.trace.get_current_span", lambda: span)
    assert active_trace_fields() == {"trace_id": "0" * 31 + "1", "span_id": "0" * 15 + "2"}
    assert span_trace_fields(span)["span_id"].endswith("2")
    assert span_trace_fields(None) == {}
    invalid = SimpleNamespace(get_span_context=lambda: SimpleNamespace(is_valid=False))
    assert span_trace_fields(invalid) == {}
    assert _Getter().keys({1: "value"}) == ["1"]
