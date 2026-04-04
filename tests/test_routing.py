import asyncio
import json

import pytest

from relayna.contracts import ContractAliasConfig, WorkflowEnvelope
from relayna.rabbitmq import (
    RelaynaRabbitClient,
    RetryInfrastructure,
    ShardRoutingStrategy,
    TaskIdRoutingStrategy,
    TaskTypeRoutingStrategy,
)
from relayna.topology import (
    RoutedTasksSharedStatusShardedAggregationTopology,
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusTopology,
    WorkflowEntryRoute,
    WorkflowStage,
)


def test_task_id_routing_uses_task_id() -> None:
    strategy = TaskIdRoutingStrategy("task.request")
    assert strategy.task_routing_key({"task_id": "abc"}) == "task.request"
    assert strategy.status_routing_key({"task_id": "abc"}) == "abc"


def test_shard_routing_uses_parent_task_id_when_present() -> None:
    strategy = ShardRoutingStrategy("task.request", shard_count=2)
    key = strategy.status_routing_key({"task_id": "child", "meta": {"parent_task_id": "parent"}})
    assert key in {"agg.0", "agg.1"}


def test_task_type_routing_uses_task_type_and_preserves_status_routing() -> None:
    strategy = TaskTypeRoutingStrategy()

    assert strategy.task_routing_key({"task_id": "abc", "task_type": "task.review"}) == "task.review"
    assert strategy.status_routing_key({"task_id": "abc"}) == "abc"


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
        self.default_exchange = FakeStatusExchange()

    async def declare_queue(
        self,
        name: str,
        *,
        durable: bool,
        arguments: dict[str, object] | None = None,
    ) -> FakeTaskQueue:
        self.declare_queue_calls.append((name, durable, arguments))
        return self.queue


class FakeRobustChannel(FakeTaskChannel):
    def __init__(self, queue: FakeTaskQueue) -> None:
        super().__init__(queue)
        self.prefetch_calls: list[int] = []
        self.declare_exchange_calls: list[tuple[str, object, bool]] = []

    async def set_qos(self, *, prefetch_count: int) -> None:
        self.prefetch_calls.append(prefetch_count)

    async def declare_exchange(self, name: str, exchange_type: object, *, durable: bool) -> object:
        self.declare_exchange_calls.append((name, exchange_type, durable))
        return object()


class FakeRobustConnection:
    def __init__(self, channel: FakeRobustChannel) -> None:
        self._channel = channel
        self.channel_calls = 0
        self.is_closed = False

    async def channel(self) -> FakeRobustChannel:
        self.channel_calls += 1
        return self._channel

    async def close(self) -> None:
        self.is_closed = True


class FakeStatusExchange:
    def __init__(self) -> None:
        self.publish_calls: list[tuple[object, str]] = []

    async def publish(self, message: object, *, routing_key: str) -> None:
        self.publish_calls.append((message, routing_key))


class FakeBindingQueue(FakeTaskQueue):
    pass


@pytest.mark.asyncio
async def test_ensure_tasks_queue_declares_and_binds_queue() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    queue = FakeTaskQueue("tasks.queue")
    channel = FakeTaskChannel(queue)
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._channel = channel
    client._tasks_exchange = object()

    queue_name = await client.ensure_tasks_queue()

    assert queue_name == "tasks.queue"
    assert channel.declare_queue_calls == [("tasks.queue", True, None)]
    assert queue.bind_calls == [(client._tasks_exchange, "task.request")]


def test_task_queue_arguments_include_curated_and_generic_fields() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        tasks_message_ttl_ms=3000,
        dead_letter_exchange="tasks.dlx",
        task_consumer_timeout_ms=600000,
        task_single_active_consumer=True,
        task_max_priority=10,
        task_queue_type="quorum",
        task_queue_arguments_overrides={"x-queue-mode": "lazy"},
        task_queue_kwargs={"x-overflow": "reject-publish"},
    )

    assert topology.task_queue_arguments() == {
        "x-message-ttl": 3000,
        "x-dead-letter-exchange": "tasks.dlx",
        "x-consumer-timeout": 600000,
        "x-single-active-consumer": True,
        "x-max-priority": 10,
        "x-queue-type": "quorum",
        "x-queue-mode": "lazy",
        "x-overflow": "reject-publish",
    }


@pytest.mark.parametrize("value", [0, 256])
def test_task_queue_arguments_reject_invalid_max_priority(value: int) -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_max_priority=value,
    )

    with pytest.raises(ValueError, match="task_max_priority"):
        topology.task_queue_arguments()


def test_status_queue_arguments_merge_existing_and_generic_fields() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        status_stream_max_length_gb=2,
        status_stream_max_segment_size_mb=64,
        status_queue_arguments_overrides={"x-initial-cluster-size": 3},
        status_queue_kwargs={"x-queue-leader-locator": "balanced"},
    )

    assert topology.status_queue_arguments() == {
        "x-queue-type": "stream",
        "x-max-length-bytes": 2 * 1024**3,
        "x-stream-max-segment-size-bytes": 64 * 1024**2,
        "x-initial-cluster-size": 3,
        "x-queue-leader-locator": "balanced",
    }


def test_status_queue_ttl_is_not_applied_to_stream_queues() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        status_use_streams=True,
        status_queue_ttl_ms=60000,
    )

    assert topology.status_queue_arguments() == {
        "x-queue-type": "stream",
    }


def test_task_queue_arguments_raise_on_duplicate_keys() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_consumer_timeout_ms=600000,
        task_queue_kwargs={"x-consumer-timeout": 300000},
    )

    with pytest.raises(ValueError, match="Duplicate task queue arguments"):
        topology.task_queue_arguments()


def test_status_queue_arguments_raise_on_duplicate_keys() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        status_queue_arguments_overrides={"x-queue-type": "quorum"},
    )

    with pytest.raises(ValueError, match="Duplicate status queue arguments"):
        topology.status_queue_arguments()


@pytest.mark.asyncio
async def test_ensure_retry_infrastructure_declares_retry_and_dead_letter_queues() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    queue = FakeTaskQueue("tasks.queue")
    channel = FakeTaskChannel(queue)
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._channel = channel

    infrastructure = await client.ensure_retry_infrastructure(source_queue_name="tasks.queue", delay_ms=3000)

    assert infrastructure == RetryInfrastructure(
        source_queue_name="tasks.queue",
        retry_queue_name="tasks.queue.retry",
        dead_letter_queue_name="tasks.queue.dlq",
    )
    assert channel.declare_queue_calls == [
        (
            "tasks.queue.retry",
            True,
            {
                "x-message-ttl": 3000,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": "tasks.queue",
            },
        ),
        ("tasks.queue.dlq", True, None),
    ]


@pytest.mark.asyncio
async def test_publish_raw_to_queue_uses_default_exchange_and_preserves_headers() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    queue = FakeTaskQueue("tasks.queue")
    channel = FakeTaskChannel(queue)
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._channel = channel

    await client.publish_raw_to_queue(
        "tasks.queue.retry",
        b'{"task_id":"task-123"}',
        correlation_id="corr-123",
        headers={"x-relayna-retry-attempt": 2},
        content_type="application/json",
    )

    message, routing_key = channel.default_exchange.publish_calls[0]
    assert routing_key == "tasks.queue.retry"
    assert message.body == b'{"task_id":"task-123"}'
    assert message.correlation_id == "corr-123"
    assert message.headers["x-relayna-retry-attempt"] == 2


@pytest.mark.asyncio
async def test_publish_status_generates_event_id_when_missing() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
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
async def test_publish_task_normalizes_custom_aliases() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(
        topology=topology,
        alias_config=ContractAliasConfig(field_aliases={"task_id": "attempt_id"}),
    )
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_task({"attempt_id": "attempt-123", "payload": {"kind": "demo"}})

    message, routing_key = exchange.publish_calls[0]
    payload = json.loads(message.body.decode("utf-8"))

    assert routing_key == "task.request"
    assert payload["task_id"] == "attempt-123"
    assert "attempt_id" not in payload
    assert message.headers["task_id"] == "attempt-123"
    assert message.priority is None


@pytest.mark.asyncio
async def test_publish_task_supports_extra_headers() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_task(
        {"task_id": "task-123", "payload": {"kind": "demo"}, "correlation_id": "corr-123"},
        headers={"x-relayna-manual-retry-count": 1},
    )

    message, routing_key = exchange.publish_calls[0]
    assert routing_key == "task.request"
    assert message.correlation_id == "corr-123"
    assert message.headers["task_id"] == "task-123"
    assert message.headers["x-relayna-manual-retry-count"] == 1


@pytest.mark.asyncio
async def test_publish_task_sets_amqp_priority_from_task_priority() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_task({"task_id": "task-123", "payload": {"kind": "demo"}, "priority": 8})

    message, routing_key = exchange.publish_calls[0]
    payload = json.loads(message.body.decode("utf-8"))

    assert routing_key == "task.request"
    assert payload["priority"] == 8
    assert message.priority == 8


@pytest.mark.asyncio
async def test_publish_task_rejects_priority_above_configured_task_max_priority() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_max_priority=5,
    )
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = FakeStatusExchange()

    with pytest.raises(ValueError, match="task_max_priority 5"):
        await client.publish_task({"task_id": "task-123", "payload": {"kind": "demo"}, "priority": 6})


@pytest.mark.asyncio
async def test_publish_tasks_batch_envelope_uses_batch_id_headers_and_canonical_tasks() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(
        topology=topology,
        alias_config=ContractAliasConfig(field_aliases={"task_id": "attempt_id"}),
    )
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_tasks(
        [
            {"attempt_id": "attempt-1", "payload": {"kind": "demo"}},
            {"attempt_id": "attempt-2", "payload": {"kind": "demo"}},
        ],
        mode="batch_envelope",
        batch_id="batch-123",
        meta={"source": "bulk-api"},
    )

    message, routing_key = exchange.publish_calls[0]
    payload = json.loads(message.body.decode("utf-8"))

    assert routing_key == "task.request"
    assert payload["batch_id"] == "batch-123"
    assert payload["meta"] == {"source": "bulk-api"}
    assert [task["task_id"] for task in payload["tasks"]] == ["attempt-1", "attempt-2"]
    assert all("attempt_id" not in task for task in payload["tasks"])
    assert message.correlation_id == "batch-123"
    assert message.priority is None
    assert message.headers == {"batch_id": "batch-123", "batch_size": 2}


@pytest.mark.asyncio
async def test_publish_tasks_batch_envelope_sets_shared_priority() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_tasks(
        [
            {"task_id": "task-1", "payload": {"kind": "demo"}, "priority": 4},
            {"task_id": "task-2", "payload": {"kind": "demo"}, "priority": 4},
        ],
        mode="batch_envelope",
        batch_id="batch-123",
    )

    message, _routing_key = exchange.publish_calls[0]
    payload = json.loads(message.body.decode("utf-8"))

    assert message.priority == 4
    assert [task["priority"] for task in payload["tasks"]] == [4, 4]


@pytest.mark.asyncio
async def test_publish_tasks_batch_envelope_rejects_mixed_priorities() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = FakeStatusExchange()

    with pytest.raises(ValueError, match="share the same priority"):
        await client.publish_tasks(
            [
                {"task_id": "task-1", "payload": {"kind": "demo"}, "priority": 1},
                {"task_id": "task-2", "payload": {"kind": "demo"}, "priority": 2},
            ],
            mode="batch_envelope",
            batch_id="batch-123",
        )


@pytest.mark.asyncio
async def test_publish_tasks_batch_envelope_rejects_partial_priority_assignment() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = FakeStatusExchange()

    with pytest.raises(ValueError, match="share the same priority"):
        await client.publish_tasks(
            [
                {"task_id": "task-1", "payload": {"kind": "demo"}, "priority": 1},
                {"task_id": "task-2", "payload": {"kind": "demo"}},
            ],
            mode="batch_envelope",
            batch_id="batch-123",
        )


@pytest.mark.asyncio
async def test_publish_status_preserves_existing_event_id() -> None:
    topology = SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
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
async def test_routed_tasks_topology_binds_queue_to_task_types() -> None:
    topology = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review", "task.audit"),
    )
    queue = FakeBindingQueue("tasks.review.queue")
    channel = FakeTaskChannel(queue)
    exchange = object()

    queue_name = await topology.ensure_tasks_queue(channel, tasks_exchange=exchange)

    assert queue_name == "tasks.review.queue"
    assert queue.bind_calls == [(exchange, "task.review"), (exchange, "task.audit")]


@pytest.mark.asyncio
async def test_routed_tasks_topology_inherits_task_queue_argument_fields() -> None:
    topology = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review",),
        task_consumer_timeout_ms=600000,
        task_single_active_consumer=True,
        task_max_priority=7,
        task_queue_type="quorum",
        task_queue_arguments_overrides={"x-queue-mode": "lazy"},
        task_queue_kwargs={"x-overflow": "reject-publish"},
    )
    queue = FakeBindingQueue("tasks.review.queue")
    channel = FakeTaskChannel(queue)
    exchange = object()

    queue_name = await topology.ensure_tasks_queue(channel, tasks_exchange=exchange)

    assert queue_name == "tasks.review.queue"
    assert channel.declare_queue_calls == [
        (
            "tasks.review.queue",
            True,
            {
                "x-consumer-timeout": 600000,
                "x-single-active-consumer": True,
                "x-max-priority": 7,
                "x-queue-type": "quorum",
                "x-queue-mode": "lazy",
                "x-overflow": "reject-publish",
            },
        )
    ]
    assert queue.bind_calls == [(exchange, "task.review")]


@pytest.mark.asyncio
async def test_routed_tasks_topology_publish_task_routes_by_task_type() -> None:
    topology = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review",),
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    await client.publish_task({"task_id": "task-123", "task_type": "task.review", "payload": {}})

    assert exchange.publish_calls[0][1] == "task.review"


@pytest.mark.asyncio
async def test_routed_tasks_topology_requires_task_type_for_publish() -> None:
    topology = RoutedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review",),
    )
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._tasks_exchange = exchange

    with pytest.raises(ValueError, match="task_type"):
        await client.publish_task({"task_id": "task-123", "payload": {}})


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


def test_sharded_aggregation_topology_arguments_include_curated_and_generic_fields() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
        aggregation_consumer_timeout_ms=120000,
        aggregation_single_active_consumer=True,
        aggregation_max_priority=5,
        aggregation_queue_type="quorum",
        aggregation_queue_arguments_overrides={"x-delivery-limit": 20},
        aggregation_queue_kwargs={"x-overflow": "reject-publish"},
    )

    assert topology.aggregation_queue_arguments() == {
        "x-consumer-timeout": 120000,
        "x-single-active-consumer": True,
        "x-max-priority": 5,
        "x-queue-type": "quorum",
        "x-delivery-limit": 20,
        "x-overflow": "reject-publish",
    }


@pytest.mark.parametrize("value", [0, 256])
def test_sharded_aggregation_queue_arguments_reject_invalid_max_priority(value: int) -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
        aggregation_max_priority=value,
    )

    with pytest.raises(ValueError, match="aggregation_max_priority"):
        topology.aggregation_queue_arguments()


def test_sharded_aggregation_queue_arguments_raise_on_duplicate_keys() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
        aggregation_queue_arguments_overrides={"x-overflow": "drop-head"},
        aggregation_queue_kwargs={"x-overflow": "reject-publish"},
    )

    with pytest.raises(ValueError, match="Duplicate aggregation queue arguments"):
        topology.aggregation_queue_arguments()


@pytest.mark.asyncio
async def test_routed_sharded_topology_binds_task_queue_and_aggregation_queue() -> None:
    topology = RoutedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.review.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=("task.review",),
        shard_count=4,
    )
    task_queue = FakeBindingQueue("tasks.review.queue")
    task_channel = FakeTaskChannel(task_queue)
    tasks_exchange = object()

    await topology.ensure_tasks_queue(task_channel, tasks_exchange=tasks_exchange)

    aggregation_queue = FakeBindingQueue("aggregation.queue.2")
    aggregation_channel = FakeTaskChannel(aggregation_queue)
    status_exchange = object()

    queue_name = await topology.ensure_aggregation_queue(
        aggregation_channel,
        status_exchange=status_exchange,
        shards=[2],
    )

    assert task_queue.bind_calls == [(tasks_exchange, "task.review")]
    assert queue_name == "aggregation.queue.2"
    assert aggregation_queue.bind_calls == [(status_exchange, "agg.2")]


@pytest.mark.asyncio
async def test_sharded_topology_binds_custom_aggregation_queue_name() -> None:
    topology = SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        tasks_routing_key="task.request",
        status_exchange="status.exchange",
        status_queue="status.queue",
        shard_count=4,
    )
    queue = FakeBindingQueue("aggregation.queue.custom")
    channel = FakeTaskChannel(queue)
    exchange = object()

    queue_name = await topology.ensure_aggregation_queue(
        channel,
        status_exchange=exchange,
        shards=[2, 0],
        queue_name="aggregation.queue.custom",
    )

    assert queue_name == "aggregation.queue.custom"
    assert channel.declare_queue_calls == [("aggregation.queue.custom", True, None)]
    assert queue.bind_calls == [(exchange, "agg.0"), (exchange, "agg.2")]


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


@pytest.mark.asyncio
async def test_sharded_topology_declare_queues_works_on_python_313_slots_dataclass() -> None:
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

    await topology.declare_queues(
        channel,
        tasks_exchange=object(),
        status_exchange=object(),
    )

    assert [name for name, _durable, _arguments in channel.declare_queue_calls] == ["status.queue", "tasks.queue"]


@pytest.mark.asyncio
async def test_sharded_topology_client_initialize_works_on_python_313_slots_dataclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    channel = FakeRobustChannel(queue)
    connection = FakeRobustConnection(channel)

    async def fake_connect_robust(url: str) -> FakeRobustConnection:
        assert url == "amqp://guest:guest@localhost:5672/?name=relayna-robust"
        return connection

    monkeypatch.setattr("relayna.rabbitmq.aio_pika.connect_robust", fake_connect_robust)

    client = RelaynaRabbitClient(topology=topology)

    await client.initialize()

    assert channel.prefetch_calls == [1]
    assert connection.channel_calls == 1
    assert [name for name, _durable, _arguments in channel.declare_queue_calls] == ["status.queue", "tasks.queue"]


def make_workflow_topology() -> SharedStatusWorkflowTopology:
    return SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        stages=(
            WorkflowStage(
                name="topic_planner",
                queue="cq.topic_planner.in_queue",
                binding_keys=("planner.topic_planner.in",),
                publish_routing_key="planner.topic_planner.in",
            ),
            WorkflowStage(
                name="docsearch_planner",
                queue="cq.docsearch_planner.in_queue",
                binding_keys=("planner.docsearch_planner.in", "replanner.docsearch_planner.in"),
                publish_routing_key="planner.docsearch_planner.in",
            ),
        ),
        entry_routes=(
            WorkflowEntryRoute(
                name="planner",
                routing_key="planner.topic_planner.in",
                target_stage="topic_planner",
            ),
            WorkflowEntryRoute(
                name="replanner",
                routing_key="replanner.docsearch_planner.in",
                target_stage="docsearch_planner",
            ),
        ),
    )


def test_workflow_topology_validates_duplicate_stage_names() -> None:
    with pytest.raises(ValueError, match="Duplicate workflow stage name"):
        SharedStatusWorkflowTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            workflow_exchange="workflow.exchange",
            status_exchange="status.exchange",
            status_queue="status.queue",
            stages=(
                WorkflowStage(
                    name="planner",
                    queue="planner.1",
                    binding_keys=("planner.in",),
                    publish_routing_key="planner.in",
                ),
                WorkflowStage(
                    name="planner",
                    queue="planner.2",
                    binding_keys=("planner.2.in",),
                    publish_routing_key="planner.2.in",
                ),
            ),
        )


def test_workflow_topology_validates_unknown_entry_stage() -> None:
    with pytest.raises(ValueError, match="unknown target stage"):
        SharedStatusWorkflowTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            workflow_exchange="workflow.exchange",
            status_exchange="status.exchange",
            status_queue="status.queue",
            stages=(
                WorkflowStage(
                    name="planner",
                    queue="planner.1",
                    binding_keys=("planner.in",),
                    publish_routing_key="planner.in",
                ),
            ),
            entry_routes=(WorkflowEntryRoute(name="replanner", routing_key="replanner.in", target_stage="missing"),),
        )


def test_workflow_queue_arguments_merge_global_and_stage_fields() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        workflow_consumer_timeout_ms=600000,
        workflow_single_active_consumer=True,
        workflow_queue_type="quorum",
        workflow_queue_arguments_overrides={"x-queue-mode": "lazy"},
        workflow_queue_kwargs={"x-overflow": "reject-publish"},
        stages=(
            WorkflowStage(
                name="planner",
                queue="planner.queue",
                binding_keys=("planner.in",),
                publish_routing_key="planner.in",
                queue_arguments_overrides={"x-max-length": 100},
                queue_kwargs={"x-delivery-limit": 20},
            ),
        ),
    )

    assert topology.workflow_queue_arguments("planner") == {
        "x-consumer-timeout": 600000,
        "x-single-active-consumer": True,
        "x-queue-type": "quorum",
        "x-queue-mode": "lazy",
        "x-max-length": 100,
        "x-overflow": "reject-publish",
        "x-delivery-limit": 20,
    }


@pytest.mark.parametrize("value", [0, 256])
def test_workflow_queue_arguments_reject_invalid_max_priority(value: int) -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        workflow_max_priority=value,
        stages=(
            WorkflowStage(
                name="planner",
                queue="planner.queue",
                binding_keys=("planner.in",),
                publish_routing_key="planner.in",
            ),
        ),
    )

    with pytest.raises(ValueError, match="workflow_max_priority"):
        topology.workflow_queue_arguments("planner")


@pytest.mark.asyncio
async def test_workflow_topology_declares_and_binds_stage_queue() -> None:
    topology = make_workflow_topology()
    queue = FakeBindingQueue("cq.docsearch_planner.in_queue")
    channel = FakeTaskChannel(queue)
    exchange = object()

    queue_name = await topology.ensure_workflow_queue(channel, workflow_exchange=exchange, stage="docsearch_planner")

    assert queue_name == "cq.docsearch_planner.in_queue"
    assert channel.declare_queue_calls == [("cq.docsearch_planner.in_queue", True, None)]
    assert queue.bind_calls == [
        (exchange, "planner.docsearch_planner.in"),
        (exchange, "replanner.docsearch_planner.in"),
    ]


@pytest.mark.asyncio
async def test_publish_to_stage_uses_workflow_routing_key() -> None:
    topology = make_workflow_topology()
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._workflow_exchange = exchange

    await client.publish_to_stage(
        {"task_id": "task-123", "stage": "ignored", "payload": {"step": 1}},
        stage="docsearch_planner",
    )

    assert exchange.publish_calls
    _message, routing_key = exchange.publish_calls[0]
    assert routing_key == "planner.docsearch_planner.in"


@pytest.mark.asyncio
async def test_publish_to_entry_uses_named_entry_route() -> None:
    topology = make_workflow_topology()
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._workflow_exchange = exchange

    await client.publish_to_entry(
        WorkflowEnvelope(task_id="task-123", stage="topic_planner", payload={"step": 1}),
        route="replanner",
    )

    assert exchange.publish_calls
    message, routing_key = exchange.publish_calls[0]
    assert routing_key == "replanner.docsearch_planner.in"
    body = json.loads(message.body.decode("utf-8"))
    assert body["stage"] == "docsearch_planner"


@pytest.mark.asyncio
async def test_publish_workflow_message_sets_amqp_priority_from_workflow_priority() -> None:
    topology = make_workflow_topology()
    exchange = FakeStatusExchange()
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._workflow_exchange = exchange

    await client.publish_workflow_message(
        WorkflowEnvelope(task_id="task-123", stage="topic_planner", payload={"step": 1}, priority=6),
        routing_key="planner.topic_planner.in",
    )

    message, routing_key = exchange.publish_calls[0]
    body = json.loads(message.body.decode("utf-8"))

    assert routing_key == "planner.topic_planner.in"
    assert body["priority"] == 6
    assert message.priority == 6


@pytest.mark.asyncio
async def test_publish_workflow_message_rejects_priority_above_configured_workflow_max_priority() -> None:
    topology = SharedStatusWorkflowTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        workflow_exchange="workflow.exchange",
        status_exchange="status.exchange",
        status_queue="status.queue",
        workflow_max_priority=4,
        stages=(
            WorkflowStage(
                name="planner",
                queue="planner.queue",
                binding_keys=("planner.in",),
                publish_routing_key="planner.in",
            ),
        ),
    )
    client = RelaynaRabbitClient(topology=topology)
    client._initialized = True
    client._lock = asyncio.Lock()
    client._workflow_exchange = FakeStatusExchange()

    with pytest.raises(ValueError, match="workflow_max_priority 4"):
        await client.publish_workflow_message(
            WorkflowEnvelope(task_id="task-123", stage="planner", payload={"step": 1}, priority=5),
            routing_key="planner.in",
        )


@pytest.mark.asyncio
async def test_publish_task_raises_for_workflow_topology() -> None:
    client = RelaynaRabbitClient(topology=make_workflow_topology())

    with pytest.raises(RuntimeError, match="publish_to_stage"):
        await client.publish_task({"task_id": "task-123", "payload": {}})
