from fastapi import FastAPI
from fastapi.testclient import TestClient

from relayna.api import create_execution_router, create_replay_router, create_workflow_router
from relayna.contracts import ActionSchema, PayloadSchema
from relayna.mcp import RelaynaMCPServer, inspect_topology
from relayna.observability import ExecutionGraph, build_execution_graph
from relayna.studio import build_run_view, build_topology_view, create_service_registry_router, create_studio_app
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


def test_workflow_graph_export_includes_stage_contract_metadata() -> None:
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
                description="Plans the workflow",
                role="planner",
                owner="ops",
                tags=("core", "entry"),
                sla_ms=5000,
                accepted_actions=(
                    ActionSchema(
                        action="plan",
                        payload=PayloadSchema(name="plan_payload", required_fields=("query",)),
                    ),
                ),
                produced_actions=(
                    ActionSchema(
                        action="draft",
                        payload=PayloadSchema(name="draft_payload", required_fields=("outline",)),
                    ),
                ),
                allowed_next_stages=("writer",),
                timeout_seconds=2.5,
                max_retries=4,
                retry_delay_ms=1500,
                max_inflight=8,
                dedup_key_fields=("query",),
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

    graph = export_workflow_graph(topology)

    planner = graph["stages"][0]
    assert planner["description"] == "Plans the workflow"
    assert planner["accepted_actions"][0]["action"] == "plan"
    assert planner["allowed_next_stages"] == ["writer"]
    assert planner["max_inflight"] == 8
    assert planner["dedup_key_fields"] == ["query"]


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


def test_inspect_topology_includes_stage_contract_metadata() -> None:
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
                role="planner",
                terminal=False,
                max_inflight=4,
            ),
        ),
    )

    payload = inspect_topology(topology)

    assert payload["stages"][0]["role"] == "planner"
    assert payload["stages"][0]["max_inflight"] == 4


def test_api_exports_new_route_factories() -> None:
    assert create_execution_router is not None
    assert create_workflow_router is not None
    assert create_replay_router is not None


def test_studio_exports_registry_backend_surfaces() -> None:
    assert create_service_registry_router is not None
    assert create_studio_app is not None


def test_observability_exports_execution_graph_models() -> None:
    assert ExecutionGraph is not None
    assert build_execution_graph is not None


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
    assert payload["graph"]["stages"][0]["accepted_actions"] == []


def test_workflow_routes_expose_stage_contract_metadata() -> None:
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
                role="planner",
                max_inflight=6,
                accepted_actions=(
                    ActionSchema(
                        action="plan",
                        payload=PayloadSchema(name="plan_payload", required_fields=("query",)),
                    ),
                ),
            ),
        ),
    )
    app = FastAPI()
    app.include_router(create_workflow_router(topology=topology))
    client = TestClient(app)

    stages_response = client.get("/workflow/stages")
    topology_response = client.get("/workflow/topology")

    assert stages_response.status_code == 200
    assert stages_response.json()["stages"][0]["max_inflight"] == 6
    assert stages_response.json()["stages"][0]["accepted_actions"][0]["action"] == "plan"
    assert topology_response.status_code == 200
    assert topology_response.json()["stages"][0]["role"] == "planner"


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
