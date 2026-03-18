# relayna

`relayna` is a Python library for shared RabbitMQ, Redis, and FastAPI plumbing
around task processing and live status delivery.

It provides:

- RabbitMQ task publishing and shared status fanout
- Named RabbitMQ topology classes for default and sharded aggregation flows
- Redis-backed status history and pubsub
- Server-Sent Events replay plus live updates
- RabbitMQ stream replay for history/debug endpoints
- FastAPI lifecycle and route helpers
- Best-effort runtime observability hooks

## Requirements

- Python `>=3.13`
- RabbitMQ
- Redis

## Install

GitHub Releases are the canonical installation source for v1.

Install the wheel directly:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.1.0/relayna-1.1.0-py3-none-any.whl
```

Or install from the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.1.0/relayna-1.1.0.tar.gz
```

For local development in this repository:

```bash
uv sync --extra dev
```

## Quickstart

```python
from fastapi import FastAPI

from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.topology import SharedTasksSharedStatusTopology

topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

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

This setup gives you:

- `GET /events/{task_id}` for SSE status updates
- `GET /history` for bounded stream replay
- `GET /status/{task_id}` for the latest Redis-backed status

## Topologies

`relayna` currently ships two first-class topology classes:

- `SharedTasksSharedStatusTopology`
  One shared task queue and one shared status queue/stream.
- `SharedTasksSharedStatusShardedAggregationTopology`
  The same shared task and status plane, plus shard-owned aggregation worker
  queues bound to the status exchange.

Aggregation messages published through the sharded aggregation topology stay on
the shared status exchange, so `StatusHub`, `StreamHistoryReader`, and SSE
consumers still observe them.

See [docs/getting-started.md](docs/getting-started.md) for concrete examples of
both topologies, including `AggregationWorkerRuntime`.
For planned first-class support of multi-stage workflow topologies, see
[docs/workflow-topology-plan.md](docs/workflow-topology-plan.md).

## Public API

The v1 semver-stable API is the documented surface of these submodules:

- `relayna.topology`
- `relayna.config`
- `relayna.contracts`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status_store`
- `relayna.status_hub`
- `relayna.sse`
- `relayna.history`
- `relayna.fastapi`
- `relayna.observability`

The package root is intentionally minimal and only exports `relayna.__version__`.

## Docs and releases

- Documentation: [sarattha.github.io/relayna](https://sarattha.github.io/relayna/)
- GitHub Releases: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

`relayna` is released under the MIT license. See [LICENSE](LICENSE).
