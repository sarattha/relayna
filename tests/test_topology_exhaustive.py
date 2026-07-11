from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from relayna.contracts import ActionSchema
from relayna.topology import (
    RoutedTasksSharedStatusShardedAggregationTopology,
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusTopology,
    WorkflowEntryRoute,
    WorkflowStage,
)


class Queue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bindings: list[tuple[Any, str]] = []

    async def bind(self, exchange: Any, *, routing_key: str) -> None:
        self.bindings.append((exchange, routing_key))


class Channel:
    def __init__(self) -> None:
        self.exchanges: list[tuple[str, Any, bool]] = []
        self.queues: list[tuple[str, dict[str, Any]]] = []
        self.queue_objects: list[Queue] = []

    async def declare_exchange(self, name: str, kind: Any, *, durable: bool) -> str:
        self.exchanges.append((name, kind, durable))
        return name

    async def declare_queue(self, name: str, **kwargs: Any) -> Queue:
        self.queues.append((name, kwargs))
        queue = Queue(name)
        self.queue_objects.append(queue)
        return queue


def stage(**changes: Any) -> WorkflowStage:
    base = WorkflowStage(
        name="planner",
        queue="workflow.planner",
        binding_keys=("planner.in",),
        publish_routing_key="planner.in",
    )
    return replace(base, **changes)


def workflow_topology(**changes: Any) -> SharedStatusWorkflowTopology:
    values = {
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "workflow_exchange": "workflow.exchange",
        "status_exchange": "status.exchange",
        "status_queue": "status.queue",
        "stages": (stage(),),
    }
    values.update(changes)
    return SharedStatusWorkflowTopology(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"stages": ()}, "At least one"),
        ({"stages": (stage(name=" "),)}, "names must not be empty"),
        ({"stages": (stage(), stage(queue="workflow.other"))}, "Duplicate workflow stage"),
        ({"stages": (stage(queue=" "),)}, "queue name"),
        ({"stages": (stage(), stage(name="other"))}, "Duplicate workflow queue"),
        ({"stages": (stage(binding_keys=("",)),)}, "binding key"),
        ({"stages": (stage(publish_routing_key=""),)}, "publish_routing_key"),
        ({"stages": (stage(sla_ms=0),)}, "sla_ms"),
        ({"stages": (stage(timeout_seconds=0),)}, "timeout_seconds"),
        ({"stages": (stage(max_retries=-1),)}, "max_retries"),
        ({"stages": (stage(retry_delay_ms=0),)}, "retry_delay_ms"),
        ({"stages": (stage(max_inflight=0),)}, "max_inflight"),
        ({"stages": (stage(dedup_key_fields=("id", "id")),)}, "must be unique"),
        ({"stages": (stage(dedup_key_fields=("",)),)}, "must not be empty"),
        (
            {"stages": (stage(accepted_actions=(ActionSchema(action="go"), ActionSchema(action="go"))),)},
            "duplicate action",
        ),
        ({"entry_routes": (WorkflowEntryRoute(" ", "route", "planner"),)}, "names must not be empty"),
        (
            {
                "entry_routes": (
                    WorkflowEntryRoute("entry", "one", "planner"),
                    WorkflowEntryRoute("entry", "two", "planner"),
                )
            },
            "Duplicate workflow entry",
        ),
        ({"entry_routes": (WorkflowEntryRoute("entry", " ", "planner"),)}, "routing_key"),
        ({"entry_routes": (WorkflowEntryRoute("entry", "route", "missing"),)}, "unknown target"),
        ({"stages": (stage(allowed_next_stages=("next", "next")),)}, "must be unique"),
        ({"stages": (stage(allowed_next_stages=("missing",)),)}, "unknown downstream"),
        ({"stages": (stage(terminal=True, allowed_next_stages=("planner",)),)}, "cannot be terminal"),
        (
            {"stages": (stage(terminal=True, produced_actions=(ActionSchema(action="done"),)),)},
            "cannot be terminal",
        ),
    ],
)
def test_workflow_topology_validation_errors(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        workflow_topology(**changes)


@pytest.mark.asyncio
async def test_workflow_topology_all_methods_and_declarations() -> None:
    topology = workflow_topology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/?heartbeat=10",
        dead_letter_exchange="dead.exchange",
        workflow_consumer_timeout_ms=1000,
        workflow_single_active_consumer=True,
        workflow_max_priority=5,
        workflow_queue_type="quorum",
        status_stream_max_length_gb=2,
        status_stream_max_segment_size_mb=3,
        entry_routes=(WorkflowEntryRoute("entry", "entry.in", "planner"),),
    )
    assert topology.connection_string() == "amqp://guest:guest@localhost:5672/?heartbeat=10"
    assert topology.connection_string("worker").endswith("&name=worker")
    assert topology.task_queue_arguments()["x-max-priority"] == 5
    assert topology.status_queue_arguments()["x-max-length-bytes"] == 2 * 1024**3
    assert topology.status_queue_arguments()["x-stream-max-segment-size-bytes"] == 3 * 1024**2
    assert topology.status_stream_consume_arguments() == {"x-stream-offset": "last"}
    assert topology.aggregation_queue_arguments() == {}
    assert topology.workflow_exchange_name() == "workflow.exchange"
    assert topology.workflow_stage_names() == ("planner",)
    assert topology.workflow_queue_names() == ("workflow.planner",)
    assert topology.workflow_stage("planner").name == "planner"
    assert topology.workflow_binding_keys("planner") == ("planner.in",)
    assert topology.workflow_publish_routing_key("planner") == "planner.in"
    assert topology.workflow_entry_routing_key("entry") == "entry.in"
    assert topology.workflow_entry_target_stage("entry") == "planner"
    assert topology.default_workflow_stage() == "planner"
    assert topology.task_queue_name() == "workflow.planner"
    assert topology.status_queue_name() == "status.queue"
    assert topology.aggregation_queue_name([], queue_name="custom") == "custom"
    assert topology.task_binding_keys() == ("planner.in",)
    assert topology.status_binding_keys() == ("#",)
    assert topology.status_routing_key({"task_id": "task-1"}) == "task-1"

    for operation in (
        lambda: topology.aggregation_queue_name([]),
        lambda: topology.aggregation_binding_keys([]),
        lambda: topology.task_routing_key({}),
        lambda: topology.status_routing_key({}),
        lambda: topology.aggregation_status_routing_key({}),
        lambda: topology.aggregation_shard({}),
        lambda: topology.workflow_stage("missing"),
        lambda: topology.workflow_entry_routing_key("missing"),
    ):
        with pytest.raises((RuntimeError, ValueError, KeyError)):
            operation()

    channel = Channel()
    workflow_exchange, status_exchange = await topology.declare_exchanges(channel)  # type: ignore[arg-type]
    await topology.declare_queues(
        channel,  # type: ignore[arg-type]
        tasks_exchange=workflow_exchange,  # type: ignore[arg-type]
        status_exchange=status_exchange,  # type: ignore[arg-type]
    )
    assert await topology.ensure_tasks_queue(channel, tasks_exchange=workflow_exchange) == "workflow.planner"  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="shard-aware"):
        await topology.ensure_aggregation_queue(channel, status_exchange=status_exchange, shards=[0])  # type: ignore[arg-type]

    classic = workflow_topology(status_use_streams=False, status_queue_ttl_ms=5000)
    assert classic.status_queue_arguments() == {"x-expires": 5000}
    assert classic.status_stream_consume_arguments() == {}


def shared_topology(**changes: Any) -> SharedTasksSharedStatusTopology:
    values = {
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "tasks_exchange": "tasks.exchange",
        "tasks_queue": "tasks.queue",
        "tasks_routing_key": "task.request",
        "status_exchange": "status.exchange",
        "status_queue": "status.queue",
    }
    values.update(changes)
    return SharedTasksSharedStatusTopology(**values)


@pytest.mark.asyncio
async def test_shared_task_topology_all_methods_and_declarations() -> None:
    topology = shared_topology(
        dead_letter_exchange="dead.exchange",
        tasks_message_ttl_ms=10,
        task_consumer_timeout_ms=20,
        task_single_active_consumer=True,
        task_max_priority=5,
        task_queue_type="quorum",
        status_use_streams=False,
        status_queue_ttl_ms=30,
    )
    assert topology.connection_string() == topology.rabbitmq_url
    assert topology.connection_string("worker").endswith("?name=worker")
    assert topology.task_queue_arguments() == {
        "x-message-ttl": 10,
        "x-dead-letter-exchange": "dead.exchange",
        "x-consumer-timeout": 20,
        "x-single-active-consumer": True,
        "x-max-priority": 5,
        "x-queue-type": "quorum",
    }
    assert topology.status_queue_arguments() == {"x-expires": 30}
    assert topology.status_stream_consume_arguments() == {}
    assert topology.aggregation_queue_arguments() == {}
    assert topology.workflow_exchange_name() == "tasks.exchange"
    assert topology.workflow_stage_names() == ("default",)
    assert topology.workflow_queue_names() == ("tasks.queue",)
    assert topology.workflow_queue_name("default") == "tasks.queue"
    assert topology.workflow_binding_keys("default") == ("task.request",)
    assert topology.workflow_queue_arguments("default")["x-max-priority"] == 5
    assert topology.workflow_publish_routing_key("default") == "task.request"
    assert topology.workflow_entry_routing_key("default") == "task.request"
    assert topology.default_workflow_stage() == "default"
    assert topology.aggregation_queue_name([], queue_name="custom") == "custom"
    assert topology.task_routing_key({"task_id": "task-1"}) == "task.request"
    assert topology.status_routing_key({"task_id": "task-1"}) == "task-1"
    for operation in (
        lambda: topology.workflow_queue_name("missing"),
        lambda: topology.aggregation_queue_name([]),
        lambda: topology.aggregation_binding_keys([]),
        lambda: topology.aggregation_status_routing_key({}),
        lambda: topology.aggregation_shard({}),
    ):
        with pytest.raises((KeyError, RuntimeError)):
            operation()

    channel = Channel()
    tasks_exchange, status_exchange = await topology.declare_exchanges(channel)  # type: ignore[arg-type]
    await topology.declare_queues(
        channel,  # type: ignore[arg-type]
        tasks_exchange=tasks_exchange,  # type: ignore[arg-type]
        status_exchange=status_exchange,  # type: ignore[arg-type]
    )
    assert (
        await topology.ensure_workflow_queue(
            channel,
            workflow_exchange=tasks_exchange,
            stage="default",  # type: ignore[arg-type]
        )
        == "tasks.queue"
    )
    with pytest.raises(RuntimeError, match="shard-aware"):
        await topology.ensure_aggregation_queue(channel, status_exchange=status_exchange, shards=[0])  # type: ignore[arg-type]

    empty_routing = shared_topology(tasks_routing_key=" ")
    with pytest.raises(RuntimeError, match="single workflow"):
        empty_routing.workflow_publish_routing_key("default")

    routed = RoutedTasksSharedStatusTopology(
        rabbitmq_url=topology.rabbitmq_url,
        tasks_exchange="tasks.exchange",
        tasks_queue="tasks.queue",
        status_exchange="status.exchange",
        status_queue="status.queue",
        task_types=(" type.a ", "", "type.a", "type.b"),
    )
    assert routed.task_binding_keys() == ("type.a", "type.b")
    assert routed.task_routing_key({"task_id": "task", "task_type": "type.b"}) == "type.b"
    with pytest.raises(RuntimeError, match="single workflow"):
        routed.workflow_publish_routing_key("default")
    with pytest.raises(ValueError, match="At least one"):
        replace(routed, task_types=()).task_binding_keys()


@pytest.mark.parametrize(
    "topology",
    [
        SharedTasksSharedStatusShardedAggregationTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            tasks_exchange="tasks.exchange",
            tasks_queue="tasks.queue",
            tasks_routing_key="task.request",
            status_exchange="status.exchange",
            status_queue="status.queue",
            shard_count=4,
            aggregation_consumer_timeout_ms=100,
            aggregation_single_active_consumer=True,
            aggregation_max_priority=5,
            aggregation_queue_type="quorum",
        ),
        RoutedTasksSharedStatusShardedAggregationTopology(
            rabbitmq_url="amqp://guest:guest@localhost:5672/",
            tasks_exchange="tasks.exchange",
            tasks_queue="tasks.queue",
            status_exchange="status.exchange",
            status_queue="status.queue",
            task_types=("type.a",),
            shard_count=4,
            aggregation_consumer_timeout_ms=100,
            aggregation_single_active_consumer=True,
            aggregation_max_priority=5,
            aggregation_queue_type="quorum",
        ),
    ],
)
@pytest.mark.asyncio
async def test_sharded_topologies_all_paths(topology: Any) -> None:
    assert topology.aggregation_queue_arguments()["x-max-priority"] == 5
    assert topology.aggregation_queue_name([1]) == "aggregation.queue.1"
    assert topology.aggregation_queue_name([2, 1]) == "aggregation.queue.shards.1-2"
    assert topology.aggregation_queue_name([1], queue_name="custom") == "custom"
    assert topology.aggregation_binding_keys([2, 1]) == ("agg.1", "agg.2")
    event = {"task_id": "child", "meta": {"parent_task_id": "parent"}}
    shard = topology.aggregation_shard(event)
    assert topology.aggregation_status_routing_key(event) == f"agg.{shard}"
    with pytest.raises(ValueError, match="At least one"):
        topology.aggregation_queue_name([])
    with pytest.raises(ValueError, match="outside"):
        topology.aggregation_queue_name([4])

    channel = Channel()
    queue_name = await topology.ensure_aggregation_queue(
        channel,  # type: ignore[arg-type]
        status_exchange="status.exchange",  # type: ignore[arg-type]
        shards=[0, 1],
    )
    assert queue_name == "aggregation.queue.shards.0-1"
    await topology.declare_queues(
        channel,  # type: ignore[arg-type]
        tasks_exchange="tasks.exchange",  # type: ignore[arg-type]
        status_exchange="status.exchange",  # type: ignore[arg-type]
    )
