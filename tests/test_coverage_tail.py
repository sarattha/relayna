from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from relayna.api.health_routes import create_worker_health_router
from relayna.consumer.task_consumer import _coerce_task_type, _parent_task_id_from_meta, _task_type_from_body
from relayna.contracts import (
    ActionSchema,
    ContractAliasConfig,
    PayloadSchema,
    TerminalStatusSet,
    WorkflowActionSchema,
)
from relayna.contracts.aliases import denormalize_contract_aliases, normalize_contract_aliases
from relayna.contracts.compatibility import ensure_status_event_id, normalize_task_collection
from relayna.topology import (
    RoutedTasksSharedStatusShardedAggregationTopology,
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusTopology,
    WorkflowStage,
)
from relayna.topology.base import (
    ShardRoutingStrategy,
    TaskIdRoutingStrategy,
    TaskTypeRoutingStrategy,
    aggregation_parent_task_id,
)
from relayna.topology.kinds import topology_kind
from relayna.topology.validation import summarize_topology, validate_topology


def base_kwargs() -> dict[str, str]:
    return {
        "rabbitmq_url": "amqp://localhost/",
        "tasks_exchange": "tasks.exchange",
        "tasks_queue": "tasks.queue",
        "tasks_routing_key": "task.route",
        "status_exchange": "status.exchange",
        "status_queue": "status.queue",
    }


def test_contract_and_consumer_normalization_edge_shapes() -> None:
    aliases = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})
    assert normalize_task_collection([{"attempt_id": "task-1"}], aliases) == [
        {"attempt_id": "task-1", "task_id": "task-1"}
    ]

    event = ensure_status_event_id(
        {
            "task_id": "task-1",
            "status": "completed",
            "timestamp": datetime(2026, 7, 11, tzinfo=UTC),
            "opaque": object(),
        }
    )
    assert len(event["event_id"]) == 64

    assert TerminalStatusSet().is_terminal(None) is False
    assert _coerce_task_type([]) is None
    assert _task_type_from_body(None, alias_config=None) is None
    assert _parent_task_id_from_meta(None) is None


def test_alias_schema_routing_and_topology_kind_tail_paths() -> None:
    config = ContractAliasConfig(
        field_aliases={"task_id": " ", "status": "state"},
        http_aliases={"task_id": " "},
    )
    assert config.payload_alias_for("task_id") is None
    assert config.http_alias_for("task_id") is None
    assert denormalize_contract_aliases({"missing": 1}, config) == {"missing": 1}
    assert denormalize_contract_aliases({"status": None}, config) == {"status": None}
    assert normalize_contract_aliases({"task_id": 7}, config)["task_id"] == "7"
    assert normalize_contract_aliases({"state": "done"}, config, source="http")["status"] == "done"
    assert normalize_contract_aliases({"value": 1}, None, source="http") == {"value": 1}

    payload = PayloadSchema(name="payload", required_fields=("required",), optional_fields=("optional",))
    assert payload.validate_payload({"unknown": 1}) == [
        "missing required fields: required",
        "unknown fields: unknown",
    ]
    action = ActionSchema(action="run", payload=payload)
    schema = WorkflowActionSchema(actions=(action,))
    assert schema.for_action("run") == action
    assert schema.for_action("missing") is None
    assert schema.validate_action("missing", {}) == ["unsupported action 'missing'"]
    assert schema.validate_action("run", {"required": 1}) == []

    static = TaskIdRoutingStrategy("tasks")
    assert static.task_routing_key({}) == "tasks"
    try:
        static.status_routing_key({})
    except ValueError:
        pass
    else:
        raise AssertionError("missing task ID must fail")
    shard = ShardRoutingStrategy("tasks", shard_count=0)
    assert shard.shard_count == 1
    assert shard.routing_prefix == "agg"
    try:
        shard.shard_for_event({})
    except ValueError:
        pass
    else:
        raise AssertionError("missing shard key must fail")
    typed = TaskTypeRoutingStrategy()
    try:
        typed.task_routing_key({})
    except ValueError:
        pass
    else:
        raise AssertionError("missing task type must fail")
    assert aggregation_parent_task_id({}) == ""

    shared = SharedTasksSharedStatusTopology(**base_kwargs())
    routed = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://localhost/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("type",),
    )
    sharded = SharedTasksSharedStatusShardedAggregationTopology(**base_kwargs())
    routed_sharded = RoutedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://localhost/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("type",),
    )
    workflow = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://localhost/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(WorkflowStage("stage", "queue", ("route",), "route"),),
    )
    assert topology_kind(workflow) == "shared_status_workflow"
    assert topology_kind(routed_sharded) == "routed_tasks_shared_status_sharded_aggregation"
    assert topology_kind(sharded) == "shared_tasks_shared_status_sharded_aggregation"
    assert topology_kind(routed) == "routed_tasks_shared_status"
    assert topology_kind(shared) == "shared_tasks_shared_status"
    assert topology_kind(SimpleNamespace()) == "SimpleNamespace"


def test_worker_health_sync_and_async_providers() -> None:
    worker = {
        "worker_name": "worker",
        "running": True,
        "active_leases": [],
    }
    sync_app = FastAPI()
    sync_app.include_router(create_worker_health_router(heartbeat_provider=lambda: [worker]))
    assert TestClient(sync_app).get("/relayna/health/workers").json()["workers"][0]["worker_name"] == "worker"

    async def provider() -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return [worker]

    async_app = FastAPI()
    async_app.include_router(create_worker_health_router(heartbeat_provider=provider, prefix="/health"))
    assert TestClient(async_app).get("/health/workers").status_code == 200


def test_topology_validation_and_summary_unreachable_shapes_via_adapters() -> None:
    class BrokenWorkflow(SharedStatusWorkflowTopology):
        def workflow_stage_names(self) -> tuple[str, ...]:
            return ("broken",)

        def workflow_queue_name(self, stage: str) -> str:
            return ""

        def workflow_binding_keys(self, stage: str) -> tuple[str, ...]:
            return ()

        def workflow_stage(self, stage: str) -> WorkflowStage:
            return WorkflowStage("broken", "", (), "", allowed_next_stages=("missing",))

    broken = BrokenWorkflow(
        rabbitmq_url="amqp://localhost/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(WorkflowStage("valid", "queue", ("route",), "route"),),
    )
    errors = validate_topology(broken)
    assert len(errors) == 3

    class TaskSummary:
        prefetch_count = 4

        def status_queue_name(self) -> str:
            return "status"

        def workflow_exchange_name(self) -> None:
            return None

        def task_queue_name(self) -> str:
            return "tasks"

        def task_binding_keys(self) -> tuple[str, ...]:
            return ("route",)

    assert summarize_topology(TaskSummary()) == {
        "kind": "TaskSummary",
        "prefetch_count": 4,
        "status_queue": "status",
        "task_queue": "tasks",
        "task_binding_keys": ["route"],
    }

    class Model(BaseModel):
        task_id: str

    assert TaskIdRoutingStrategy("route").status_routing_key(Model(task_id="task")) == "task"
