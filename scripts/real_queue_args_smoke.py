from __future__ import annotations

import asyncio
import json

from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import StatusEventEnvelope, TaskEnvelope, TerminalStatusSet
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.topology import SharedTasksSharedStatusShardedAggregationTopology, SharedTasksSharedStatusTopology

try:
    from scripts.real_stack_common import app_client, build_app, parse_sse_events, poll_history, unique_suffix
except ModuleNotFoundError:
    from real_stack_common import app_client, build_app, parse_sse_events, poll_history, unique_suffix


def build_queue_args_shared_topology(suffix: str) -> SharedTasksSharedStatusTopology:
    return SharedTasksSharedStatusTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}",
        tasks_routing_key=f"relayna.task.request.{suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
        task_consumer_timeout_ms=600000,
        task_single_active_consumer=True,
        task_max_priority=10,
        task_queue_kwargs={"x-queue-mode": "lazy"},
        status_queue_kwargs={"x-initial-cluster-size": 1},
    )


def build_queue_args_sharded_topology(suffix: str) -> SharedTasksSharedStatusShardedAggregationTopology:
    return SharedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}",
        tasks_routing_key=f"relayna.task.request.{suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
        shard_count=4,
        aggregation_queue_template=f"aggregation.queue.{suffix}.{{shard}}",
        aggregation_queue_name_prefix=f"aggregation.queue.{suffix}.shards",
        aggregation_consumer_timeout_ms=120000,
        aggregation_single_active_consumer=True,
        aggregation_max_priority=5,
        aggregation_queue_kwargs={"x-queue-mode": "lazy"},
        status_queue_kwargs={"x-initial-cluster-size": 1},
    )


def assert_shared_topology_queue_arguments(topology: SharedTasksSharedStatusTopology) -> None:
    assert topology.task_queue_arguments() == {
        "x-consumer-timeout": 600000,
        "x-single-active-consumer": True,
        "x-max-priority": 10,
        "x-queue-mode": "lazy",
    }
    assert topology.status_queue_arguments() == {
        "x-queue-type": "stream",
        "x-initial-cluster-size": 1,
    }


def assert_sharded_topology_queue_arguments(topology: SharedTasksSharedStatusShardedAggregationTopology) -> None:
    assert topology.aggregation_queue_arguments() == {
        "x-consumer-timeout": 120000,
        "x-single-active-consumer": True,
        "x-max-priority": 5,
        "x-queue-mode": "lazy",
    }
    assert topology.status_queue_arguments() == {
        "x-queue-type": "stream",
        "x-initial-cluster-size": 1,
    }


async def run_task_flow(topology: SharedTasksSharedStatusTopology, task_id: str) -> tuple[list[str], list[str]]:
    app = build_app(topology, task_id.rsplit("-", 1)[1])
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-queue-args-task-consumer")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-queue-args-task-publisher")
    await rabbitmq.initialize()
    await publisher.initialize()
    processed = asyncio.Event()

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        if task.task_id != task_id:
            return
        await context.publish_status(status="processing", message="Queue-args worker started.")
        await asyncio.sleep(0.1)
        await context.publish_status(status="completed", message="Queue-args worker completed.")
        processed.set()

    consumer = TaskConsumer(rabbitmq=rabbitmq, handler=handler, idle_retry_seconds=0.1)
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-queue-args-task-consumer")
    try:
        async with app_client(app) as client:
            await publisher.publish_task(TaskEnvelope(task_id=task_id, payload={"kind": "queue-args"}))
            await asyncio.wait_for(processed.wait(), timeout=10.0)
            history = await poll_history(client, task_id=task_id, expected_count=2)
            events_response = await client.get("/events/" + task_id)
            events_response.raise_for_status()
            sse_events = parse_sse_events(events_response.text)
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]
    assert history_statuses == ["processing", "completed"]
    assert sse_statuses == ["processing", "completed"]
    return history_statuses, sse_statuses


async def run_aggregation_flow(
    topology: SharedTasksSharedStatusShardedAggregationTopology,
    task_id: str,
    parent_task_id: str,
) -> tuple[str, list[str], list[str]]:
    probe = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-queue-args-aggregation-probe")
    await probe.initialize()
    channel = None
    try:
        queue_name = await probe.ensure_aggregation_queue(shards=list(range(topology.shard_count)))
        channel = await probe.acquire_channel(prefetch=1)
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments=topology.aggregation_queue_arguments() or None,
        )
        publisher = RelaynaRabbitClient(
            topology=topology, connection_name="relayna-real-queue-args-aggregation-publisher"
        )
        await publisher.initialize()
        try:
            await publisher.publish_aggregation_status(
                StatusEventEnvelope(
                    task_id=task_id,
                    status="aggregating",
                    message="Aggregation input ready.",
                    meta={"parent_task_id": parent_task_id},
                )
            )
        finally:
            await publisher.close()
        message = await queue.get(timeout=5.0, fail=True)
        routed_payload = json.loads(message.body.decode("utf-8", errors="replace"))
        await message.ack()
    finally:
        if channel is not None:
            await channel.close()
        await probe.close()

    assert routed_payload["status"] == "aggregating"
    assert routed_payload["meta"]["parent_task_id"] == parent_task_id

    app = build_app(
        topology, parent_task_id.rsplit("-", 1)[1], sse_terminal_statuses=TerminalStatusSet({"aggregation-processed"})
    )
    async with app_client(app) as client:
        finisher = RelaynaRabbitClient(
            topology=topology, connection_name="relayna-real-queue-args-aggregation-finisher"
        )
        await finisher.initialize()
        try:
            await finisher.publish_status(
                StatusEventEnvelope(
                    task_id=task_id,
                    status="aggregation-processed",
                    message="Aggregation worker completed.",
                    meta={"parent_task_id": parent_task_id},
                    correlation_id=task_id,
                )
            )
        finally:
            await finisher.close()
        history = await poll_history(client, task_id=task_id, expected_count=2)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]
    assert history_statuses == ["aggregating", "aggregation-processed"]
    assert sse_statuses == ["aggregating", "aggregation-processed"]
    return queue_name, history_statuses, sse_statuses


async def main() -> None:
    task_suffix = unique_suffix()
    shared_topology = build_queue_args_shared_topology(task_suffix)
    assert_shared_topology_queue_arguments(shared_topology)
    task_id = f"task-queue-args-{task_suffix}"
    task_history_statuses, task_sse_statuses = await run_task_flow(shared_topology, task_id)

    aggregation_suffix = unique_suffix()
    sharded_topology = build_queue_args_sharded_topology(aggregation_suffix)
    assert_sharded_topology_queue_arguments(sharded_topology)
    aggregation_task_id = f"agg-queue-args-{aggregation_suffix}"
    parent_task_id = f"parent-queue-args-{aggregation_suffix}"
    queue_name, aggregation_history_statuses, aggregation_sse_statuses = await run_aggregation_flow(
        sharded_topology,
        aggregation_task_id,
        parent_task_id,
    )

    print("real_queue_args_smoke: ok")
    print(f"task_id={task_id}")
    print(f"task_history_statuses={task_history_statuses}")
    print(f"task_sse_statuses={task_sse_statuses}")
    print(f"aggregation_task_id={aggregation_task_id}")
    print(f"parent_task_id={parent_task_id}")
    print(f"aggregation_queue_name={queue_name}")
    print(f"aggregation_history_statuses={aggregation_history_statuses}")
    print(f"aggregation_sse_statuses={aggregation_sse_statuses}")


if __name__ == "__main__":
    asyncio.run(main())
