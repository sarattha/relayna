from __future__ import annotations

from datetime import UTC, datetime

from relayna_studio import (
    build_dlq_view,
    build_execution_view,
    build_run_view,
    build_stage_view,
    build_topology_view,
    create_service_registry_router,
    create_studio_app,
)

from relayna.dlq.models import DLQMessageSummary, DLQQueueSummary, DLQRecordState
from relayna.observability import ExecutionGraph
from relayna.observability.stage_metrics import StageHealthSnapshot
from relayna.topology import build_linear_workflow_topology, topology_kind
from relayna.workflow import WorkflowRunState


def test_backend_package_exports_runtime_surfaces() -> None:
    assert create_service_registry_router is not None
    assert create_studio_app is not None


def test_backend_package_exports_presenter_helpers() -> None:
    assert ExecutionGraph is not None
    assert build_execution_view is not None

    run_state = WorkflowRunState(task_id="task-123")
    run_state.update_stage("planner", status="retrying", message_id="msg-1")
    run_payload = build_run_view(run_state)

    topology = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=("planner", "writer"),
    )
    topology_payload = build_topology_view(topology)

    assert run_payload["task_id"] == "task-123"
    assert run_payload["diagnosis"]
    assert topology_payload["stage_count"] == 2
    assert topology_payload["graph"]["stages"][0]["accepted_actions"] == []


def test_build_execution_view_includes_mermaid_and_graph_payload() -> None:
    topology = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=("planner",),
    )
    graph = ExecutionGraph(
        topology_kind=topology_kind(topology),
        task_id="task-123",
        related_task_ids=["task-456"],
        nodes=[],
        edges=[],
        summary={"status": "completed", "graph_completeness": "partial"},
    )

    payload = build_execution_view(graph)

    assert payload["task_id"] == "task-123"
    assert payload["graph"] == graph.model_dump(mode="json")
    assert "flowchart LR" in payload["mermaid"]


def test_build_dlq_and_stage_views_serialize_presenter_models() -> None:
    timestamp = datetime(2026, 7, 12, tzinfo=UTC)
    queue = DLQQueueSummary(queue_name="payments.dlq", indexed_count=1, exists=True, message_count=2)
    message = DLQMessageSummary(
        dlq_id="dlq-1",
        queue_name="payments.dlq",
        source_queue_name="payments",
        retry_queue_name="payments.retry",
        task_id="task-1",
        reason="timeout",
        retry_attempt=1,
        max_retries=3,
        body_encoding="json",
        dead_lettered_at=timestamp,
        state=DLQRecordState.DEAD_LETTERED,
        replay_count=0,
    )

    dlq_payload = build_dlq_view([queue], [message])
    healthy = build_stage_view(StageHealthSnapshot(stage="charge", received=2, published=2, failed=0))
    unhealthy = build_stage_view(StageHealthSnapshot(stage="charge", received=2, published=1, failed=1))

    assert dlq_payload["queues"][0]["queue_name"] == "payments.dlq"  # type: ignore[index]
    assert dlq_payload["messages"][0]["dead_lettered_at"] == "2026-07-12T00:00:00Z"  # type: ignore[index]
    assert healthy == {"stage": "charge", "received": 2, "published": 2, "failed": 0, "healthy": True}
    assert unhealthy["healthy"] is False
