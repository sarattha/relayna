from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import (
    RUNTIME_CAPABILITY_ROUTE_IDS,
    create_backpressure_router,
    create_capabilities_router,
    merge_capability_route_ids,
)
from relayna.api.health_routes import TaskLeaseHealthSummary, WorkerHeartbeatSummary
from relayna.dlq import DLQQueueSummary
from relayna.metrics import RelaynaMetrics
from relayna.observability import (
    DLQPressureCollector,
    PressureSeverity,
    QueuePressureCollector,
    RuntimePressureService,
    RuntimePressureSignal,
    WorkerHealthPressureCollector,
    severity_for_thresholds,
)
from relayna.rabbitmq import QueueInspection
from relayna.topology import SharedTasksSharedStatusTopology


def make_topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )


def test_pressure_severity_thresholds() -> None:
    assert severity_for_thresholds(None, warning_threshold=1) is PressureSeverity.UNKNOWN
    assert severity_for_thresholds(0, warning_threshold=1, critical_threshold=10) is PressureSeverity.NORMAL
    assert severity_for_thresholds(5, warning_threshold=1, critical_threshold=10) is PressureSeverity.WARNING
    assert severity_for_thresholds(10, warning_threshold=1, critical_threshold=10) is PressureSeverity.CRITICAL


def test_runtime_pressure_service_degrades_failed_collectors() -> None:
    class FailingCollector:
        async def collect(self):
            raise RuntimeError("collector unavailable")

    async def scenario() -> None:
        snapshot = await RuntimePressureService(collectors=[FailingCollector()]).snapshot()
        assert snapshot.signals[0].kind == "pressure_collector_unavailable"
        assert snapshot.signals[0].severity is PressureSeverity.UNKNOWN

    asyncio.run(scenario())


def test_queue_worker_and_dlq_collectors_emit_pressure_signals() -> None:
    async def inspect_queue(queue_name: str) -> QueueInspection | None:
        assert queue_name == "tasks.queue"
        return QueueInspection(queue_name=queue_name, message_count=250, consumer_count=0)

    async def dlq_summaries() -> list[DLQQueueSummary]:
        return [
            DLQQueueSummary(
                queue_name="tasks.queue.dlq",
                indexed_count=3,
                exists=True,
                message_count=3,
            )
        ]

    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    workers = [
        WorkerHeartbeatSummary(
            worker_name="worker-a",
            running=True,
            last_heartbeat_at=datetime.now(UTC),
            active_leases=[
                TaskLeaseHealthSummary(
                    lease_id="task-1",
                    task_id="task-1",
                    owner_id="worker-a",
                    consumer_name="worker-a",
                    heartbeat_at=expired_at,
                    expires_at=expired_at,
                    expired=True,
                )
            ],
        )
    ]

    async def scenario() -> None:
        service = RuntimePressureService(
            collectors=[
                QueuePressureCollector(queue_names=["tasks.queue"], inspect_queue=inspect_queue),
                DLQPressureCollector(summaries_provider=dlq_summaries),
                WorkerHealthPressureCollector(workers_provider=lambda: workers),
            ]
        )
        snapshot = await service.snapshot()
        kinds = {signal.kind for signal in snapshot.signals}
        assert {"queue_depth_high", "consumer_count_zero", "dlq_growth_high", "lease_expiry_rate_high"} <= kinds

    asyncio.run(scenario())


def test_backpressure_route_returns_runtime_pressure_snapshot() -> None:
    service = RuntimePressureService(
        collectors=[
            _StaticCollector(
                [
                    RuntimePressureSignal(
                        scope="queue",
                        scope_id="tasks.queue",
                        kind="queue_depth_high",
                        severity=PressureSeverity.WARNING,
                        value=25,
                        threshold=10,
                    )
                ]
            )
        ]
    )
    app = FastAPI()
    app.include_router(create_backpressure_router(pressure_service=service))

    response = TestClient(app).get("/relayna/runtime/backpressure")

    assert response.status_code == 200
    assert response.json()["signals"][0]["kind"] == "queue_depth_high"
    assert response.json()["signals"][0]["severity"] == "warning"


def test_runtime_backpressure_capability_can_be_advertised() -> None:
    routes = merge_capability_route_ids(RUNTIME_CAPABILITY_ROUTE_IDS)
    app = FastAPI()
    app.include_router(create_capabilities_router(topology=make_topology(), supported_routes=routes))

    response = TestClient(app).get("/relayna/capabilities")

    assert response.status_code == 200
    assert response.json()["supported_routes"] == ["runtime.backpressure"]


def test_pressure_metrics_use_low_cardinality_labels() -> None:
    metrics = RelaynaMetrics(service="payments-api")
    metrics.record_pressure_signal(scope="queue", kind="queue_depth_high", severity="warning", value=25)

    rendered = metrics.render().decode("utf-8")

    assert "relayna_runtime_pressure_signal" in rendered
    assert 'scope="queue"' in rendered
    assert 'kind="queue_depth_high"' in rendered
    assert 'severity="warning"' in rendered
    assert 'task_id="' not in rendered
    assert 'message_id="' not in rendered


class _StaticCollector:
    def __init__(self, signals: list[RuntimePressureSignal]) -> None:
        self._signals = signals

    async def collect(self) -> list[RuntimePressureSignal]:
        return list(self._signals)


def test_runtime_pressure_collectors_run_concurrently_and_preserve_order() -> None:
    started: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    class BlockingCollector:
        def __init__(self, name: str) -> None:
            self.name = name

        async def collect(self) -> list[RuntimePressureSignal]:
            started.append(self.name)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            return [
                RuntimePressureSignal(
                    scope="collector",
                    scope_id=self.name,
                    kind="ready",
                    severity=PressureSeverity.NORMAL,
                )
            ]

    async def scenario() -> None:
        service = RuntimePressureService(
            collectors=[BlockingCollector("first"), BlockingCollector("second")],
            max_concurrency=2,
        )
        snapshot_task = asyncio.create_task(service.snapshot())
        await asyncio.wait_for(both_started.wait(), timeout=1)
        release.set()
        snapshot = await snapshot_task
        assert [signal.scope_id for signal in snapshot.signals] == ["first", "second"]

    asyncio.run(scenario())


def test_queue_pressure_inspection_is_bounded() -> None:
    active = 0
    max_active = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def inspect_queue(queue_name: str) -> QueueInspection:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_started.set()
        try:
            await release.wait()
            return QueueInspection(queue_name=queue_name, message_count=0, consumer_count=1)
        finally:
            active -= 1

    async def scenario() -> None:
        collector = QueuePressureCollector(
            queue_names=["queue-1", "queue-2", "queue-3"],
            inspect_queue=inspect_queue,
            max_concurrency=2,
        )
        collect_task = asyncio.create_task(collector.collect())
        await asyncio.wait_for(two_started.wait(), timeout=1)
        assert max_active == 2
        release.set()
        signals = list(await collect_task)
        assert [signal.scope_id for signal in signals] == ["queue-1", "queue-2", "queue-3"]

    asyncio.run(scenario())


def test_pressure_concurrency_must_be_positive() -> None:
    async def inspect_queue(_name: str) -> None:
        return None

    with pytest.raises(ValueError, match="max_concurrency"):
        RuntimePressureService(max_concurrency=0)
    with pytest.raises(ValueError, match="max_concurrency"):
        QueuePressureCollector(queue_names=[], inspect_queue=inspect_queue, max_concurrency=0)
