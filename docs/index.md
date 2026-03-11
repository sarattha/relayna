# relayna docs

`relayna` provides shared RabbitMQ and Redis infrastructure for task submission,
status fanout, stream replay, and FastAPI event endpoints.

## Import style

The package root is intentionally minimal. Import from submodules:

```python
from relayna.config import RelaynaTopologyConfig
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.status_store import RedisStatusStore
```

Avoid broad imports such as:

```python
from relayna import RelaynaRabbitClient
```

## Guides

- [Getting started](getting-started.md)
- [Components](components.md)
- [Development](development.md)
