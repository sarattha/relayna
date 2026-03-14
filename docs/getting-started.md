# Getting started

## Install

Core install:

```bash
uv add relayna
```

For local development in this repository:

```bash
uv sync --extra dev
```

## Basic RabbitMQ setup

```python
from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient

config = RelaynaTopologyConfig(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

client = RelaynaRabbitClient(config)
await client.initialize()
await client.publish_task({"task_id": "task-123", "payload": {"kind": "demo"}})
```

## Worker consumption

```python
from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Task processing started.")


consumer = TaskConsumer(rabbitmq=client, handler=handle_task)
await consumer.run_forever()
```

By default, handler failures reject the message without requeue. Set
`failure_action=FailureAction.REQUEUE` if you want RabbitMQ requeue behavior.

## Redis-backed status history

```python
from redis.asyncio import Redis

from relayna.status_store import RedisStatusStore

redis = Redis.from_url("redis://localhost:6379/0")
store = RedisStatusStore(redis, prefix="relayna", ttl_seconds=86400, history_maxlen=50)

await store.set_history(
    "task-123",
    {"task_id": "task-123", "status": "queued", "message": "Task accepted."},
)
history = await store.get_history("task-123")
```

## SSE streaming

```python
from relayna.sse import SSEStatusStream

stream = SSEStatusStream(store=store)
```

Use `stream.stream(task_id)` as the body for an SSE response.

By default, Relayna emits SSE keepalive comments every 15 seconds and supports
resume via the standard `Last-Event-ID` header. Relayna status publishers
generate `event_id` automatically when missing, which makes resume work
consistently for the normal publish paths.

## FastAPI router

```python
from fastapi import FastAPI

from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology_config=config,
        redis_url="redis://localhost:6379/0",
    )
)
runtime = get_relayna_runtime(app)
app.include_router(create_status_router(sse_stream=runtime.sse_stream))
```

If you also need stream replay history, pass a `StreamHistoryReader` instance as
`history_reader` or use `runtime.history_reader`.

`create_relayna_lifespan()` is the preferred setup for FastAPI services. Manual
composition is still supported when you want to manage RabbitMQ, Redis, or
`StatusHub` yourself.

If you pass `latest_status_store=runtime.store` into `create_status_router(...)`,
Relayna also exposes `GET /status/{task_id}` for the latest Redis-backed status
event. The route returns `404` when no status exists yet.
