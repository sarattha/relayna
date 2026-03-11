# relayna

Shared RabbitMQ and Redis infrastructure for task submission, status fanout,
stream replay, and FastAPI event endpoints.

## Installation

For local development in this repository:

```bash
uv sync --extra dev
```

## Import style

The package root is intentionally minimal. Import concrete APIs from
submodules:

```python
from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.status_store import RedisStatusStore
```

The package root only exposes `relayna.__version__`.

## Quickstart

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

## Components

- `relayna.contracts`: canonical task and status envelopes plus alias helpers
- `relayna.config`: topology configuration
- `relayna.rabbitmq`: publishing, routing, and queue helpers
- `relayna.status_store`: Redis-backed status history and pubsub
- `relayna.status_hub`: RabbitMQ-to-Redis status bridge
- `relayna.sse`: SSE streaming helper
- `relayna.history`: stream replay helper
- `relayna.fastapi`: optional FastAPI router helper

## Development

Run tests with:

```bash
uv run --extra dev pytest -q
```

Build packages with:

```bash
uv build
```

Additional documentation:

- [docs/index.md](docs/index.md)
- [docs/getting-started.md](docs/getting-started.md)
- [docs/components.md](docs/components.md)
- [docs/development.md](docs/development.md)
