from __future__ import annotations

import asyncio
import json

from relayna.contracts import StatusEventEnvelope, TerminalStatusSet
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        build_sharded_topology,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import app_client, build_app, build_sharded_topology, parse_sse_events, poll_history, unique_suffix


async def publish_status(topology, event: StatusEventEnvelope, *, connection_name: str) -> None:
    client = RelaynaRabbitClient(topology=topology, connection_name=connection_name)
    await client.initialize()
    try:
        if event.status == "aggregating":
            await client.publish_aggregation_status(event)
        else:
            await client.publish_status(event)
    finally:
        await client.close()


async def probe_aggregation_queue(topology, task_id: str, parent_task_id: str) -> tuple[str, dict[str, object]]:
    probe = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-aggregation-probe")
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
        await publish_status(
            topology,
            StatusEventEnvelope(
                task_id=task_id,
                status="aggregating",
                message="Aggregation input ready.",
                meta={"parent_task_id": parent_task_id},
            ),
            connection_name="relayna-real-aggregation-publisher",
        )
        message = await queue.get(timeout=5.0, fail=True)
        payload = json.loads(message.body.decode("utf-8", errors="replace"))
        await message.ack()
        return queue_name, payload
    finally:
        if channel is not None:
            await channel.close()
        await probe.close()


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"agg-task-{suffix}"
    parent_task_id = f"parent-{suffix}"
    topology = build_sharded_topology(suffix)

    queue_name, routed_payload = await probe_aggregation_queue(topology, task_id, parent_task_id)

    app = build_app(topology, suffix, sse_terminal_statuses=TerminalStatusSet({"aggregation-processed"}))
    async with app_client(app) as client:
        await publish_status(
            topology,
            StatusEventEnvelope(
                task_id=task_id,
                status="aggregation-processed",
                message="Aggregation worker completed.",
                meta={"parent_task_id": parent_task_id},
                correlation_id=task_id,
            ),
            connection_name="relayna-real-aggregation-complete-publisher",
        )
        history = await poll_history(client, task_id=task_id, expected_count=2)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert routed_payload["task_id"] == task_id
    assert routed_payload["status"] == "aggregating"
    assert routed_payload["meta"]["parent_task_id"] == parent_task_id
    assert routed_payload["meta"]["aggregation_role"] == "aggregation"
    assert "aggregation_shard" in routed_payload["meta"]

    assert history_statuses == ["aggregating", "aggregation-processed"]
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["aggregating", "aggregation-processed"]

    print("real_sharded_aggregation_smoke: ok")
    print(f"task_id={task_id}")
    print(f"parent_task_id={parent_task_id}")
    print(f"queue_name={queue_name}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")


if __name__ == "__main__":
    asyncio.run(main())
