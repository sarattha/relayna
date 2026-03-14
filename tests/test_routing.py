import asyncio
import json

import pytest

from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient, ShardRoutingStrategy, TaskIdRoutingStrategy


def test_task_id_routing_uses_task_id() -> None:
    strategy = TaskIdRoutingStrategy("task.request")
    assert strategy.task_routing_key({"task_id": "abc"}) == "task.request"
    assert strategy.status_routing_key({"task_id": "abc"}) == "abc"


def test_shard_routing_uses_parent_task_id_when_present() -> None:
    strategy = ShardRoutingStrategy("task.request", shard_count=2)
    key = strategy.status_routing_key({"task_id": "child", "meta": {"parent_task_id": "parent"}})
    assert key in {"agg.0", "agg.1"}


class FakeTaskQueue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bind_calls: list[tuple[object, str]] = []

    async def bind(self, exchange: object, *, routing_key: str) -> None:
        self.bind_calls.append((exchange, routing_key))


class FakeTaskChannel:
    def __init__(self, queue: FakeTaskQueue) -> None:
        self.queue = queue
        self.declare_queue_calls: list[tuple[str, bool, dict[str, object] | None]] = []

    async def declare_queue(
        self,
        name: str,
        *,
        durable: bool,
        arguments: dict[str, object] | None = None,
    ) -> FakeTaskQueue:
        self.declare_queue_calls.append((name, durable, arguments))
        return self.queue


class FakeStatusExchange:
    def __init__(self) -> None:
        self.publish_calls: list[tuple[object, str]] = []

    async def publish(self, message: object, *, routing_key: str) -> None:
        self.publish_calls.append((message, routing_key))


@pytest.mark.asyncio
async def test_ensure_tasks_queue_declares_and_binds_queue() -> None:
    config = RelaynaTopologyConfig(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    queue = FakeTaskQueue("tasks.queue")
    channel = FakeTaskChannel(queue)
    client = RelaynaRabbitClient(config)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._channel = channel
    client._tasks_exchange = object()

    queue_name = await client.ensure_tasks_queue()

    assert queue_name == "tasks.queue"
    assert channel.declare_queue_calls == [("tasks.queue", True, None)]
    assert queue.bind_calls == [(client._tasks_exchange, "task.request")]


@pytest.mark.asyncio
async def test_publish_status_generates_event_id_when_missing() -> None:
    config = RelaynaTopologyConfig(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(config)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._status_exchange = exchange

    await client.publish_status({"task_id": "task-123", "status": "queued"})
    await client.publish_status({"task_id": "task-123", "status": "queued"})

    first_message, first_key = exchange.publish_calls[0]
    second_message, second_key = exchange.publish_calls[1]
    first_payload = json.loads(first_message.body.decode("utf-8"))
    second_payload = json.loads(second_message.body.decode("utf-8"))

    assert first_key == "task-123"
    assert second_key == "task-123"
    assert first_payload["correlation_id"] == "task-123"
    assert first_payload["event_id"] == second_payload["event_id"]
    assert len(first_payload["event_id"]) == 64


@pytest.mark.asyncio
async def test_publish_status_preserves_existing_event_id() -> None:
    config = RelaynaTopologyConfig(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(config)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._status_exchange = exchange

    await client.publish_status({"task_id": "task-123", "status": "queued", "event_id": "evt-custom"})

    payload = json.loads(exchange.publish_calls[0][0].body.decode("utf-8"))

    assert payload["event_id"] == "evt-custom"
