from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from relayna.consumer import RetryPolicy, RetryStatusConfig, TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        build_shared_topology,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import (
        app_client,
        build_app,
        build_shared_topology,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )


async def run_failing_worker_until_dead_letter(topology: Any, task_id: str) -> int:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-retry-consumer")
    await rabbitmq.initialize()
    attempts = 0
    exhausted = asyncio.Event()

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        nonlocal attempts
        if task.task_id != task_id:
            return
        attempts += 1
        if attempts >= 2:
            exhausted.set()
        raise RuntimeError("retry smoke failure")

    consumer = TaskConsumer(
        rabbitmq=rabbitmq,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=1, delay_ms=500),
        retry_statuses=RetryStatusConfig(enabled=True),
        idle_retry_seconds=0.1,
    )
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-retry-consumer")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-retry-publisher")
    await publisher.initialize()

    try:
        await publisher.publish_task(TaskEnvelope(task_id=task_id, payload={"kind": "retry-demo"}))
        await asyncio.wait_for(exhausted.wait(), timeout=10.0)
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()

    return attempts


async def fetch_dead_letter_payload(topology: Any) -> tuple[dict[str, Any], Mapping[str, Any], str | None]:
    probe = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-retry-probe")
    await probe.initialize()
    channel = None
    try:
        source_queue_name = await probe.ensure_tasks_queue()
        infrastructure = await probe.ensure_retry_infrastructure(source_queue_name=source_queue_name, delay_ms=500)
        channel = await probe.acquire_channel(prefetch=1)
        queue = await channel.declare_queue(infrastructure.dead_letter_queue_name, durable=True, arguments=None)

        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            message = await queue.get(timeout=1.0, fail=False)
            if message is None:
                continue
            try:
                payload = json.loads(message.body.decode("utf-8", errors="replace"))
                headers = dict(getattr(message, "headers", {}) or {})
                correlation_id = getattr(message, "correlation_id", None)
                return payload, headers, correlation_id
            finally:
                await message.ack()
        raise TimeoutError("Timed out waiting for a dead-lettered task message.")
    finally:
        if channel is not None:
            await channel.close()
        await probe.close()


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"retry-task-{suffix}"
    topology = build_shared_topology(suffix)
    app = build_app(topology, suffix)

    async with app_client(app) as client:
        attempts = await run_failing_worker_until_dead_letter(topology, task_id)
        history = await poll_history(client, task_id=task_id, expected_count=2)
        latest = await fetch_latest_status(client, task_id)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    dlq_payload, dlq_headers, dlq_correlation_id = await fetch_dead_letter_payload(topology)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert attempts == 2
    assert history_statuses == ["retrying", "failed"]
    assert latest["task_id"] == task_id
    assert latest["event"]["status"] == "failed"
    assert latest["event"]["message"] == "retry smoke failure"
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["retrying", "failed"]

    assert dlq_payload["task_id"] == task_id
    assert dlq_payload["payload"]["kind"] == "retry-demo"
    assert dlq_headers["x-relayna-retry-attempt"] == 1
    assert dlq_headers["x-relayna-max-retries"] == 1
    assert dlq_headers["x-relayna-failure-reason"] == "handler_error"
    assert dlq_headers["x-relayna-exception-type"] == "RuntimeError"
    assert dlq_headers["x-relayna-source-queue"] == topology.tasks_queue
    assert dlq_correlation_id == task_id

    print("real_retry_dead_letter_smoke: ok")
    print(f"task_id={task_id}")
    print(f"attempts={attempts}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")
    print(f"dlq_queue={topology.tasks_queue}.dlq")


if __name__ == "__main__":
    asyncio.run(main())
