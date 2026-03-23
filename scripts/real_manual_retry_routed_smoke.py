from __future__ import annotations

import asyncio
from typing import Any

from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.topology import RoutedTasksSharedStatusShardedAggregationTopology

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import (
        app_client,
        build_app,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )


def build_routed_topology(
    suffix: str,
    *,
    queue_suffix: str,
    task_types: tuple[str, ...],
) -> RoutedTasksSharedStatusShardedAggregationTopology:
    return RoutedTasksSharedStatusShardedAggregationTopology(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        tasks_exchange=f"relayna.tasks.exchange.{suffix}",
        tasks_queue=f"relayna.tasks.queue.{suffix}.{queue_suffix}",
        status_exchange=f"relayna.status.exchange.{suffix}",
        status_queue=f"relayna.status.queue.{suffix}",
        task_types=task_types,
        shard_count=1,
        aggregation_queue_template=f"aggregation.queue.{suffix}.{{shard}}",
        aggregation_queue_name_prefix=f"aggregation.queue.{suffix}.shards",
    )


async def run_handoff_worker_until_republished(topology: Any, task_id: str) -> None:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-manual-retry-source")
    await rabbitmq.initialize()
    republished = asyncio.Event()

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        if task.task_id != task_id or task.task_type != "task.generate":
            return
        republished.set()
        await context.manual_retry(
            task_type="task.review",
            payload_merge={"quality": "high"},
            reason="quality gate failed",
            meta={"handoff": "review"},
        )

    consumer = TaskConsumer(rabbitmq=rabbitmq, handler=handler, idle_retry_seconds=0.1)
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-manual-retry-source")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-manual-retry-publisher")
    await publisher.initialize()

    try:
        await publisher.publish_task(
            TaskEnvelope(
                task_id=task_id,
                task_type="task.generate",
                payload={"quality": "low"},
                correlation_id=task_id,
            )
        )
        await asyncio.wait_for(republished.wait(), timeout=10.0)
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()


async def ensure_task_queue_bound(topology: Any, connection_name: str) -> None:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name=connection_name)
    await rabbitmq.initialize()
    try:
        await rabbitmq.ensure_tasks_queue()
    finally:
        await rabbitmq.close()


async def run_review_worker_until_processed(topology: Any, task_id: str) -> dict[str, Any]:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-manual-retry-target")
    await rabbitmq.initialize()
    processed = asyncio.Event()
    received: dict[str, Any] = {}

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        if task.task_id != task_id or task.task_type != "task.review":
            return
        received.update(
            {
                "task_type": task.task_type,
                "payload": dict(task.payload),
                "manual_retry_count": context.manual_retry_count,
                "previous_task_type": context.manual_retry_previous_task_type,
                "source_consumer": context.manual_retry_source_consumer,
                "reason": context.manual_retry_reason,
            }
        )
        await context.publish_status(status="completed", message="Manual retry completed.")
        processed.set()

    consumer = TaskConsumer(rabbitmq=rabbitmq, handler=handler, idle_retry_seconds=0.1)
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-manual-retry-target")
    try:
        await asyncio.wait_for(processed.wait(), timeout=10.0)
    finally:
        consumer.stop()
        await consumer_task
        await rabbitmq.close()
    return received


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"manual-retry-task-{suffix}"
    source_topology = build_routed_topology(suffix, queue_suffix="generate", task_types=("task.generate",))
    target_topology = build_routed_topology(suffix, queue_suffix="review", task_types=("task.review",))
    app = build_app(source_topology, suffix)

    async with app_client(app) as client:
        await ensure_task_queue_bound(target_topology, "relayna-real-manual-retry-target-setup")
        await run_handoff_worker_until_republished(source_topology, task_id)
        handoff_history = await poll_history(client, task_id=task_id, expected_count=1)
        assert [event["status"] for event in handoff_history["events"]] == ["manual_retrying"]

        review_worker = asyncio.create_task(run_review_worker_until_processed(target_topology, task_id))
        received = await asyncio.wait_for(review_worker, timeout=10.0)
        history = await poll_history(client, task_id=task_id, expected_count=2)
        latest = await fetch_latest_status(client, task_id)
        events_response = await asyncio.wait_for(client.get("/events/" + task_id), timeout=10.0)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert history_statuses == ["manual_retrying", "completed"]
    assert latest["task_id"] == task_id
    assert latest["event"]["status"] == "completed"
    assert latest["event"]["message"] == "Manual retry completed."
    assert latest["event"]["meta"]["manual_retry"]["count"] == 1
    assert latest["event"]["meta"]["manual_retry"]["previous_task_type"] == "task.generate"
    assert latest["event"]["meta"]["manual_retry"]["source_consumer"] == "relayna-task-consumer"
    assert latest["event"]["meta"]["manual_retry"]["reason"] == "quality gate failed"
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["manual_retrying", "completed"]
    assert received == {
        "task_type": "task.review",
        "payload": {"quality": "high"},
        "manual_retry_count": 1,
        "previous_task_type": "task.generate",
        "source_consumer": "relayna-task-consumer",
        "reason": "quality gate failed",
    }

    print("real_manual_retry_routed_smoke: ok")
    print(f"task_id={task_id}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")
    print(f"received={received}")


if __name__ == "__main__":
    asyncio.run(main())
