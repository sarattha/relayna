from __future__ import annotations

import asyncio
from typing import Any

from redis.asyncio import Redis

from relayna.consumer import (
    RetryPolicy,
    RetryStatusConfig,
    TaskConsumer,
    TaskContext,
    WorkflowConsumer,
    WorkflowContext,
)
from relayna.contracts import TaskEnvelope, WorkflowEnvelope
from relayna.observability import RedisObservationStore, make_redis_observation_sink
from relayna.rabbitmq import RelaynaRabbitClient

try:
    from scripts.real_stack_common import (
        app_client,
        build_app,
        build_shared_topology,
        build_workflow_topology,
        fetch_latest_status,
        poll_execution_graph,
        poll_history,
        unique_suffix,
    )
except ModuleNotFoundError:
    from real_stack_common import (
        app_client,
        build_app,
        build_shared_topology,
        build_workflow_topology,
        fetch_latest_status,
        poll_execution_graph,
        poll_history,
        unique_suffix,
    )


async def run_retry_flow(topology: Any, task_id: str, observation_store_prefix: str) -> int:
    rabbitmq = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-exec-graph-task-consumer")
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-exec-graph-task-publisher")
    redis = Redis.from_url("redis://localhost:6379/0")
    observation_store = RedisObservationStore(redis, prefix=observation_store_prefix, ttl_seconds=86400)
    attempts = 0
    exhausted = asyncio.Event()

    await rabbitmq.initialize()
    await publisher.initialize()

    async def handler(task: TaskEnvelope, context: TaskContext) -> None:
        nonlocal attempts
        if task.task_id != task_id:
            return
        attempts += 1
        if attempts == 1:
            raise RuntimeError("execution graph retry smoke failure")
        await context.publish_status(status="completed", message="Retry completed.")
        exhausted.set()

    consumer = TaskConsumer(
        rabbitmq=rabbitmq,
        handler=handler,
        retry_policy=RetryPolicy(max_retries=1, delay_ms=500),
        retry_statuses=RetryStatusConfig(enabled=True),
        idle_retry_seconds=0.1,
        observation_sink=make_redis_observation_sink(observation_store),
    )
    consumer_task = asyncio.create_task(consumer.run_forever(), name="relayna-real-exec-graph-task-consumer")

    try:
        await publisher.publish_task(TaskEnvelope(task_id=task_id, payload={"kind": "execution-graph-smoke"}))
        await asyncio.wait_for(exhausted.wait(), timeout=15.0)
    finally:
        consumer.stop()
        await consumer_task
        await publisher.close()
        await rabbitmq.close()
        await redis.aclose()

    return attempts


async def run_workflow_flow(topology: Any, task_id: str, observation_store_prefix: str) -> None:
    topic_rabbit = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-exec-graph-topic-planner")
    docsearch_rabbit = RelaynaRabbitClient(
        topology=topology,
        connection_name="relayna-real-exec-graph-docsearch-planner",
    )
    publisher = RelaynaRabbitClient(topology=topology, connection_name="relayna-real-exec-graph-workflow-publisher")
    redis = Redis.from_url("redis://localhost:6379/0")
    observation_store = RedisObservationStore(redis, prefix=observation_store_prefix, ttl_seconds=86400)
    observation_sink = make_redis_observation_sink(observation_store)
    topic_processed = asyncio.Event()
    docsearch_processed = asyncio.Event()

    await topic_rabbit.initialize()
    await docsearch_rabbit.initialize()
    await publisher.initialize()

    async def topic_handler(message: WorkflowEnvelope, context: WorkflowContext) -> None:
        if message.task_id != task_id:
            return
        await context.publish_status(status="planning", message="Topic planner started.")
        await context.publish_to_stage(
            "docsearch_planner",
            payload={"query": message.payload["query"]},
            action="collect-documents",
            meta={"handoff": "topic->docsearch"},
        )
        topic_processed.set()

    async def docsearch_handler(message: WorkflowEnvelope, context: WorkflowContext) -> None:
        if message.task_id != task_id:
            return
        await context.publish_status(status="completed", message="Docsearch planner completed.")
        docsearch_processed.set()

    topic_consumer = WorkflowConsumer(
        rabbitmq=topic_rabbit,
        stage="topic_planner",
        handler=topic_handler,
        idle_retry_seconds=0.1,
        observation_sink=observation_sink,
    )
    docsearch_consumer = WorkflowConsumer(
        rabbitmq=docsearch_rabbit,
        stage="docsearch_planner",
        handler=docsearch_handler,
        idle_retry_seconds=0.1,
        observation_sink=observation_sink,
    )
    topic_task = asyncio.create_task(topic_consumer.run_forever(), name="relayna-real-exec-graph-topic-planner")
    docsearch_task = asyncio.create_task(
        docsearch_consumer.run_forever(),
        name="relayna-real-exec-graph-docsearch-planner",
    )

    try:
        await publisher.publish_to_entry(
            WorkflowEnvelope(
                task_id=task_id,
                stage="topic_planner",
                action="build-plan",
                payload={"query": f"workflow-{task_id}"},
                meta={"source": "real-execution-graph-smoke"},
            ),
            route="planner_entry",
        )
        await asyncio.wait_for(topic_processed.wait(), timeout=15.0)
        await asyncio.wait_for(docsearch_processed.wait(), timeout=15.0)
    finally:
        topic_consumer.stop()
        docsearch_consumer.stop()
        await topic_task
        await docsearch_task
        await publisher.close()
        await topic_rabbit.close()
        await docsearch_rabbit.close()
        await redis.aclose()


async def main() -> None:
    suffix = unique_suffix()
    shared_task_id = f"execution-graph-task-{suffix}"
    workflow_task_id = f"execution-graph-workflow-{suffix}"
    shared_observation_prefix = f"relayna-smoke-observations-{suffix}-shared"
    workflow_observation_prefix = f"relayna-smoke-observations-{suffix}-workflow"

    shared_topology = build_shared_topology(suffix)
    shared_app = build_app(
        shared_topology,
        suffix,
        observation_store_prefix=shared_observation_prefix,
        observation_store_ttl_seconds=86400,
        include_execution_router=True,
    )
    workflow_topology = build_workflow_topology(suffix)
    workflow_app = build_app(
        workflow_topology,
        f"{suffix}-workflow",
        observation_store_prefix=workflow_observation_prefix,
        observation_store_ttl_seconds=86400,
        include_execution_router=True,
    )

    async with app_client(shared_app) as shared_client:
        attempts = await run_retry_flow(shared_topology, shared_task_id, shared_observation_prefix)
        shared_history = await poll_history(shared_client, task_id=shared_task_id, expected_count=2)
        shared_latest = await fetch_latest_status(shared_client, shared_task_id)
        shared_graph = await poll_execution_graph(
            shared_client,
            task_id=shared_task_id,
            expected_node_kinds={"task", "task_attempt", "retry", "status_event"},
        )

    async with app_client(workflow_app) as workflow_client:
        await run_workflow_flow(workflow_topology, workflow_task_id, workflow_observation_prefix)
        workflow_history = await poll_history(workflow_client, task_id=workflow_task_id, expected_count=2)
        workflow_latest = await fetch_latest_status(workflow_client, workflow_task_id)
        workflow_graph = await poll_execution_graph(
            workflow_client,
            task_id=workflow_task_id,
            expected_node_kinds={"task", "workflow_message", "stage_attempt", "status_event"},
        )

    shared_history_statuses = [event["status"] for event in shared_history["events"]]
    workflow_history_statuses = [event["status"] for event in workflow_history["events"]]
    shared_edge_kinds = {edge["kind"] for edge in shared_graph["edges"]}
    workflow_edge_kinds = {edge["kind"] for edge in workflow_graph["edges"]}

    assert attempts == 2
    assert shared_history_statuses == ["retrying", "completed"]
    assert shared_latest["event"]["status"] == "completed"
    assert shared_graph["summary"]["graph_completeness"] == "full"
    assert shared_graph["topology_kind"] == "shared_tasks_shared_status"
    assert {"received_by", "published_status", "retried_as"} <= shared_edge_kinds

    assert workflow_history_statuses == ["planning", "completed"]
    assert workflow_latest["event"]["status"] == "completed"
    assert workflow_graph["summary"]["graph_completeness"] == "full"
    assert workflow_graph["topology_kind"] == "shared_status_workflow"
    assert {"received_by", "entered_stage", "stage_transitioned_to", "published_status"} <= workflow_edge_kinds

    print("real_execution_graph_smoke: ok")
    print(f"shared_task_id={shared_task_id}")
    print(f"shared_history_statuses={shared_history_statuses}")
    print(f"shared_graph_nodes={len(shared_graph['nodes'])}")
    print(f"workflow_task_id={workflow_task_id}")
    print(f"workflow_history_statuses={workflow_history_statuses}")
    print(f"workflow_graph_nodes={len(workflow_graph['nodes'])}")


if __name__ == "__main__":
    asyncio.run(main())
