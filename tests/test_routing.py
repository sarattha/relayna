import asyncio
import json

import pytest

from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient, ShardRoutingStrategy, TaskIdRoutingStrategy
from relayna.topology import SharedTasksSharedStatusShardedAggregationTopology, SharedTasksSharedStatusTopology


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


class FakeBindingQueue(FakeTaskQueue):
    pass


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


@pytest.mark.asyncio
async def test_publish_aggregation_status_enriches_metadata_and_routes_by_shard() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._status_exchange = exchange

    await client.publish_aggregation_status(
        {"task_id": "child-1", "status": "processing", "meta": {"parent_task_id": "parent-1"}}
    )

    message, routing_key = exchange.publish_calls[0]
    payload = json.loads(message.body.decode("utf-8"))

    assert routing_key.startswith("agg.")
    assert payload["meta"]["aggregation_role"] == "aggregation"
    assert payload["meta"]["parent_task_id"] == "parent-1"
    assert payload["meta"]["aggregation_shard"] == int(routing_key.split(".")[1])


@pytest.mark.asyncio
async def test_publish_aggregation_status_requires_parent_task_id() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=2,
    )
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._status_exchange = FakeStatusExchange()

    with pytest.raises(ValueError, match="meta.parent_task_id"):
        await client.publish_aggregation_status({"task_id": "child-1", "status": "processing", "meta": {}})


@pytest.mark.asyncio
async def test_default_topology_binds_shared_status_queue_to_wildcard() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    queue = FakeBindingQueue("status.queue")
    channel = FakeTaskChannel(queue)
    exchange = object()
    queue_name = await topology.ensure_status_queue(channel, status_exchange=exchange)

    assert queue_name == "status.queue"
    assert queue.bind_calls == [(exchange, "#")]


@pytest.mark.asyncio
async def test_sharded_topology_binds_subset_queue_to_multiple_shards() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
    )
    queue = FakeBindingQueue("aggregation.queue.shards.1-3")
    channel = FakeTaskChannel(queue)
    exchange = object()

    queue_name = await topology.ensure_aggregation_queue(
        channel,
        status_exchange=exchange,
        shards=[3, 1],
    )

    assert queue_name == "aggregation.queue.shards.1-3"
    assert queue.bind_calls == [(exchange, "agg.1"), (exchange, "agg.3")]


@pytest.mark.asyncio
async def test_sharded_aggregation_topology_does_not_predeclare_all_aggregation_queues() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
    )
    queue = FakeBindingQueue("status.queue")
    channel = FakeTaskChannel(queue)
    tasks_exchange = object()
    status_exchange = object()

    await topology.declare_queues(
        channel,
        tasks_exchange=tasks_exchange,
        status_exchange=status_exchange,
    )

    declared_names = [name for name, _durable, _arguments in channel.declare_queue_calls]
    assert declared_names == ["status.queue", "tasks.queue"]
