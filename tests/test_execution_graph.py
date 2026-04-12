from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import create_execution_router
from relayna.contracts import ContractAliasConfig
from relayna.observability import ExecutionGraph, build_execution_graph, execution_graph_mermaid
from relayna.topology import SharedStatusWorkflowTopology, SharedTasksSharedStatusTopology, WorkflowStage


def test_build_execution_graph_shared_task_includes_retry_and_dlq_nodes() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )

    graph = build_execution_graph(
        topology=topology,
        task_id="task-123",
        status_histories={
            "task-123": [
                {"task_id": "task-123", "status": "processing", "timestamp": "2026-04-06T10:00:01+00:00"},
                {"task_id": "task-123", "status": "failed", "timestamp": "2026-04-06T10:00:03+00:00"},
            ]
        },
        observation_histories={
            "task-123": [
                {
                    "event_type": "TaskMessageReceived",
                    "task_id": "task-123",
                    "queue_name": "tasks.queue",
                    "retry_attempt": 0,
                    "timestamp": "2026-04-06T10:00:00+00:00",
                },
                {
                    "event_type": "ConsumerRetryScheduled",
                    "task_id": "task-123",
                    "queue_name": "tasks.queue.retry",
                    "source_queue_name": "tasks.queue",
                    "retry_attempt": 1,
                    "max_retries": 3,
                    "reason": "handler_error",
                    "timestamp": "2026-04-06T10:00:02+00:00",
                },
                {
                    "event_type": "TaskMessageReceived",
                    "task_id": "task-123",
                    "queue_name": "tasks.queue.retry",
                    "retry_attempt": 1,
                    "timestamp": "2026-04-06T10:00:02.500000+00:00",
                },
                {
                    "event_type": "ConsumerDeadLetterPublished",
                    "task_id": "task-123",
                    "queue_name": "tasks.queue.dlq",
                    "source_queue_name": "tasks.queue.retry",
                    "retry_attempt": 1,
                    "max_retries": 3,
                    "reason": "handler_error",
                    "timestamp": "2026-04-06T10:00:03+00:00",
                },
            ]
        },
        dlq_records={},
    )

    node_kinds = {node.kind for node in graph.nodes}
    edge_kinds = {edge.kind for edge in graph.edges}

    assert graph.topology_kind == "shared_tasks_shared_status"
    assert graph.summary.graph_completeness == "full"
    assert {"task", "task_attempt", "retry", "dlq_record", "status_event"} <= node_kinds
    assert {"received_by", "retried_as", "dead_lettered_to", "published_status"} <= edge_kinds
    assert any(
        edge.kind == "received_by" and edge.source == "retry:task-123:1" and edge.target == "task-attempt:task-123:2"
        for edge in graph.edges
    )


def test_build_execution_graph_workflow_tracks_stage_transitions() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="planner",
                queue="workflow.planner",
                binding_keys=("planner.in",),
                publish_routing_key="planner.in",
            ),
            WorkflowStage(
                name="writer",
                queue="workflow.writer",
                binding_keys=("writer.in",),
                publish_routing_key="writer.in",
                terminal=True,
            ),
        ),
    )

    graph = build_execution_graph(
        topology=topology,
        task_id="task-123",
        status_histories={
            "task-123": [
                {"task_id": "task-123", "status": "planning", "timestamp": "2026-04-06T10:00:01+00:00"},
                {"task_id": "task-123", "status": "completed", "timestamp": "2026-04-06T10:00:03+00:00"},
            ]
        },
        observation_histories={
            "task-123": [
                {
                    "event_type": "WorkflowMessageReceived",
                    "task_id": "task-123",
                    "message_id": "msg-1",
                    "stage": "planner",
                    "queue_name": "workflow.planner",
                    "timestamp": "2026-04-06T10:00:00+00:00",
                },
                {
                    "event_type": "WorkflowMessagePublished",
                    "task_id": "task-123",
                    "message_id": "msg-2",
                    "stage": "writer",
                    "origin_stage": "planner",
                    "queue_name": "workflow.planner",
                    "routing_key": "writer.in",
                    "timestamp": "2026-04-06T10:00:02+00:00",
                },
                {
                    "event_type": "WorkflowMessageReceived",
                    "task_id": "task-123",
                    "message_id": "msg-2",
                    "stage": "writer",
                    "queue_name": "workflow.writer",
                    "timestamp": "2026-04-06T10:00:02.100000+00:00",
                },
            ]
        },
        dlq_records={},
    )

    node_kinds = {node.kind for node in graph.nodes}
    edge_kinds = {edge.kind for edge in graph.edges}

    assert graph.topology_kind == "shared_status_workflow"
    assert {"workflow_message", "stage_attempt", "status_event"} <= node_kinds
    assert {"entered_stage", "stage_transitioned_to", "published_status"} <= edge_kinds


def test_build_execution_graph_returns_partial_when_observations_are_missing() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )

    graph = build_execution_graph(
        topology=topology,
        task_id="task-123",
        status_histories={
            "task-123": [{"task_id": "task-123", "status": "completed", "timestamp": "2026-04-06T10:00:01+00:00"}]
        },
        observation_histories={},
        dlq_records={},
    )

    assert graph.summary.graph_completeness == "partial"
    assert [node.kind for node in graph.nodes] == ["task", "status_event"]


def test_create_execution_router_supports_alias_task_id_path() -> None:
    class FakeExecutionGraphService:
        async def get_graph(self, task_id: str) -> ExecutionGraph | None:
            if task_id != "task-123":
                return None
            return ExecutionGraph(
                task_id=task_id,
                topology_kind="shared_tasks_shared_status",
                summary={"status": "completed", "graph_completeness": "partial"},
                nodes=[],
                edges=[],
                related_task_ids=[],
            )

    app = FastAPI()
    app.include_router(
        create_execution_router(
            execution_graph_service=FakeExecutionGraphService(),  # type: ignore[arg-type]
            alias_config=ContractAliasConfig(
                http_aliases={"task_id": "attempt_id"}, field_aliases={"task_id": "attempt_id"}
            ),
        )
    )

    response = TestClient(app).get("/executions/task-123/graph")

    assert response.status_code == 200
    assert response.json()["attempt_id"] == "task-123"


def test_execution_graph_mermaid_renders_nodes_and_edges() -> None:
    graph = ExecutionGraph(
        task_id="task-123",
        topology_kind="shared_tasks_shared_status",
        summary={"status": "completed", "graph_completeness": "partial"},
        nodes=[
            {"id": "task:task-123", "kind": "task", "label": "task-123"},
            {"id": "status:task-123:1", "kind": "status_event", "label": "completed"},
        ],
        edges=[{"source": "task:task-123", "target": "status:task-123:1", "kind": "published_status"}],
        related_task_ids=[],
    )

    mermaid = execution_graph_mermaid(graph)

    assert "flowchart LR" in mermaid
    assert "published_status" in mermaid
    assert 'node_task_task_123["task-123\\ntask"]' in mermaid

