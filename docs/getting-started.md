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

## FastAPI SSE integration

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

## End-to-end example

The intended composition is:

1. Publish tasks with `RelaynaRabbitClient`.
2. Consume tasks with `TaskConsumer`.
3. Publish status updates from handlers with `TaskContext.publish_status(...)`.
4. Run `StatusHub` to bridge shared status messages into `RedisStatusStore`.
5. Expose `create_status_router(...)` in your FastAPI service for history and SSE.
