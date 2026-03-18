# Getting started

## Requirements

You need:

- Python `>=3.13`
- RabbitMQ
- Redis

## Install from GitHub Releases

Install the wheel:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.0.0/relayna-1.0.0-py3-none-any.whl
```

Or install the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.0.0/relayna-1.0.0.tar.gz
```

For local work in this repository:

```bash
uv sync --extra dev
```

## Topology overview

`relayna` now uses named topology classes as the primary RabbitMQ configuration
API.

- `SharedTasksSharedStatusTopology`: one shared task queue and one shared status
  queue/stream.
- `SharedTasksSharedStatusShardedAggregationTopology`: the same shared task and
  shared status plane, plus shard-bound aggregation worker queues on the status
  exchange.

`RelaynaTopologyConfig` still exists for backward compatibility, but new code
should prefer the topology classes in `relayna.topology`.

## Example: shared tasks + shared status

This is the default setup for task workers, shared status history, and FastAPI
status endpoints.

```python
from fastapi import FastAPI

from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.topology import SharedTasksSharedStatusTopology

topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

client = RelaynaRabbitClient(topology=topology)
await client.initialize()
await client.publish_task({"task_id": "task-123", "payload": {"kind": "demo"}})

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
    )
)
runtime = get_relayna_runtime(app)
app.include_router(
    create_status_router(
        sse_stream=runtime.sse_stream,
        history_reader=runtime.history_reader,
        latest_status_store=runtime.store,
    )
)
```

This exposes:

- `GET /events/{task_id}`
- `GET /history`
- `GET /status/{task_id}`

### Task worker example

```python
from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Task processing started.")
    await context.publish_status(status="completed", message="Task processing completed.")


consumer = TaskConsumer(rabbitmq=client, handler=handle_task)
await consumer.run_forever()
```

## Example: shared tasks + shared status + sharded aggregation

Use this topology when normal task workers stay on one shared task queue, but
aggregation work should be distributed across shard-owned queues such as
`aggregation.queue.0`, `aggregation.queue.1`, or subset queues like
`aggregation.queue.shards.1-3`.

Aggregation messages are still published on the shared status exchange, so
`StatusHub`, `StreamHistoryReader`, and SSE clients can see them like normal
status events.

```python
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.topology import SharedTasksSharedStatusShardedAggregationTopology

topology = SharedTasksSharedStatusShardedAggregationTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    shard_count=4,
)

client = RelaynaRabbitClient(topology=topology)
await client.initialize()
```

### Task worker publishes aggregation work

```python
from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Sub-task started.")

    await context.publish_aggregation_status(
        status="aggregating",
        message="Partial result ready for shard aggregation.",
        meta={"parent_task_id": "root-task-123"},
        result={"chunk_id": task.task_id, "value": 42},
    )


consumer = TaskConsumer(rabbitmq=client, handler=handle_task)
await consumer.run_forever()
```

`meta.parent_task_id` is required for aggregation publishing. `relayna`
computes the shard from that parent id, writes `meta.aggregation_shard`, sets
`meta.aggregation_role="aggregation"`, and publishes the event to a routing key
like `agg.0`.

### Aggregation worker runtime example

```python
import asyncio

from relayna.consumer import AggregationWorkerRuntime, TaskContext
from relayna.contracts import StatusEventEnvelope


async def aggregate(event: StatusEventEnvelope, context: TaskContext) -> None:
    shard = event.meta.get("aggregation_shard")
    await context.publish_status(
        status="aggregation-processed",
        message=f"Aggregation worker handled shard {shard}.",
        meta={
            "parent_task_id": event.meta["parent_task_id"],
            "aggregation_shard": shard,
        },
    )


runtime = AggregationWorkerRuntime(
    topology=topology,
    handler=aggregate,
    shard_groups=[[0], [1, 2, 3]],
)

await runtime.start()
try:
    await asyncio.Event().wait()
finally:
    await runtime.stop()
```

`shard_groups=[[0], [1, 2, 3]]` means:

- one aggregation consumer owns shard `0`
- a second aggregation consumer owns shards `1`, `2`, and `3`

## Redis-backed status history

```python
from redis.asyncio import Redis

from relayna.status_store import RedisStatusStore

redis = Redis.from_url("redis://localhost:6379/0")
store = RedisStatusStore(redis, prefix="relayna", ttl_seconds=86400, history_maxlen=50)
```

## Status bridge

```python
from relayna.status_hub import StatusHub

hub = StatusHub(rabbitmq=client, store=store)
await hub.run_forever()
```

## End-to-end composition

For the default shared topology:

1. Publish tasks with `RelaynaRabbitClient`.
2. Consume tasks with `TaskConsumer`.
3. Publish status updates from handlers with `TaskContext.publish_status(...)`.
4. Run `StatusHub` to bridge shared status messages into `RedisStatusStore`.
5. Expose `create_status_router(...)` in your FastAPI service for history and SSE.

For the sharded aggregation topology:

1. Publish tasks with the same shared task plane.
2. Task workers publish aggregation work with `publish_aggregation_status(...)`.
3. Aggregation workers consume shard-owned queues through `AggregationWorkerRuntime`.
4. `StatusHub`, `StreamHistoryReader`, and SSE still observe aggregation events from the shared status exchange.
