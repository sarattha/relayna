from __future__ import annotations

from relayna_studio import (
    build_execution_view,
    build_run_view,
    build_topology_view,
    create_service_registry_router,
    create_studio_app,
)

from relayna.observability import ExecutionGraph
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
