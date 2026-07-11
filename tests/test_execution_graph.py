from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import create_execution_router
from relayna.contracts import ContractAliasConfig
from relayna.observability import ExecutionGraph, ExecutionGraphService, build_execution_graph, execution_graph_mermaid
from relayna.observability.execution_graph import (
    ExecutionGraphEdge,
    ExecutionGraphNode,
    _build_mermaid_node_ids,
    _edge_state,
    _latest_attempt,
    _latest_stage_attempt,
    _latest_timestamp,
    _next_attempt,
    _state_from_status,
)
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
    assert graph.summary.live_state_counts["dead_lettered"] >= 1
    assert graph.summary.live_state_counts["retrying"] >= 1
    assert graph.summary.live_state_counts["failed"] >= 1
    assert any(
        edge.kind == "received_by" and edge.source == "retry:task-123:1" and edge.target == "task-attempt:task-123:2"
        for edge in graph.edges
    )
    retry_node = next(node for node in graph.nodes if node.kind == "retry")
    dlq_node = next(node for node in graph.nodes if node.kind == "dlq_record")
    retry_edge = next(edge for edge in graph.edges if edge.kind == "retried_as")
    dlq_edge = next(edge for edge in graph.edges if edge.kind == "dead_lettered_to")
    assert retry_node.state == "retrying"
    assert retry_node.state_reason == "retry_scheduled"
    assert dlq_node.state == "dead_lettered"
    assert dlq_node.state_reason == "dlq_recorded"
    assert retry_edge.state == "retry_path"
    assert dlq_edge.state == "blocked"


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
    assert graph.summary.live_state_counts["succeeded"] >= 1
    stage_attempt = next(node for node in graph.nodes if node.kind == "stage_attempt" and "writer" in node.id)
    transition_edge = next(edge for edge in graph.edges if edge.kind == "stage_transitioned_to")
    assert stage_attempt.state == "succeeded"
    assert transition_edge.state == "traversed"


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
    assert graph.summary.live_state_counts == {"succeeded": 2}
    assert [node.kind for node in graph.nodes] == ["task", "status_event"]


def test_build_execution_graph_marks_replayed_dlq_records() -> None:
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
        status_histories={},
        observation_histories={},
        dlq_records={
            "task-123": [
                {
                    "dlq_id": "dlq-1",
                    "queue_name": "tasks.queue.dlq",
                    "source_queue_name": "tasks.queue",
                    "retry_queue_name": "tasks.queue.retry",
                    "task_id": "task-123",
                    "reason": "handler_error",
                    "retry_attempt": 3,
                    "max_retries": 3,
                    "body_encoding": "json",
                    "dead_lettered_at": "2026-04-06T10:00:03+00:00",
                    "state": "replayed",
                    "replayed_at": "2026-04-06T10:05:00+00:00",
                }
            ]
        },
    )

    dlq_node = next(node for node in graph.nodes if node.kind == "dlq_record")
    assert dlq_node.state == "replayed"
    assert dlq_node.state_reason == "dlq_replayed"
    assert dlq_node.updated_at == "2026-04-06T10:05:00+00:00"
    assert graph.summary.live_state_counts["replayed"] == 1


def test_create_execution_router_supports_alias_task_id_path() -> None:
    class FakeExecutionGraphService:
        async def get_graph(self, task_id: str) -> ExecutionGraph | None:
            if task_id != "task-123":
                return None
            return ExecutionGraph(
                task_id=task_id,
                topology_kind="shared_tasks_shared_status",
                summary={
                    "status": "completed",
                    "graph_completeness": "partial",
                    "live_state_counts": {"succeeded": 1},
                },
                nodes=[
                    {
                        "id": "task:task-123",
                        "kind": "task",
                        "state": "succeeded",
                        "state_reason": "latest_status:completed",
                        "updated_at": "2026-04-06T10:00:01+00:00",
                    }
                ],
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
    assert response.json()["summary"]["live_state_counts"] == {"succeeded": 1}
    assert response.json()["nodes"][0]["state"] == "succeeded"


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


def test_execution_graph_service_loads_related_histories_concurrently() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    active = 0
    max_active = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    class StatusStore:
        async def get_child_task_ids(self, parent_task_id: str) -> list[str]:
            assert parent_task_id == "task-root"
            return ["task-child-1", "task-child-2"]

        async def get_history(self, task_id: str) -> list[dict[str, str]]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                two_started.set()
            try:
                await release.wait()
                return [{"task_id": task_id, "status": "completed"}]
            finally:
                active -= 1

    async def scenario() -> None:
        service = ExecutionGraphService(
            topology=topology,
            status_store=StatusStore(),  # type: ignore[arg-type]
            max_concurrency=2,
        )
        graph_task = asyncio.create_task(service.get_graph("task-root"))
        await asyncio.wait_for(two_started.wait(), timeout=1)
        assert max_active == 2
        release.set()
        graph = await graph_task
        assert graph is not None
        assert graph.related_task_ids == ["task-child-1", "task-child-2"]

    asyncio.run(scenario())


def test_execution_graph_service_observations_dlq_and_empty_paths() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )

    class StatusStore:
        def __init__(self, populated: bool) -> None:
            self.populated = populated

        async def get_child_task_ids(self, task_id: str) -> list[str]:
            return [task_id, "child"] if self.populated else []

        async def get_history(self, task_id: str) -> list[dict[str, str]]:
            return [{"task_id": task_id, "status": "completed"}] if self.populated else []

    class ObservationStore:
        async def get_history(self, task_id: str) -> list[dict[str, str]]:
            return [{"event_type": "TaskMessageReceived", "task_id": task_id}] if task_id == "task" else []

    class Item:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"reason": "failed"}

    class DLQ:
        async def list_messages(self, *, task_id: str, limit: int):
            assert limit == 200
            return type("Payload", (), {"items": [Item()] if task_id == "task" else []})()

    async def scenario() -> None:
        empty = ExecutionGraphService(topology=topology, status_store=StatusStore(False))  # type: ignore[arg-type]
        assert await empty.get_graph("task") is None
        service = ExecutionGraphService(
            topology=topology,
            status_store=StatusStore(True),  # type: ignore[arg-type]
            observation_store=ObservationStore(),  # type: ignore[arg-type]
            dlq_service=DLQ(),  # type: ignore[arg-type]
        )
        graph = await service.get_graph("task")
        assert graph is not None
        assert "child" in graph.related_task_ids
        assert any(node.kind == "dlq_record" for node in graph.nodes)

    asyncio.run(scenario())


def test_execution_graph_rare_events_and_internal_projection_helpers() -> None:
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
        task_id="task",
        related_task_ids=["task", "", "child"],
        status_histories={
            "task": [
                {"task_id": "task", "status": "manual_retrying", "timestamp": "2026-01-01T00:00:01Z"},
                {"task_id": "task", "status": "expired", "timestamp": "2026-01-01T00:00:04Z"},
            ]
        },
        observation_histories={
            "task": [
                {
                    "event_type": "TaskMessageReceived",
                    "task_id": "task",
                    "task_type": "type.a",
                    "timestamp": "2026-01-01T00:00:00Z",
                },
                {
                    "event_type": "TaskResourceSampled",
                    "task_id": "task",
                    "sample_kind": "",
                    "timestamp": "2026-01-01T00:00:00.500000Z",
                },
                {
                    "event_type": "ConsumerRetryScheduled",
                    "task_id": "task",
                    "timestamp": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "TaskMessageReceived",
                    "task_id": "task",
                    "timestamp": "2026-01-01T00:00:02Z",
                },
                {"event_type": "WorkflowMessagePublished", "task_id": "task", "message_id": ""},
                {
                    "event_type": "WorkflowMessagePublished",
                    "task_id": "task",
                    "message_id": "message",
                    "stage": "planner",
                    "timestamp": "2026-01-01T00:00:02.500000Z",
                },
                {
                    "event_type": "WorkflowMessageReceived",
                    "task_id": "task",
                    "message_id": "",
                    "stage": "planner",
                    "timestamp": "2026-01-01T00:00:03Z",
                },
                {
                    "event_type": "ConsumerRetryScheduled",
                    "task_id": "task",
                    "timestamp": "2026-01-01T00:00:05Z",
                },
            ]
        },
        dlq_records={"task": [{"reason": "manual", "dead_lettered_at": "2026-01-01T00:00:06Z"}]},
    )
    assert graph.annotations == {"task_types": ["type.a"]}
    assert any(node.kind == "resource_sample" for node in graph.nodes)
    assert any(edge.kind == "manual_retry_to" for edge in graph.edges)
    assert graph.summary.status == "expired"

    assert _state_from_status("processing") == "running"
    assert _state_from_status("retrying") == "retrying"
    assert _state_from_status("success") == "succeeded"
    assert _state_from_status("dlq") == "dead_lettered"
    assert _state_from_status("error") == "failed"
    assert _state_from_status("lease_expired") == "expired"
    assert _state_from_status("waiting") == "queued"
    assert _state_from_status("blocked") == "blocked"
    assert _state_from_status("custom") == "unknown"

    attempts = [
        ExecutionGraphNode(id="one", kind="task_attempt", timestamp="2026-01-01T00:00:00Z"),
        ExecutionGraphNode(id="two", kind="task_attempt", timestamp="2026-01-01T00:00:02Z"),
    ]
    assert _latest_attempt([], None) is None
    assert _latest_attempt(attempts, None) == attempts[-1]
    assert _latest_attempt(attempts, "2025-01-01") == attempts[0]
    assert _next_attempt(attempts, None) is None
    assert _next_attempt(attempts, "2027-01-01") is None
    assert _latest_stage_attempt(attempts, "missing", None) is None
    assert _latest_timestamp("2026-01-01", None) == "2026-01-01"
    assert _edge_state(ExecutionGraphEdge(source="a", target="b", kind="replayed_as"))[0] == "replay_path"
    assert _edge_state(ExecutionGraphEdge(source="a", target="b", kind="custom"))[0] == "unknown"
    assert len(set(_build_mermaid_node_ids(["a-b", "a_b", ""], prefix="node").values())) == 3

    dangling = ExecutionGraph(
        task_id="task",
        topology_kind="test",
        summary={},
        nodes=[{"id": "one", "kind": "task", "label": 'quoted "label"', "timestamp": "2026-01-01"}],
        edges=[{"source": "missing", "target": "one", "kind": "ignored"}],
    )
    assert "ignored" not in execution_graph_mermaid(dangling)
