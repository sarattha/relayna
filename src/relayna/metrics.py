from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

ALLOWED_METRIC_LABELS = frozenset({"service", "stage", "queue", "status", "worker_type"})
HIGH_CARDINALITY_METRIC_LABELS = frozenset(
    {"task_id", "correlation_id", "request_id", "worker_id", "pod", "pod_name", "container", "message_id"}
)


@dataclass(frozen=True, slots=True)
class TaskResourceSample:
    cpu_process_seconds: float
    memory_rss_bytes: int | None


def validate_metric_label_names(label_names: set[str] | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(item).strip() for item in label_names)
    for label in normalized:
        if not label:
            raise ValueError("metric label names must not be empty")
        if label in HIGH_CARDINALITY_METRIC_LABELS:
            raise ValueError(f"metric label '{label}' is too high-cardinality for Prometheus")
        if label not in ALLOWED_METRIC_LABELS:
            raise ValueError(f"metric label '{label}' is not in the Relayna low-cardinality allowlist")
    return normalized


def sample_task_resources() -> TaskResourceSample | None:
    try:
        cpu_seconds = time.process_time()
    except Exception:
        return None
    memory_rss_bytes: int | None = None
    try:
        import resource

        raw_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        memory_rss_bytes = raw_rss if sys.platform == "darwin" else raw_rss * 1024
    except Exception:
        memory_rss_bytes = None
    return TaskResourceSample(cpu_process_seconds=cpu_seconds, memory_rss_bytes=memory_rss_bytes)


class RelaynaMetrics:
    def __init__(self, *, service: str = "relayna", registry: CollectorRegistry | None = None) -> None:
        self.service = service.strip() or "relayna"
        self.registry = registry or CollectorRegistry()
        common_labels = ("service", "stage", "queue", "status", "worker_type")
        validate_metric_label_names(common_labels)
        self.tasks_started = Counter(
            "relayna_tasks_started_total",
            "Relayna tasks started.",
            common_labels,
            registry=self.registry,
        )
        self.tasks_completed = Counter(
            "relayna_tasks_completed_total",
            "Relayna tasks completed.",
            common_labels,
            registry=self.registry,
        )
        self.tasks_failed = Counter(
            "relayna_tasks_failed_total",
            "Relayna tasks failed.",
            common_labels,
            registry=self.registry,
        )
        self.tasks_retried = Counter(
            "relayna_tasks_retried_total",
            "Relayna tasks retried.",
            common_labels,
            registry=self.registry,
        )
        self.tasks_dlq = Counter(
            "relayna_tasks_dlq_total",
            "Relayna tasks dead-lettered.",
            common_labels,
            registry=self.registry,
        )
        self.task_duration = Histogram(
            "relayna_task_duration_seconds",
            "Relayna task handler duration.",
            common_labels,
            registry=self.registry,
        )
        self.task_attempts = Gauge(
            "relayna_task_attempts",
            "Relayna latest task attempt number.",
            common_labels,
            registry=self.registry,
        )
        self.worker_active_tasks = Gauge(
            "relayna_worker_active_tasks",
            "Relayna active tasks by worker type.",
            ("service", "stage", "queue", "worker_type"),
            registry=self.registry,
        )
        self.worker_heartbeat = Gauge(
            "relayna_worker_heartbeat_timestamp",
            "Relayna worker heartbeat Unix timestamp.",
            ("service", "stage", "queue", "worker_type"),
            registry=self.registry,
        )
        self.queue_publish = Counter(
            "relayna_queue_publish_total",
            "Relayna queue publish operations.",
            ("service", "stage", "queue", "status", "worker_type"),
            registry=self.registry,
        )
        self.status_events_published = Counter(
            "relayna_status_events_published_total",
            "Relayna status events published.",
            ("service", "stage", "queue", "status", "worker_type"),
            registry=self.registry,
        )
        self.observation_events = Counter(
            "relayna_observation_events_total",
            "Relayna observation events emitted.",
            ("service", "stage", "queue", "status", "worker_type"),
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)

    def labels(
        self,
        *,
        stage: str | None = None,
        queue: str | None = None,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> dict[str, str]:
        return {
            "service": self.service,
            "stage": _label(stage),
            "queue": _label(queue),
            "status": _label(status),
            "worker_type": _label(worker_type),
        }

    def worker_labels(
        self,
        *,
        stage: str | None = None,
        queue: str | None = None,
        worker_type: str | None = None,
    ) -> dict[str, str]:
        return {
            "service": self.service,
            "stage": _label(stage),
            "queue": _label(queue),
            "worker_type": _label(worker_type),
        }

    def record_task_started(
        self,
        *,
        stage: str | None,
        queue: str | None,
        worker_type: str | None,
        retry_attempt: int = 0,
    ) -> None:
        labels = self.labels(stage=stage, queue=queue, status="started", worker_type=worker_type)
        self.tasks_started.labels(**labels).inc()
        self.task_attempts.labels(**labels).set(max(0, retry_attempt))
        self.worker_active_tasks.labels(**self.worker_labels(stage=stage, queue=queue, worker_type=worker_type)).inc()
        self.record_worker_heartbeat(stage=stage, queue=queue, worker_type=worker_type)

    def record_task_finished(
        self,
        *,
        outcome: Literal["completed", "failed"],
        stage: str | None,
        queue: str | None,
        worker_type: str | None,
        duration_seconds: float,
    ) -> None:
        labels = self.labels(stage=stage, queue=queue, status=outcome, worker_type=worker_type)
        if outcome == "completed":
            self.tasks_completed.labels(**labels).inc()
        else:
            self.tasks_failed.labels(**labels).inc()
        self.task_duration.labels(**labels).observe(max(0.0, duration_seconds))
        self.worker_active_tasks.labels(**self.worker_labels(stage=stage, queue=queue, worker_type=worker_type)).dec()
        self.record_worker_heartbeat(stage=stage, queue=queue, worker_type=worker_type)

    def record_task_retry(self, *, stage: str | None, queue: str | None, worker_type: str | None) -> None:
        self.tasks_retried.labels(
            **self.labels(stage=stage, queue=queue, status="retrying", worker_type=worker_type)
        ).inc()

    def record_task_dlq(self, *, stage: str | None, queue: str | None, worker_type: str | None) -> None:
        self.tasks_dlq.labels(
            **self.labels(stage=stage, queue=queue, status="dead_lettered", worker_type=worker_type)
        ).inc()

    def record_queue_publish(
        self,
        *,
        stage: str | None = None,
        queue: str | None = None,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> None:
        self.queue_publish.labels(
            **self.labels(stage=stage, queue=queue, status=status or "published", worker_type=worker_type)
        ).inc()

    def record_status_event(
        self,
        *,
        stage: str | None = None,
        queue: str | None = None,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> None:
        self.status_events_published.labels(
            **self.labels(stage=stage, queue=queue, status=status or "published", worker_type=worker_type)
        ).inc()

    def record_observation_event(
        self,
        *,
        stage: str | None = None,
        queue: str | None = None,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> None:
        self.observation_events.labels(
            **self.labels(stage=stage, queue=queue, status=status or "emitted", worker_type=worker_type)
        ).inc()

    def record_worker_heartbeat(self, *, stage: str | None, queue: str | None, worker_type: str | None) -> None:
        self.worker_heartbeat.labels(**self.worker_labels(stage=stage, queue=queue, worker_type=worker_type)).set(
            time.time()
        )


def create_metrics_router(metrics: RelaynaMetrics, *, prefix: str = "") -> APIRouter:
    router = APIRouter()
    path = f"{prefix.rstrip('/')}/metrics" if prefix else "/metrics"

    @router.get(path, include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)

    return router


def start_metrics_http_server(metrics: RelaynaMetrics, *, port: int = 8001, addr: str = "0.0.0.0") -> Any:
    return start_http_server(port=port, addr=addr, registry=metrics.registry)


def _label(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or "none"


__all__ = [
    "ALLOWED_METRIC_LABELS",
    "HIGH_CARDINALITY_METRIC_LABELS",
    "RelaynaMetrics",
    "TaskResourceSample",
    "create_metrics_router",
    "sample_task_resources",
    "start_metrics_http_server",
    "validate_metric_label_names",
]
