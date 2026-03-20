# Getting started

## Requirements

You need:

- Python `>=3.13`
- RabbitMQ
- Redis

## Install from GitHub Releases

Install the wheel:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.1.5/relayna-1.1.5-py3-none-any.whl
```

Or install the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.1.5/relayna-1.1.5.tar.gz
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

## Topology naming guidance

Shared topology resources are usually namespaced by exchange, queue, and Redis
prefix values. For sharded topologies, remember that the default aggregation
queue names such as `aggregation.queue.0` and
`aggregation.queue.shards.1-3` are global durable queues. If multiple
deployments, smoke tests, or local stacks share one RabbitMQ vhost, give the
aggregation queues a deployment-specific prefix too.

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

For a detailed observability walkthrough, see [Observability](observability.md).

### Task worker example

```python
from relayna.consumer import RetryPolicy, TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Task processing started.")
    await context.publish_status(status="completed", message="Task processing completed.")


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
)
await consumer.run_forever()
```

When `retry_policy` is enabled, Relayna creates a broker-delayed retry queue and
a per-source DLQ. Malformed JSON and invalid envelopes go straight to the DLQ;
handler failures retry until `max_retries` is exhausted, then dead-letter.

### Retry and DLQ headers

Relayna keeps retry metadata in RabbitMQ message headers. The task or
aggregation payload body is not rewritten.

Headers added by Relayna:

- `headers["x-relayna-retry-attempt"]`
  The current retry attempt number on the republished message.
  `0` means the original delivery. `1` means the first delayed retry.
- `headers["x-relayna-max-retries"]`
  The configured retry limit for this consumer. Relayna compares the current
  attempt against this value to decide whether to retry again or dead-letter.
- `headers["x-relayna-source-queue"]`
  The original worker queue that owns the message. Relayna uses this to make it
  clear which queue the retry or DLQ message came from.
- `headers["x-relayna-failure-reason"]`
  A short machine-readable failure category such as `handler_error`,
  `malformed_json`, or `invalid_envelope`.
- `headers["x-relayna-exception-type"]`
  The Python exception type name when one exists, such as `RuntimeError` or
  `ValidationError`. Relayna sets this to `null`/`None` style absence when the
  failure was not caused by a raised exception, such as malformed JSON decode.

Example DLQ message metadata after a handler fails on the last allowed retry:

```python
headers = {
    "x-relayna-retry-attempt": 3,
    "x-relayna-max-retries": 3,
    "x-relayna-source-queue": "tasks.queue",
    "x-relayna-failure-reason": "handler_error",
    "x-relayna-exception-type": "RuntimeError",
}
```

Example task body in that same DLQ message:

```json
{
  "task_id": "task-123",
  "payload": {
    "kind": "demo"
  }
}
```

That example means:

- the task has already been retried three times
- the consumer limit was three retries
- the message originated from `tasks.queue`
- the final failure came from application handler code
- the thrown exception type was `RuntimeError`

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
    aggregation_queue_template="aggregation.queue.dev.{shard}",
    aggregation_queue_name_prefix="aggregation.queue.dev.shards",
)

client = RelaynaRabbitClient(topology=topology)
await client.initialize()
```

### Task worker publishes aggregation work

```python
from relayna.consumer import RetryPolicy, RetryStatusConfig, TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Sub-task started.")

    await context.publish_aggregation_status(
        status="aggregating",
        message="Partial result ready for shard aggregation.",
        meta={"parent_task_id": "root-task-123"},
        result={"chunk_id": task.task_id, "value": 42},
    )


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=15000),
    retry_statuses=RetryStatusConfig(enabled=True),
)
await consumer.run_forever()
```

`meta.parent_task_id` is required for aggregation publishing. `relayna`
computes the shard from that parent id, writes `meta.aggregation_shard`, sets
`meta.aggregation_role="aggregation"`, and publishes the event to a routing key
like `agg.0`.

### Aggregation worker runtime example

```python
import asyncio

from relayna.consumer import AggregationWorkerRuntime, RetryPolicy, RetryStatusConfig, TaskContext
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
    retry_policy=RetryPolicy(max_retries=3, delay_ms=15000),
    retry_statuses=RetryStatusConfig(enabled=True),
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

### Direct aggregation-queue probe example

Use this when you want to validate shard routing directly against RabbitMQ in a
real environment.

```python
import json

from relayna.contracts import StatusEventEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

probe = RelaynaRabbitClient(topology=topology, connection_name="aggregation-probe")
await probe.initialize()

queue_name = await probe.ensure_aggregation_queue(shards=[0, 1, 2, 3])
await probe.publish_aggregation_status(
    StatusEventEnvelope(
        task_id="agg-task-123",
        status="aggregating",
        message="Aggregation input ready.",
        meta={"parent_task_id": "parent-123"},
    )
)

channel = await probe.acquire_channel(prefetch=1)
queue = await channel.declare_queue(
    queue_name,
    durable=True,
    arguments=topology.aggregation_queue_arguments() or None,
)
message = await queue.get(timeout=5.0, fail=True)
payload = json.loads(message.body.decode("utf-8"))
await message.ack()
```

`payload["meta"]["aggregation_shard"]` tells you which shard routing key was
used, and the fact that the queue received the message proves that the
aggregation queue binding is live.

### Custom SSE terminal statuses

If your aggregation workflow finishes on a status other than `completed` or
`failed`, pass a `TerminalStatusSet` to FastAPI so `/events/{task_id}` streams
terminate correctly.

```python
from relayna.contracts import TerminalStatusSet
from relayna.fastapi import create_relayna_lifespan

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        sse_terminal_statuses=TerminalStatusSet({"aggregation-processed"}),
    )
)
```

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

## Real-environment smoke commands

If RabbitMQ is running at `localhost:5672` and Redis at `localhost:6379`, these
scripts exercise both topology families against the real stack:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/real_fastapi_status_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_task_worker_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_sharded_aggregation_smoke.py
```
