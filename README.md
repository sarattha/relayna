# relayna

`relayna` is a small shared infrastructure library for services that need:

- RabbitMQ task publishing
- RabbitMQ status fanout
- Redis-backed status history and pubsub
- Server-Sent Events (SSE) for live client updates
- Stream replay for debugging or history endpoints
- Optional FastAPI route helpers

It is designed as reusable plumbing, not as a full application framework. The package gives you the shared transport and event-stream pieces so individual services can keep their own business logic, payload schemas, and API contracts.

## What it provides

- A canonical task envelope and status event envelope
- RabbitMQ topology declaration for task and status exchanges/queues
- A worker consumer helper for task queue processing
- Configurable routing strategies for per-task or sharded status routing
- A Redis status store with event deduplication, TTL, and bounded history
- A status hub that bridges RabbitMQ status events into Redis
- SSE helpers that replay history first, then stream live updates
- A stream history reader for stream-backed RabbitMQ queues
- A small FastAPI router factory for `/events/{task_id}` and `/history`

## Requirements

- Python `>=3.13`
- RabbitMQ
- Redis
- [`uv`](https://docs.astral.sh/uv/) for local development in this repository

## Installation

Install from a package index:

```bash
uv add relayna
```

For local development in this repository:

```bash
uv sync --extra dev
```

## Public API style

The package root is intentionally minimal. Import concrete APIs from submodules:

```python
from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.status_store import RedisStatusStore
```

The package root only exposes `relayna.__version__`.

## Architecture

```mermaid
flowchart LR
    A["Producer service"] -->|publish task| B["RabbitMQ tasks exchange"]
    C["Worker service"] -->|publish status| D["RabbitMQ status exchange / stream"]
    D --> E["StatusHub"]
    E --> F["Redis history list"]
    E --> G["Redis pubsub channel"]
    F --> H["SSEStatusStream"]
    G --> H
    H --> I["FastAPI SSE endpoint"]
    D --> J["StreamHistoryReader"]
    J --> K["FastAPI history endpoint"]
```

Typical flow:

1. A producer publishes a task to RabbitMQ.
2. Workers publish status events to the shared status exchange.
3. `StatusHub` consumes those status messages and writes normalized events into Redis.
4. `SSEStatusStream` replays stored history oldest-to-newest, then subscribes to Redis pubsub for live events.
5. `StreamHistoryReader` can replay status history directly from a RabbitMQ stream-backed queue.

## Core concepts

### Tasks

`relayna.contracts.TaskEnvelope` is the canonical task transport shape. It includes:

- `task_id`
- `payload`
- `created_at`
- `service`
- `task_type`
- `correlation_id`
- `spec_version`

Extra fields are allowed so services can attach their own metadata.

### Status events

`relayna.contracts.StatusEventEnvelope` is the canonical status transport shape. It includes:

- `task_id`
- `status`
- `timestamp`
- `message`
- `meta`
- `result`
- `correlation_id`
- `event_id`
- `service`
- `spec_version`

`as_transport_dict()` emits a JSON-friendly payload and ensures `correlation_id` falls back to `task_id`.

### Compatibility aliases

Some clients still use `documentId` instead of `task_id`. `relayna.contracts` includes:

- `normalize_event_aliases()` to normalize inbound payloads into canonical keys
- `denormalize_document_aliases()` to keep `documentId` visible for document-oriented clients

## Quickstart

### 1. Configure RabbitMQ topology

```python
from relayna.config import RelaynaTopologyConfig

config = RelaynaTopologyConfig(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)
```

### 2. Publish tasks and statuses

```python
from relayna.contracts import StatusEventEnvelope, TaskEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

client = RelaynaRabbitClient(config)
await client.initialize()

await client.publish_task(
    TaskEnvelope(
        task_id="task-123",
        payload={"kind": "demo"},
        service="api",
    )
)

await client.publish_status(
    StatusEventEnvelope(
        task_id="task-123",
        status="queued",
        message="Task accepted.",
        service="api",
    )
)
```

### 3. Consume tasks in a worker

```python
from relayna.consumer import TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="translating", message="Translation started.")


consumer = TaskConsumer(rabbitmq=client, handler=handle_task)
await consumer.run_forever()
```

By default, handler exceptions reject the message without requeue. Set
`failure_action=FailureAction.REQUEUE` if your worker should retry via RabbitMQ.

### 4. Store and stream statuses from Redis

```python
from redis.asyncio import Redis

from relayna.status_store import RedisStatusStore

redis = Redis.from_url("redis://localhost:6379/0")
store = RedisStatusStore(
    redis,
    prefix="relayna",
    ttl_seconds=86400,
    history_maxlen=50,
)
```

### 5. Bridge RabbitMQ statuses into Redis

```python
from relayna.status_hub import StatusHub

hub = StatusHub(rabbitmq=client, store=store)
await hub.run_forever()
```

`StatusHub`:

- consumes the shared status queue
- ACKs messages before Redis writes
- normalizes aliases like `documentId`
- removes sensitive meta keys such as `auth_token` by default
- writes best-effort status history into Redis

### 6. Expose SSE and history endpoints with FastAPI

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
app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))
```

This gives you:

- `GET /events/{task_id}`: SSE stream
- `GET /history`: stream replay endpoint

## End-to-end example

The following layout is the intended composition:

```python
from fastapi import FastAPI

from relayna.config import RelaynaTopologyConfig
from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime

config = RelaynaTopologyConfig(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology_config=config,
        redis_url="redis://localhost:6379/0",
    )
)
runtime = get_relayna_runtime(app)
app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))
```

With this setup, Relayna manages:

- RabbitMQ initialization and shutdown
- Redis client creation and shutdown
- `StatusHub` background task startup and shutdown
- construction of `RedisStatusStore`, `SSEStatusStream`, and `StreamHistoryReader`

Your application still owns:

- creating the FastAPI app
- including Relayna's router where you want it
- choosing prefixes, tags, dependencies, and whether to expose history

Manual composition remains supported if you need tighter control over lifecycle or wiring.

## Configuration

`RelaynaTopologyConfig` centralizes RabbitMQ topology and queue settings.

| Field | Purpose | Default |
| --- | --- | --- |
| `rabbitmq_url` | Base RabbitMQ connection string | required |
| `tasks_exchange` | Task exchange name | required |
| `tasks_queue` | Task queue name | required |
| `tasks_routing_key` | Routing key for submitted tasks | required |
| `status_exchange` | Status exchange name | required |
| `status_queue` | Shared status queue or stream name | required |
| `dead_letter_exchange` | Optional DLX for task queue | `None` |
| `prefetch_count` | Default channel prefetch | `1` |
| `tasks_message_ttl_ms` | Optional task message TTL | `None` |
| `status_use_streams` | Declare status queue as a RabbitMQ stream | `True` |
| `status_queue_ttl_ms` | TTL for non-stream status queue | `None` |
| `status_stream_max_length_gb` | Stream max length in GB | `None` |
| `status_stream_max_segment_size_mb` | Stream segment size in MB | `None` |
| `status_stream_initial_offset` | Default stream consume offset | `"last"` |

You can also build this config from an existing settings object:

```python
config = RelaynaTopologyConfig.from_settings(settings)
```

`from_settings()` supports both lowercase attribute names and uppercase environment-style names such as `RABBITMQ_URL` and `STATUS_EXCHANGE`.

## RabbitMQ helpers

### `RelaynaRabbitClient`

Responsible for:

- opening a robust `aio-pika` connection
- declaring task and status exchanges
- declaring and binding task/status queues
- publishing JSON task messages
- publishing JSON status messages
- opening ad hoc channels for consumers or history readers

The client declares:

- a durable direct exchange for tasks
- a durable topic exchange for statuses
- a durable task queue bound to the configured task routing key
- a durable status queue bound with routing key `#`

### Routing strategies

`relayna.rabbitmq` includes two built-in routing strategies:

- `TaskIdRoutingStrategy`
  - tasks use the configured static task routing key
  - statuses route by `task_id`
- `ShardRoutingStrategy`
  - statuses route to deterministic shards like `agg.0`, `agg.1`, and so on
  - by default it prefers `meta.parent_task_id`, then falls back to `task_id`

### Direct queue publishing

`DirectQueuePublisher` publishes JSON payloads to a queue through RabbitMQ's default exchange. Use it when you need simple queue-targeted publishing instead of exchange-based routing.

## Worker consumption

`TaskConsumer` provides a shared worker loop for consuming `TaskEnvelope`
messages from the configured task queue.

Default behavior:

- validates each message as a `TaskEnvelope`
- passes the task plus a `TaskContext` into your async handler
- acknowledges successful messages
- rejects malformed JSON and invalid envelopes without requeue
- rejects handler failures without requeue by default
- can optionally publish `processing`, `completed`, and `failed` lifecycle statuses

`TaskContext.publish_status(...)` lets handlers publish status updates without
repeating `task_id` and correlation-id plumbing.

## Redis status store

`RedisStatusStore` is the hot-path state store used by SSE consumers.

Behavior:

- stores history in a Redis list using `LPUSH`
- trims history to `history_maxlen`
- optionally applies TTL to both dedupe keys and history keys
- publishes live updates on a task-specific Redis pubsub channel
- deduplicates events using `event_id` when available
- falls back to a content hash when `event_id` is missing

History is stored newest-first internally. `SSEStatusStream` reverses that order during replay so clients receive events oldest-to-newest.

## SSE behavior

`SSEStatusStream` combines stored history with live pubsub events.

Default behavior:

- emits an initial `ready` SSE event
- replays Redis history oldest-to-newest
- subscribes to the Redis pubsub channel for the task
- emits each payload as an SSE `status` event
- closes automatically when a terminal status is seen

The default terminal statuses are:

- `completed`
- `failed`

You can override that by passing a custom `TerminalStatusSet`.

If you need document-oriented client payloads, use:

```python
from relayna.sse import SSEStatusStream, document_output_adapter

stream = SSEStatusStream(store=store, output_adapter=document_output_adapter)
```

## History replay behavior

`StreamHistoryReader` reads status history directly from a RabbitMQ stream-backed queue.

Highlights:

- works from `first`, `last`, or an integer stream offset
- optionally filters to a single `task_id`
- can stop after terminal statuses
- supports `max_seconds` and `max_scan` guardrails
- raises `RuntimeError` if stream replay is required but the configured status queue is not a stream

This is intended for debugging, operational endpoints, or lightweight history APIs. It is not a replacement for a permanent analytics store.

## FastAPI integration

`create_relayna_lifespan()` is the preferred integration path for FastAPI services. It manages RabbitMQ, Redis, `StatusHub`, and the shared runtime objects used by the router.

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
app.include_router(create_status_router(sse_stream=runtime.sse_stream, history_reader=runtime.history_reader))
```

`get_relayna_runtime()` returns the shared runtime so services can wire Relayna into their own app structure without giving up route ownership.

`create_status_router()` returns an `APIRouter` with:

- an SSE endpoint at `/events/{task_id}` by default
- an optional history endpoint at `/history` when `history_reader` is provided

The history endpoint accepts:

- `task_id`
- `start_offset`
- `max_seconds`
- `max_scan`

To keep the replay bounded, the route returns `422` unless at least one of `task_id`, `max_seconds`, or `max_scan` is supplied.

`sse_response()` is also exported if you want to wire the streaming response manually.

If you need advanced composition, you can still instantiate `StatusHub`, `RedisStatusStore`, `SSEStatusStream`, and `StreamHistoryReader` manually and keep using `create_status_router()` on its own.

## Module guide

- `relayna.contracts`
  - canonical task and status envelopes
  - terminal status handling
  - alias normalization helpers
- `relayna.config`
  - topology config and settings adapter
- `relayna.rabbitmq`
  - robust RabbitMQ client
  - routing strategies
  - direct queue publisher
- `relayna.consumer`
  - task consumer loop
  - task handler protocol
  - task context and lifecycle status config
- `relayna.status_store`
  - Redis-backed task history and pubsub store
- `relayna.status_hub`
  - RabbitMQ-to-Redis status bridge
- `relayna.sse`
  - SSE stream helper and document output adapter
- `relayna.history`
  - RabbitMQ stream replay helper
- `relayna.fastapi`
  - FastAPI lifespan helper, runtime accessor, router factory, and SSE response helper

## Development

Run tests:

```bash
uv run --extra dev pytest -q
```

Build packages:

```bash
uv build
```

This produces:

- `dist/relayna-<version>.tar.gz`
- `dist/relayna-<version>-py3-none-any.whl`

## Repository docs

More focused docs live under `docs/`:

- [docs/index.md](docs/index.md)
- [docs/getting-started.md](docs/getting-started.md)
- [docs/components.md](docs/components.md)
- [docs/development.md](docs/development.md)
