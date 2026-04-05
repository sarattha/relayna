from relayna.api import create_replay_router, create_workflow_router
from relayna.mcp import RelaynaMCPServer, inspect_topology
from relayna.studio import build_run_view, build_topology_view
from relayna.topology import (
    SharedStatusWorkflowTopology,
    WorkflowEntryRoute,
    WorkflowStage,
    build_linear_workflow_topology,
    export_workflow_graph,
    workflow_graph_mermaid,
)
from relayna.workflow import WorkflowRunState


def test_workflow_template_and_graph_export() -> None:
    topology = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=("planner", "writer"),
    )

    graph = export_workflow_graph(topology)

    assert graph["workflow_exchange"] == "workflow.exchange"
    assert [stage["id"] for stage in graph["stages"]] == ["planner", "writer"]


def test_studio_run_view_includes_diagnosis() -> None:
    run_state = WorkflowRunState(task_id="task-123")
    run_state.update_stage("planner", status="retrying", message_id="msg-1")

    payload = build_run_view(run_state)

    assert payload["task_id"] == "task-123"
    assert payload["current_stage"] == "planner"
    assert payload["diagnosis"]


def test_mcp_server_lists_tools_and_topology_resource() -> None:
    topology = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=("planner",),
    )
    server = RelaynaMCPServer(read_tools={"inspect_topology": inspect_topology})

    tools = server.list_tools()
    resources = server.list_resources(topology=topology)

    assert "inspect_topology" in tools["read"]
    assert resources[0]["type"] == "topology"


def test_api_exports_new_route_factories() -> None:
    assert create_workflow_router is not None
    assert create_replay_router is not None


def test_studio_topology_view_counts_graph_items() -> None:
    topology = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=("planner", "writer"),
    )

    payload = build_topology_view(topology)

    assert payload["stage_count"] == 2


def test_workflow_graph_mermaid_sanitizes_and_uniquifies_stage_node_ids() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="writer-stage",
                queue="workflow.writer-stage",
                binding_keys=("writer-stage.in",),
                publish_routing_key="writer-stage.in",
            ),
            WorkflowStage(
                name="writer.stage",
                queue="workflow.writer.stage",
                binding_keys=("writer.stage.in",),
                publish_routing_key="writer.stage.in",
            ),
        ),
        entry_routes=(
            WorkflowEntryRoute(
                name="start-route",
                routing_key="writer.stage.in",
                target_stage="writer.stage",
            ),
        ),
    )

    graph = workflow_graph_mermaid(topology)

    assert 'stage_writer_stage["writer-stage\\nworkflow.writer-stage"]' in graph
    assert 'stage_writer_stage_2["writer.stage\\nworkflow.writer.stage"]' in graph
    assert 'entry_start_route["start-route\\nwriter.stage.in"] --> stage_writer_stage_2' in graph
    assert "stage_writer_stage -->|writer-stage.in| stage_writer_stage" in graph
    assert "stage_writer_stage_2 -->|writer.stage.in| stage_writer_stage_2" in graph
