from __future__ import annotations

import asyncio

from relayna.contracts import StatusEventEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import app_client, build_app, build_config, parse_sse_events, poll_history, unique_suffix
except ModuleNotFoundError:
    from real_stack_common import app_client, build_app, build_config, parse_sse_events, poll_history, unique_suffix


async def publish_status_flow(config, task_id: str) -> None:
    client = RelaynaRabbitClient(config, connection_name="relayna-real-status-publisher")
    await client.initialize()
    try:
        await client.publish_status(
            StatusEventEnvelope(task_id=task_id, status="queued", message="Task accepted.")
        )
        await client.publish_status(
            StatusEventEnvelope(task_id=task_id, status="processing", message="Worker started.")
        )
        await client.publish_status(
            StatusEventEnvelope(task_id=task_id, status="completed", message="Task completed.")
        )
    finally:
        await client.close()


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"task-{suffix}"
    config = build_config(suffix)
    app = build_app(config, suffix)

    async with app_client(app) as client:
        await publish_status_flow(config, task_id)
        history = await poll_history(client, task_id=task_id, expected_count=3)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert history_statuses == ["queued", "processing", "completed"]
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["queued", "processing", "completed"]
    assert all("id" in event for event in sse_events if event.get("event") == "status")

    print("real_fastapi_status_smoke: ok")
    print(f"task_id={task_id}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")


if __name__ == "__main__":
    asyncio.run(main())
