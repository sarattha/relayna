from __future__ import annotations

import asyncio

from relayna.consumer import RetryPolicy, TaskConsumer, TaskContext
from relayna.contracts import ContractAliasConfig, TaskEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import app_client, build_app, build_shared_topology, parse_sse_events, poll_history, unique_suffix
except ModuleNotFoundError:
    from real_stack_common import app_client, build_app, build_shared_topology, parse_sse_events, poll_history, unique_suffix


async def run_batch_worker_once(
    topology,
    *,
    alias_config: ContractAliasConfig,
    batch_id: str,
    attempt_ids: list[str],
) -> list[tuple[str, str | None, int | None, int | None]]:
    rabbitmq = RelaynaRabbitClient(
        topology=topology,
        alias_config=alias_config,
        connection_name="relayna-real-alias-batch-consumer",
    )
    await rabbitmq.initialize()
    processed = asyncio.Event()
    handled: list[tuple[str, str | None, int | None, int | None]] = []
    expected_ids = set(attempt_ids)

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        if task.task_id not in expected_ids:
            return
        handled.append((task.task_id, context.batch_id, context.batch_index, context.batch_size))
        await context.publish_status(status="processing", message="Worker started.")
        await asyncio.sleep(0.1)
        await context.publish_status(
            status="completed",
            message="Worker completed.",
            result={
                "batch_id": context.batch_id,
                "batch_index": context.batch_index,
                "batch_size": context.batch_size,
            },
        )
        if len(handled) == len(attempt_ids):
            processed.set()

    consumer = TaskConsumer(
        rabbitmq=rabbitmq,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        alias_config=alias_config,
        idle_retry_seconds=0.1,
    )
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-alias-batch-consumer")
    publisher = RelaynaRabbitClient(
        topology=topology,
        alias_config=alias_config,
        connection_name="relayna-real-alias-batch-publisher",
    )
    await publisher.initialize()

    try:
        await publisher.publish_tasks(
            [
                {"attempt_id": attempt_ids[0], "payload": {"kind": "demo", "value": "one"}},
                {"attempt_id": attempt_ids[1], "payload": {"kind": "demo", "value": "two"}},
            ],
            mode="batch_envelope",
            batch_id=batch_id,
            meta={"source": "real-smoke"},
        )
        await asyncio.wait_for(processed.wait(), timeout=10.0)
        return handled
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()


async def main() -> None:
    suffix = unique_suffix()
    batch_id = f"batch-{suffix}"
    attempt_ids = [f"attempt-{suffix}-1", f"attempt-{suffix}-2"]
    alias_config = ContractAliasConfig(field_aliases={"task_id": "attempt_id"})
    topology = build_shared_topology(suffix)
    app = build_app(topology, suffix, alias_config=alias_config)

    async with app_client(app) as client:
        handled = await run_batch_worker_once(
            topology,
            alias_config=alias_config,
            batch_id=batch_id,
            attempt_ids=attempt_ids,
        )
        history = await poll_history(client, task_id=attempt_ids[0], expected_count=2, task_param_name="attempt_id")
        latest = await client.get("/status/" + attempt_ids[0])
        latest.raise_for_status()
        latest_payload = latest.json()
        events_response = await client.get("/events/" + attempt_ids[0])
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]
    completed_event = next(
        event for event in sse_events if event.get("event") == "status" and event["data"]["status"] == "completed"
    )

    assert handled == [
        (attempt_ids[0], batch_id, 0, 2),
        (attempt_ids[1], batch_id, 1, 2),
    ]
    assert history["attempt_id"] == attempt_ids[0]
    assert history_statuses == ["processing", "completed"]
    assert all("attempt_id" in event for event in history["events"])
    assert all("task_id" not in event for event in history["events"])
    assert latest_payload["attempt_id"] == attempt_ids[0]
    assert latest_payload["event"]["attempt_id"] == attempt_ids[0]
    assert latest_payload["event"]["status"] == "completed"
    assert latest_payload["event"]["result"]["batch_id"] == batch_id
    assert latest_payload["event"]["result"]["batch_index"] == 0
    assert latest_payload["event"]["result"]["batch_size"] == 2
    assert "task_id" not in latest_payload["event"]
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["processing", "completed"]
    assert completed_event["data"]["attempt_id"] == attempt_ids[0]
    assert completed_event["data"]["result"]["batch_id"] == batch_id
    assert completed_event["data"]["result"]["batch_index"] == 0
    assert completed_event["data"]["result"]["batch_size"] == 2
    assert "task_id" not in completed_event["data"]

    print("real_alias_batch_task_smoke: ok")
    print(f"batch_id={batch_id}")
    print(f"attempt_ids={attempt_ids}")
    print(f"handled={handled}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")


if __name__ == "__main__":
    asyncio.run(main())
