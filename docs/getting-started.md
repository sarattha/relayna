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

## FastAPI router

```python
from fastapi import FastAPI

from relayna.fastapi import create_status_router

app = FastAPI()
app.include_router(create_status_router(sse_stream=stream))
```

If you also need stream replay history, pass a `StreamHistoryReader` instance as
`history_reader`.
