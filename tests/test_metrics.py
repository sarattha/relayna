from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.metrics import RelaynaMetrics, create_metrics_router, validate_metric_label_names


def test_relayna_metrics_exports_expected_names() -> None:
    metrics = RelaynaMetrics(service="payments-api")

    metrics.record_task_started(stage="worker", queue="payments", worker_type="task", retry_attempt=1)
    metrics.record_task_finished(
        outcome="completed",
        stage="worker",
        queue="payments",
        worker_type="task",
        duration_seconds=0.25,
    )
    metrics.record_queue_publish(queue="payments", status="task", worker_type="api")
    metrics.record_status_event(status="completed", worker_type="task")
    metrics.record_observation_event(status="TaskResourceSampled", worker_type="task")
    rendered = metrics.render().decode("utf-8")

    for name in [
        "relayna_tasks_started_total",
        "relayna_tasks_completed_total",
        "relayna_task_duration_seconds",
        "relayna_task_attempts",
        "relayna_worker_active_tasks",
        "relayna_worker_heartbeat_timestamp",
        "relayna_queue_publish_total",
        "relayna_status_events_published_total",
        "relayna_observation_events_total",
    ]:
        assert name in rendered
    assert 'task_id="' not in rendered


def test_duplicate_relayna_metrics_instances_do_not_conflict() -> None:
    first = RelaynaMetrics(service="first")
    second = RelaynaMetrics(service="second")

    assert "relayna_tasks_started_total" in first.render().decode("utf-8")
    assert "relayna_tasks_started_total" in second.render().decode("utf-8")


def test_metric_label_guardrails_reject_high_cardinality_labels() -> None:
    try:
        validate_metric_label_names(["service", "task_id"])
    except ValueError as exc:
        assert "high-cardinality" in str(exc)
    else:
        raise AssertionError("task_id label should be rejected")


def test_metrics_router_exposes_prometheus_text() -> None:
    metrics = RelaynaMetrics(service="payments-api")
    app = FastAPI()
    app.include_router(create_metrics_router(metrics))

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "relayna_tasks_started_total" in response.text
    assert response.headers["content-type"].startswith("text/plain")
