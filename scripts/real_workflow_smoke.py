from __future__ import annotations

import asyncio
from typing import Any

from relayna.consumer import RetryPolicy, RetryStatusConfig, WorkflowConsumer, WorkflowContext
from relayna.contracts import WorkflowEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        build_workflow_topology,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import (
        app_client,
        build_app,
        build_workflow_topology,
        fetch_latest_status,
        parse_sse_events,
        poll_history,
        unique_suffix,
    )


async def run_workflow_once(topology: Any, task_id: str) -> dict[str, Any]:
    topic_rabbit = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-workflow-topic-planner")
    docsearch_rabbit = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-workflow-docsearch-planner")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-workflow-publisher")
    await topic_rabbit.initialize()
    await docsearch_rabbit.initialize()
    await publisher.initialize()

    topic_processed = asyncio.Event()
    docsearch_processed = asyncio.Event()
    received: dict[str, Any] = {}

    async def topic_handler(message: WorkflowEnvelope, context: WorkflowContext) -> None:
        if message.task_id != task_id:
            return
        received["topic_stage"] = message.stage
        await context.publish_status(status="planning", message="Topic planner started.")
        await context.publish_to_stage(
            "docsearch_planner",
            payload={"query": message.payload["query"], "seed_topics": ["policy", "retention"]},
            action="collect-documents",
            meta={"handoff": "topic->docsearch"},
        )
        topic_processed.set()

    async def docsearch_handler(message: WorkflowEnvelope, context: WorkflowContext) -> None:
        if message.task_id != task_id:
            return
        received["docsearch_stage"] = message.stage
        received["origin_stage"] = message.origin_stage
        received["action"] = message.action
        received["payload"] = dict(message.payload)
        await context.publish_status(
            status="completed",
            message="Docsearch planner completed.",
            result={"documents": ["doc-123", "doc-456"]},
        )
        docsearch_processed.set()

    topic_consumer = WorkflowConsumer(
        rabbitmq=topic_rabbit,
        stage="topic_planner",
        handler=topic_handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        idle_retry_seconds=0.1,
    )
    docsearch_consumer = WorkflowConsumer(
        rabbitmq=docsearch_rabbit,
        stage="docsearch_planner",
        handler=docsearch_handler,
        retry_policy=RetryPolicy(max_retries=2, delay_ms=1000),
        retry_statuses=RetryStatusConfig(enabled=True),
        idle_retry_seconds=0.1,
    )
    topic_task = asyncio.create_task(topic_consumer.run_forever(), name="relayna-real-workflow-topic-planner")
    docsearch_task = asyncio.create_task(
        docsearch_consumer.run_forever(), name="relayna-real-workflow-docsearch-planner"
    )

    try:
        await publisher.publish_to_entry(
            WorkflowEnvelope(
                task_id=task_id,
                stage="topic_planner",
                action="build-plan",
                payload={"query": f"workflow-{task_id}"},
                meta={"source": "real-smoke"},
            ),
            route="planner_entry",
        )
        await asyncio.wait_for(topic_processed.wait(), timeout=10.0)
        await asyncio.wait_for(docsearch_processed.wait(), timeout=10.0)
    finally:
        topic_consumer.stop()
        docsearch_consumer.stop()
        await topic_task
        await docsearch_task
        await publisher.close()
        await topic_rabbit.close()
        await docsearch_rabbit.close()

    return received


async def main() -> None:
    suffix = unique_suffix()
    task_id = f"workflow-task-{suffix}"
    topology = build_workflow_topology(suffix)
    app = build_app(topology, suffix)

    async with app_client(app) as client:
        received = await run_workflow_once(topology, task_id)
        history = await poll_history(client, task_id=task_id, expected_count=2)
        latest = await fetch_latest_status(client, task_id)
        events_response = await client.get("/events/" + task_id)
        events_response.raise_for_status()
        sse_events = parse_sse_events(events_response.text)

    history_statuses = [event["status"] for event in history["events"]]
    sse_statuses = [event["data"]["status"] for event in sse_events if event.get("event") == "status"]

    assert history_statuses == ["planning", "completed"]
    assert latest["event"]["status"] == "completed"
    assert latest["event"]["message"] == "Docsearch planner completed."
    assert latest["event"]["result"] == {"documents": ["doc-123", "doc-456"]}
    assert sse_events[0]["event"] == "ready"
    assert sse_statuses == ["planning", "completed"]
    assert received == {
        "topic_stage": "topic_planner",
        "docsearch_stage": "docsearch_planner",
        "origin_stage": "topic_planner",
        "action": "collect-documents",
        "payload": {
            "query": f"workflow-{task_id}",
            "seed_topics": ["policy", "retention"],
        },
    }

    print("real_workflow_smoke: ok")
    print(f"task_id={task_id}")
    print(f"history_statuses={history_statuses}")
    print(f"sse_statuses={sse_statuses}")
    print(f"received={received}")


if __name__ == "__main__":
    asyncio.run(main())
