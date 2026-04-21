from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import relayna
from relayna.api import (
    BROKER_DLQ_CAPABILITY_ROUTE_IDS,
    STATUS_CAPABILITY_ROUTE_IDS,
    WORKFLOW_CAPABILITY_ROUTE_IDS,
    create_capabilities_router,
    merge_capability_route_ids,
)
from relayna.contracts import ContractAliasConfig
from relayna.topology import SharedStatusWorkflowTopology, SharedTasksSharedStatusTopology, WorkflowStage


def test_capabilities_route_returns_version_topology_alias_summary_and_declared_routes() -> None:
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
        ),
    )
    alias_config = ContractAliasConfig(
        field_aliases={"task_id": "attempt_id"},
        http_aliases={"task_id": "attemptId"},
    )
    supported_routes = merge_capability_route_ids(STATUS_CAPABILITY_ROUTE_IDS, WORKFLOW_CAPABILITY_ROUTE_IDS)
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            topology=topology,
            supported_routes=supported_routes,
            alias_config=alias_config,
            service_title="Planner API",
        )
    )
    client = TestClient(app)

    response = client.get("/relayna/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "relayna_version": relayna.__version__,
        "topology_kind": "shared_status_workflow",
        "alias_config_summary": {
            "aliasing_enabled": True,
            "payload_aliases": {"task_id": "attempt_id"},
            "http_aliases": {"task_id": "attemptId"},
        },
        "supported_routes": list(supported_routes),
        "feature_flags": [],
        "service_metadata": {
            "service_title": "Planner API",
            "capability_path": "/relayna/capabilities",
            "discovery_source": "live",
            "compatibility": "capabilities_v1",
        },
    }


def test_capabilities_route_support_is_explicit_and_defaults_feature_flags_to_empty() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            topology=topology,
            supported_routes=STATUS_CAPABILITY_ROUTE_IDS,
        )
    )
    client = TestClient(app)

    response = client.get("/relayna/capabilities")

    assert response.status_code == 200
    assert response.json()["topology_kind"] == "shared_tasks_shared_status"
    assert response.json()["supported_routes"] == list(STATUS_CAPABILITY_ROUTE_IDS)
    assert "workflow.topology" not in response.json()["supported_routes"]
    assert "execution.graph" not in response.json()["supported_routes"]
    assert "dlq.messages" not in response.json()["supported_routes"]
    assert response.json()["feature_flags"] == []
    assert response.json()["alias_config_summary"] == {
        "aliasing_enabled": False,
        "payload_aliases": {},
        "http_aliases": {},
    }


def test_capabilities_route_snapshots_one_shot_iterables() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            topology=topology,
            supported_routes=iter(STATUS_CAPABILITY_ROUTE_IDS),
            feature_flags=iter(["federated_reads"]),
        )
    )
    client = TestClient(app)

    first = client.get("/relayna/capabilities")
    second = client.get("/relayna/capabilities")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["supported_routes"] == list(STATUS_CAPABILITY_ROUTE_IDS)
    assert second.json()["supported_routes"] == list(STATUS_CAPABILITY_ROUTE_IDS)
    assert first.json()["feature_flags"] == ["federated_reads"]
    assert second.json()["feature_flags"] == ["federated_reads"]


def test_capabilities_route_can_explicitly_advertise_broker_dlq_messages_when_configured() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            topology=topology,
            supported_routes=merge_capability_route_ids(STATUS_CAPABILITY_ROUTE_IDS, BROKER_DLQ_CAPABILITY_ROUTE_IDS),
        )
    )
    client = TestClient(app)

    response = client.get("/relayna/capabilities")

    assert response.status_code == 200
    assert "broker.dlq.messages" in response.json()["supported_routes"]
