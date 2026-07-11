from __future__ import annotations

import json
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from relayna.contracts import StatusEventEnvelope, WorkflowEnvelope
from relayna.metrics import RelaynaMetrics
from relayna.rabbitmq.client import (
    DirectQueuePublisher,
    RelaynaRabbitClient,
    _clear_default_priority,
    _coerce_queue_max_priority,
    _to_dict,
    _to_json_bytes,
    declare_stream_queue,
)
from relayna.topology import (
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusTopology,
    WorkflowEntryRoute,
    WorkflowStage,
)


class Exchange:
    def __init__(self) -> None:
        self.publishes: list[tuple[Any, str]] = []

    async def publish(self, message: Any, *, routing_key: str) -> None:
        self.publishes.append((message, routing_key))


class Queue:
    def __init__(self, name: str, *, messages: int = 0, consumers: int = 0) -> None:
        self.name = name
        self.bindings: list[tuple[Any, str]] = []
        self.declaration_result = SimpleNamespace(message_count=messages, consumer_count=consumers)

    async def bind(self, exchange: Any, *, routing_key: str) -> None:
        self.bindings.append((exchange, routing_key))


class Channel:
    def __init__(self, *, fail_declare: bool = False, fail_close: bool = False) -> None:
        self.default_exchange = Exchange()
        self.exchanges: dict[str, Exchange] = {}
        self.queues: list[Queue] = []
        self.qos: list[int] = []
        self.closed = False
        self.is_closed = False
        self.fail_declare = fail_declare
        self.fail_close = fail_close

    async def set_qos(self, *, prefetch_count: int) -> None:
        self.qos.append(prefetch_count)

    async def declare_exchange(self, name: str, *_args: Any, **_kwargs: Any) -> Exchange:
        exchange = Exchange()
        self.exchanges[name] = exchange
        return exchange

    async def declare_queue(self, name: str, **_kwargs: Any) -> Queue:
        if self.fail_declare:
            raise RuntimeError("declare failed")
        queue = Queue(name, messages=4, consumers=2)
        self.queues.append(queue)
        return queue

    async def close(self) -> None:
        self.closed = True
        self.is_closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


class Connection:
    def __init__(self, channels: list[Channel] | None = None) -> None:
        self.channels = list(channels or [Channel()])
        self.last_channel = self.channels[-1]
        self.is_closed = False
        self.closed = False

    async def channel(self) -> Channel:
        if self.channels:
            self.last_channel = self.channels.pop(0)
        return self.last_channel

    async def close(self) -> None:
        self.is_closed = True
        self.closed = True


def shared_topology() -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_max_priority=5,
    )


def workflow_topology() -> SharedStatusWorkflowTopology:
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
                max_inflight=2,
            ),
        ),
        entry_routes=(WorkflowEntryRoute("entry", "entry.in", "planner"),),
        workflow_max_priority=3,
    )


@pytest.mark.asyncio
async def test_client_initialization_queue_ensure_publish_inspect_ping_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = Channel()
    inspect_channel = Channel()
    ping_channel = Channel()
    connection = Connection([primary, inspect_channel, ping_channel])

    async def connect(_url: str) -> Connection:
        return connection

    monkeypatch.setattr("relayna.rabbitmq.client.aio_pika.connect_robust", connect)
    metrics = RelaynaMetrics(service="test-client")
    client = RelaynaRabbitClient(shared_topology(), alias_config=None, metrics=metrics)
    assert client.topology.tasks_queue == "tasks.queue"
    assert client.alias_config is None
    assert client.metrics is metrics
    await client.initialize()
    await client.initialize()
    assert primary.qos == [1]
    assert await client.ensure_status_queue() == "status.queue"
    assert await client.ensure_tasks_queue() == "tasks.queue"
    assert await client.ensure_workflow_queue("default") == "tasks.queue"
    retry = await client.ensure_retry_infrastructure(source_queue_name="tasks.queue", delay_ms=100)
    assert retry.retry_queue_name == "tasks.queue.retry"

    await client.publish_tasks(
        [{"task_id": "task-1"}, {"task_id": "task-2"}],
        max_concurrency=2,
    )
    await client.publish_status({"task_id": "task-1", "status": "done"})
    await client.publish_raw_to_queue("raw.queue", b"payload", priority=None)
    inspection = await client.inspect_queue("tasks.queue")
    assert inspection is not None
    assert (inspection.message_count, inspection.consumer_count) == (4, 2)
    await client.ping()
    assert ping_channel.closed is True
    rendered = metrics.render().decode()
    assert "relayna_queue_publish_total" in rendered
    assert "relayna_status_events_published_total" in rendered
    await client.close()
    assert connection.closed is True
    assert client._connection is None


@pytest.mark.asyncio
async def test_client_error_states_and_inspection_failure() -> None:
    client = RelaynaRabbitClient(shared_topology())
    client._initialized = True
    for method in (client.ensure_status_queue, client.ensure_tasks_queue):
        with pytest.raises(RuntimeError, match="not initialized"):
            await method()
    with pytest.raises(RuntimeError, match="Workflow exchange"):
        await client.ensure_workflow_queue("default")
    with pytest.raises(RuntimeError, match="not initialized"):
        await client.ensure_aggregation_queue(shards=[0])
    with pytest.raises(RuntimeError, match="not initialized"):
        await client.ensure_retry_infrastructure(source_queue_name="queue", delay_ms=1)
    with pytest.raises(RuntimeError, match="Tasks exchange"):
        await client.publish_task({"task_id": "task"})
    with pytest.raises(RuntimeError, match="Status exchange"):
        await client.publish_status({"task_id": "task", "status": "done"})
    with pytest.raises(RuntimeError, match="Workflow exchange"):
        await client.publish_workflow_message(
            {"task_id": "task", "message_id": "message", "stage": "default", "payload": {}},
            routing_key="route",
        )
    with pytest.raises(RuntimeError, match="not initialized"):
        await client.publish_raw_to_queue("queue", b"body")
    with pytest.raises(RuntimeError, match="robust connection"):
        await client.acquire_channel()

    client._channel = Channel(fail_declare=True, fail_close=True)  # type: ignore[assignment]
    client._connection = Connection([client._channel])  # type: ignore[list-item,assignment]
    assert await client.inspect_queue("missing") is None

    client._channel = Channel()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="source_queue_name"):
        await client.ensure_retry_infrastructure(source_queue_name=" ", delay_ms=1)
    with pytest.raises(ValueError, match="greater than zero"):
        await client.ensure_retry_infrastructure(source_queue_name="queue", delay_ms=0)


@pytest.mark.asyncio
async def test_publish_validation_workflow_and_batch_errors() -> None:
    shared = RelaynaRabbitClient(shared_topology())
    shared._initialized = True
    shared._channel = Channel()  # type: ignore[assignment]
    shared._tasks_exchange = Exchange()  # type: ignore[assignment]
    shared._status_exchange = Exchange()  # type: ignore[assignment]
    shared._workflow_exchange = shared._tasks_exchange

    with pytest.raises(ValueError, match="Unsupported publish mode"):
        await shared.publish_tasks([], mode="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="batch_id"):
        await shared.publish_tasks([], mode="batch_envelope")
    with pytest.raises(RuntimeError, match="requires SharedStatusWorkflowTopology"):
        await shared.publish_to_entry(
            {"task_id": "task", "message_id": "message", "stage": "default", "payload": {}},
            route="entry",
        )
    with pytest.raises(ValueError, match="parent_task_id"):
        await shared.publish_aggregation_status({"task_id": "task", "status": "done"})

    workflow = RelaynaRabbitClient(workflow_topology(), metrics=RelaynaMetrics(service="workflow"))
    workflow._initialized = True
    workflow._channel = Channel()  # type: ignore[assignment]
    exchange = Exchange()
    workflow._workflow_exchange = exchange  # type: ignore[assignment]
    workflow._status_exchange = Exchange()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="publish_to_stage"):
        await workflow.publish_task({"task_id": "task"})
    payload = {
        "task_id": "task",
        "message_id": "message",
        "stage": "planner",
        "origin_stage": "planner",
        "payload": {},
        "priority": 2,
    }
    await workflow.publish_workflow(payload, headers={"custom": "yes"})
    await workflow.publish_to_stage(payload, stage="planner")
    await workflow.publish_to_entry(payload, route="entry")
    assert len(exchange.publishes) == 3
    with pytest.raises(ValueError, match="workflow priority"):
        await workflow.publish_workflow_message({**payload, "priority": 4}, routing_key="planner.in")


def test_payload_helpers_cover_models_enums_and_priority_helpers() -> None:
    class Status(StrEnum):
        DONE = "done"

    class Payload(BaseModel):
        task_id: str
        status: Status | None = None

    client = RelaynaRabbitClient(shared_topology())
    assert client._prepare_status_payload(Payload(task_id="task", status=Status.DONE))["status"] == "done"
    assert client._prepare_status_payload({"task_id": "task", "status": None})["status"] is None
    numeric = client._prepare_status_payload({"task_id": "task", "status": 7})
    assert numeric["status"] == "7"
    workflow = WorkflowEnvelope(task_id="task", message_id="message", stage="writer", payload={})
    assert client._prepare_workflow_payload(workflow, stage="planner")["stage"] == "planner"
    assert _to_dict(Payload(task_id="task")) == {"task_id": "task"}
    assert json.loads(_to_json_bytes({"unicode": "สวัสดี"})) == {"unicode": "สวัสดี"}
    assert _coerce_queue_max_priority({}) is None
    assert _coerce_queue_max_priority({"x-max-priority": "4"}) == 4

    message = SimpleNamespace(priority=0)
    _clear_default_priority(message, priority=None)  # type: ignore[arg-type]
    assert message.priority is None
    _clear_default_priority(message, priority=1)  # type: ignore[arg-type]
    assert message.priority is None


@pytest.mark.asyncio
async def test_direct_queue_publisher_and_declare_stream_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = Channel()
    connection = Connection([channel])

    async def connect(_url: str) -> Connection:
        return connection

    monkeypatch.setattr("relayna.rabbitmq.client.aio_pika.connect_robust", connect)
    publisher = DirectQueuePublisher(amqp_url="amqp://localhost/", queue_name="direct.queue")
    await publisher.initialize()
    await publisher.initialize()
    await publisher.publish({"hello": "world"}, correlation_id="corr")
    assert channel.default_exchange.publishes[0][1] == "direct.queue"
    declared = await declare_stream_queue(
        channel=channel,  # type: ignore[arg-type]
        queue_name="stream.queue",
        queue_arguments={"x-queue-type": "stream"},
    )
    assert declared.name == "stream.queue"
    await publisher.close()
    assert connection.closed is True

    broken = DirectQueuePublisher(amqp_url="amqp://localhost/", queue_name="direct.queue")

    async def no_channel() -> None:
        broken._channel = None

    broken.initialize = no_channel  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Publisher channel"):
        await broken.publish({})


@pytest.mark.asyncio
async def test_client_remaining_prepare_batch_contract_and_lazy_init_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = Channel()
    connection = Connection([channel])

    async def connect(_url: str) -> Connection:
        return connection

    monkeypatch.setattr("relayna.rabbitmq.client.aio_pika.connect_robust", connect)
    lazy = RelaynaRabbitClient(shared_topology())
    assert await lazy.ensure_tasks_queue() == "tasks.queue"

    class WorkflowModel(BaseModel):
        task_id: str
        message_id: str
        stage: str
        payload: dict[str, Any]

    prepared = lazy._prepare_workflow_payload(
        WorkflowModel(task_id="task", message_id="message", stage="default", payload={})
    )
    assert prepared["stage"] == "default"
    lazy._validate_workflow_contract(prepared)
    lazy._validate_workflow_contract({"stage": ""})
    assert lazy._resolved_workflow_max_priority("") is None

    status = StatusEventEnvelope(task_id="task", status="done")
    assert lazy._prepare_status_payload(status)["task_id"] == "task"

    lazy._tasks_exchange = None
    with pytest.raises(RuntimeError, match="Tasks exchange"):
        await lazy._publish_batch_envelope({"batch_id": "batch", "tasks": []}, priority=None)
    metrics_client = RelaynaRabbitClient(shared_topology(), metrics=RelaynaMetrics(service="batch"))
    metrics_client._initialized = True
    metrics_client._tasks_exchange = Exchange()  # type: ignore[assignment]
    await metrics_client.publish_tasks([{"task_id": "task"}], mode="batch_envelope", batch_id="batch")
    assert "relayna_queue_publish_total" in metrics_client.metrics.render().decode()  # type: ignore[union-attr]
