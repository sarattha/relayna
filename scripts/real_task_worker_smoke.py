from __future__ import annotations

import asyncio

from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        build_shared_topology,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import app_client, build_app, build_shared_topology, parse_sse_events, poll_history, unique_suffix


async def run_worker_once(topology, task_id: str) -> None:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-worker-consumer")
    await rabbitmq.initialize()
    processed = asyncio.Event()

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        if task.task_id != task_id:
            return
        await context.publish_status(status="processing", message="Worker started.")
        await asyncio.sleep(0.1)
        await context.publish_status(
            status="completed",
            message="Worker completed.",
            result={"payload": task.payload},
        )
        processed.set()

    consumer = TaskConsumer(rabbitmq=rabbitmq, handler=handler, idle_retry_seconds=0.1)
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-worker-consumer")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-worker-publisher")
    await publisher.initialize()

    try:
        await publisher.publish_task(
            TaskEnvelope(task_id=task_id, payload={"kind": "demo", "value": suffix_from_task(task_id)})
        )
        await asyncio.wait_for(processed.wait(), timeout=10.0)
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()


def suffix_from_task(task_id: str) -> str:
    return task_id.split("-", 1)[1]


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"task-{suffix}"
    topology = build_shared_topology(suffix)
    app = build_app(topology, suffix)

    async with app_client(app) as client:
        await run_worker_once(topology, task_id)
        history = await poll_history(client, task_id=task_id, expected_count=2)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert history_statuses == ["processing", "completed"]
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["processing", "completed"]
    completed_event = next(event for event in sse_events if event.get("event") == "status" and event["data"]["status"] == "completed")
    assert completed_event["data"]["result"]["payload"]["kind"] == "demo"

    print("real_task_worker_smoke: ok")
    print(f"task_id={task_id}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")


if __name__ == "__main__":
    asyncio.run(main())
