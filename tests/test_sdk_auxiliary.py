from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from relayna.consumer._retry_decision import decide_static_retry
from relayna.consumer.context import FailureAction, RetryPolicy
from relayna.consumer.idempotency import InMemoryIdempotencyBackend
from relayna.consumer.middleware import MiddlewareChain
from relayna.contracts import ActionSchema
from relayna.mcp.server import RelaynaMCPServer
from relayna.mcp.tools_ops import replay_dlq, resume_workflow
from relayna.mcp.tools_read import explain_workflow, inspect_topology, list_dlq_messages
from relayna.observability.alerts import detect_stage_alerts
from relayna.observability.collectors import AsyncQueueObservationCollector, MemoryObservationCollector
from relayna.observability.exporters import event_to_dict, make_logging_sink
from relayna.observability.stage_metrics import compute_stage_health
from relayna.observability.task_timeline import build_task_timeline
from relayna.policies import RetryDecisionAction, RetryDecisionContext
from relayna.rabbitmq.declarations import ensure_all, ensure_status_queue, ensure_tasks_queue
from relayna.rabbitmq.publisher import (
    publish_status,
    publish_task,
    publish_to_entry,
    publish_to_stage,
    publish_workflow,
)
from relayna.rabbitmq.retry import clear_retry_headers
from relayna.rabbitmq.routing import resolve_status_routing_key, resolve_task_routing_key, resolve_workflow_queue
from relayna.storage.redis_models import fanin_key, run_state_key
from relayna.storage.retention import clamp_ttl_seconds
from relayna.topology import SharedStatusWorkflowTopology, SharedTasksSharedStatusTopology, WorkflowStage
from relayna.topology.validation import summarize_topology, validate_topology
from relayna.topology.workflow_templates import (
    build_linear_workflow_topology,
    build_search_aggregate_workflow_topology,
)
from relayna.workflow.actions import WorkflowAction, validate_action_payload
from relayna.workflow.diagnostics import explain_stall
from relayna.workflow.fanin import FanInProgress, update_fanin_progress
from relayna.workflow.policies import StagePolicy
from relayna.workflow.replay import ReplayRequest
from relayna.workflow.run_state import WorkflowRunState
from relayna.workflow.stage_registry import StageMetadata, StageRegistry
from relayna.workflow.transitions import TransitionRule, validate_transition


def make_workflow_topology() -> SharedStatusWorkflowTopology:
    return SharedStatusWorkflowTopology(
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
                allowed_next_stages=("writer",),
                accepted_actions=(ActionSchema(action="plan"),),
                description="Plan",
                owner="team-a",
                tags=("entry",),
                max_retries=2,
                max_inflight=3,
                dedup_key_fields=("request_id",),
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


@pytest.mark.asyncio
async def test_idempotency_and_middleware_helpers() -> None:
    backend = InMemoryIdempotencyBackend()
    assert await backend.acquire("key") is True
    assert await backend.acquire("key") is False
    await backend.release("key")
    assert await backend.acquire("key") is True

    calls: list[str] = []

    class Middleware:
        def __init__(self, name: str) -> None:
            self.name = name

        async def before_handle(self, payload: object, context: object) -> None:
            calls.append(f"before:{self.name}:{payload}:{context}")

        async def after_handle(self, payload: object, context: object) -> None:
            calls.append(f"after:{self.name}:{payload}:{context}")

        async def on_error(self, payload: object, context: object, exc: Exception) -> None:
            calls.append(f"error:{self.name}:{exc}")

    chain = MiddlewareChain([Middleware("one"), Middleware("two")])
    await chain.before_handle("payload", "context")
    await chain.after_handle("payload", "context")
    await chain.on_error("payload", "context", RuntimeError("boom"))
    assert calls == [
        "before:one:payload:context",
        "before:two:payload:context",
        "after:two:payload:context",
        "after:one:payload:context",
        "error:two:boom",
        "error:one:boom",
    ]


def test_retry_decision_adapter_uses_policy_and_failure_action() -> None:
    context = RetryDecisionContext(
        worker_type="task",
        queue_name="tasks.queue",
        retry_attempt=0,
        reason="handler_error",
        task_id="task-1",
    )
    decision = decide_static_retry(context, retry_policy=RetryPolicy(max_retries=2, delay_ms=10))
    assert decision.action is RetryDecisionAction.RETRY
    rejected = decide_static_retry(context, retry_policy=None, failure_action=FailureAction.REQUEUE)
    assert rejected.action is RetryDecisionAction.REQUEUE


@pytest.mark.asyncio
async def test_observation_collectors_exporters_metrics_and_timeline(caplog: pytest.LogCaptureFixture) -> None:
    @dataclass
    class WorkflowMessageReceived:
        task_id: str
        stage: str
        timestamp: int

    @dataclass
    class WorkflowStageFailed:
        task_id: str
        stage: str
        timestamp: int

    @dataclass
    class WorkflowMessageAcked:
        task_id: str
        stage: str
        timestamp: int

    events = [
        WorkflowMessageAcked("task-1", "planner", 3),
        WorkflowMessageReceived("task-1", "planner", 1),
        WorkflowStageFailed("task-1", "planner", 2),
        SimpleNamespace(task_id="other", stage=None, timestamp=0),
    ]
    health = compute_stage_health(events)
    assert (health["planner"].received, health["planner"].published, health["planner"].failed) == (1, 1, 1)
    assert detect_stage_alerts(events) == ["stage 'planner' has 1 failures"]
    assert detect_stage_alerts(events, failure_threshold=2) == []
    assert [item.timestamp for item in build_task_timeline(events, task_id="task-1")] == [1, 2, 3]

    memory = MemoryObservationCollector()
    queue = AsyncQueueObservationCollector(maxsize=2)
    await memory(events[0])
    await queue(events[0])
    await queue(events[1])
    assert memory.items == [events[0]]
    assert await queue.drain() == events[:2]
    assert await queue.drain() == []

    assert event_to_dict(events[0])["task_id"] == "task-1"

    class PlainEvent:
        def __init__(self) -> None:
            self.value = 1

    assert event_to_dict(PlainEvent())["value"] == 1
    logger = logging.getLogger("relayna-test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        await make_logging_sink(logger)(events[0])
    assert "relayna_observation" in caplog.text


@pytest.mark.asyncio
async def test_rabbitmq_wrappers_and_routing_helpers() -> None:
    calls: list[tuple[str, Any]] = []

    class Client:
        async def initialize(self) -> None:
            calls.append(("initialize", None))

        async def ensure_status_queue(self) -> str:
            calls.append(("ensure_status", None))
            return "status.queue"

        async def ensure_tasks_queue(self) -> str:
            calls.append(("ensure_tasks", None))
            return "tasks.queue"

        async def publish_task(self, payload: Any) -> None:
            calls.append(("task", payload))

        async def publish_status(self, payload: Any) -> None:
            calls.append(("status", payload))

        async def publish_workflow(self, payload: Any) -> None:
            calls.append(("workflow", payload))

        async def publish_to_stage(self, payload: Any, *, stage: str) -> None:
            calls.append((f"stage:{stage}", payload))

        async def publish_to_entry(self, payload: Any, *, route: str) -> None:
            calls.append((f"entry:{route}", payload))

    client = Client()
    await ensure_all(client)  # type: ignore[arg-type]
    assert await ensure_status_queue(client) == "status.queue"  # type: ignore[arg-type]
    assert await ensure_tasks_queue(client) == "tasks.queue"  # type: ignore[arg-type]
    await publish_task(client, {"task_id": "task-1"})  # type: ignore[arg-type]
    await publish_status(client, {"status": "done"})  # type: ignore[arg-type]
    await publish_workflow(client, {"stage": "planner"})  # type: ignore[arg-type]
    await publish_to_stage(client, {}, stage="planner")  # type: ignore[arg-type]
    await publish_to_entry(client, {}, route="start")  # type: ignore[arg-type]
    assert len(calls) == 8

    topology = make_workflow_topology()
    assert resolve_workflow_queue(topology, "planner") == "workflow.planner"
    task_topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    assert resolve_task_routing_key(task_topology, {"task_id": "task-1"}) == "task.request"
    assert resolve_status_routing_key(task_topology, {"task_id": "task-1"}) == "task-1"
    assert clear_retry_headers(
        {
            "x-relayna-retry-attempt": 1,
            "x-relayna-failure-reason": "boom",
            "x-relayna-exception-type": "RuntimeError",
            "keep": "yes",
        }
    ) == {"keep": "yes"}


def test_workflow_state_registry_diagnostics_and_topology_helpers() -> None:
    topology = make_workflow_topology()
    planner = topology.workflow_stage("planner")
    policy = StagePolicy.from_stage(planner)
    assert (policy.max_retries, policy.concurrency, policy.dedup_key_field) == (2, 3, "request_id")

    metadata = StageMetadata.from_stage(planner)
    registry = StageRegistry.from_stages(topology.stages)
    registry.register(metadata)
    assert registry.get("planner") == metadata
    assert registry.get("missing") is None
    assert registry.names() == ("planner", "writer")
    assert registry.as_dict()["planner"]["expected_actions"] == ["plan"]

    rule = TransitionRule.from_stage(planner)
    assert validate_transition("planner", "writer", (rule,)) is True
    assert validate_transition("planner", "unknown", (rule,)) is False
    assert validate_transition("unknown", "writer", (rule,)) is False

    state = WorkflowRunState(task_id="task-1")
    state.update_stage("planner", status="running", message_id="message-1", meta={"attempt": 1})
    state.update_stage("planner", status="failed", meta={"reason": "boom"})
    assert state.stages["planner"].attempts == 2
    assert state.stages["planner"].meta == {"attempt": 1, "reason": "boom"}
    diagnosis = explain_stall(state)
    assert diagnosis.reasons == ["stage 'planner' is failed"]
    untouched = WorkflowRunState(task_id="task-2")
    assert "not entered" in explain_stall(untouched).reasons[0]
    completed = WorkflowRunState(task_id="task-3", current_stage="writer", status="completed")
    assert explain_stall(completed).reasons == []

    progress = FanInProgress(stage="aggregate", expected={"one", "two"})
    assert progress.is_complete is False
    update_fanin_progress(progress, completed_stage="one")
    update_fanin_progress(progress, completed_stage="two")
    assert progress.is_complete is True

    action = WorkflowAction("plan", {"query": "hello"})
    assert validate_action_payload(action, None) == []
    assert validate_action_payload(action, ActionSchema(action="plan")) == []

    assert validate_topology(topology) == []
    assert summarize_topology(topology)["workflow_stages"][0]["name"] == "planner"
    invalid = SimpleNamespace()
    assert validate_topology(invalid) == ["topology must expose connection_string(...)"]

    linear = build_linear_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stage_names=["one", "two"],
    )
    search = build_search_aggregate_workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    assert linear.workflow_stage_names() == ("one", "two")
    assert search.workflow_entry_target_stage("planner") == "planner"

    assert run_state_key("prefix", "task-1") == "prefix:workflow:run:task-1"
    assert fanin_key("prefix", "task-1", "aggregate") == "prefix:workflow:fanin:task-1:aggregate"
    assert clamp_ttl_seconds(None, default=10) == 10
    assert clamp_ttl_seconds(1, minimum=60) == 60


@pytest.mark.asyncio
async def test_mcp_dispatch_and_tool_adapters() -> None:
    topology = make_workflow_topology()
    state = WorkflowRunState(task_id="task-1")
    request = ReplayRequest(task_id="task-1", stage="planner", reason="retry", meta={"force": True})
    assert resume_workflow(request) == {
        "task_id": "task-1",
        "stage": "planner",
        "reason": "retry",
        "meta": {"force": True},
    }
    assert inspect_topology(topology)["stages"][0]["name"] == "planner"
    assert explain_workflow(state)["task_id"] == "task-1"

    class Service:
        async def replay_message(self, dlq_id: str, *, force: bool = False) -> dict[str, Any]:
            return {"dlq_id": dlq_id, "force": force}

        async def list_messages(self, *, queue_name: str | None, limit: int) -> Any:
            assert limit == 50
            return SimpleNamespace(model_dump=lambda mode: {"queue_name": queue_name, "items": [], "mode": mode})

    service = Service()
    assert await replay_dlq(service, "dlq-1", force=True) == {"dlq_id": "dlq-1", "force": True}  # type: ignore[arg-type]
    assert (await list_dlq_messages(service, queue_name="queue"))["queue_name"] == "queue"  # type: ignore[arg-type]

    async def async_tool(value: int) -> int:
        await asyncio.sleep(0)
        return value + 1

    server = RelaynaMCPServer(read_tools={"sync": lambda value: value * 2}, ops_tools={"async": async_tool})
    assert server.list_tools() == {"read": ["sync"], "ops": ["async"]}
    assert await server.call_tool("sync", 2) == 4
    assert await server.call_tool("async", 2) == 3
    with pytest.raises(KeyError, match="Unknown MCP tool"):
        await server.call_tool("missing")
    assert server.list_resources(topology=topology, run_states=[state])[0]["type"] == "topology"
