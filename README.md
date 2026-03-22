# relayna

`relayna` is a Python library for shared RabbitMQ, Redis, and FastAPI plumbing
around task processing and live status delivery.

It provides:

- RabbitMQ task publishing and shared status fanout
- Broker-delayed retry and dead-letter utilities for worker consumers
- Named RabbitMQ topology classes for default and sharded aggregation flows
- Redis-backed status history and pubsub
- Redis-backed DLQ indexing and replay helpers
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
pip install https://github.com/sarattha/relayna/releases/download/v1.2.0/relayna-1.2.0-py3-none-any.whl
```

Or install from the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.2.0/relayna-1.2.0.tar.gz
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

## Contract Aliases And Batch Tasks

Use `ContractAliasConfig` when producers and HTTP clients should speak aliased
top-level envelope fields.

```python
from relayna.contracts import ContractAliasConfig
from relayna.rabbitmq import RelaynaRabbitClient

alias_config = ContractAliasConfig(
    field_aliases={
        "task_id": "attempt_id",
        "correlation_id": "request_id",
        "service": "source_service",
        "task_type": "job_type",
    }
)
client = RelaynaRabbitClient(topology=topology, alias_config=alias_config)
await client.initialize()

await client.publish_task(
    {
        "attempt_id": "attempt-123",
        "request_id": "req-123",
        "source_service": "billing-api",
        "job_type": "invoice.render",
        "payload": {"kind": "demo"},
    }
)
await client.publish_tasks(
    [
        {
            "attempt_id": "attempt-123",
            "request_id": "req-123",
            "source_service": "billing-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
        {
            "attempt_id": "attempt-124",
            "request_id": "req-124",
            "source_service": "billing-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
    ],
    mode="batch_envelope",
    batch_id="batch-123",
    meta={"source": "bulk-api"},
)
```

When the same `alias_config` is passed into `create_relayna_lifespan(...)` and
`create_status_router(...)`:

- producers can send `attempt_id` instead of `task_id`
- producers can also alias other top-level fields such as `correlation_id`,
  `service`, and `task_type`
- workers still receive canonical `TaskEnvelope` fields internally
- `GET /events/{attempt_id}`, `GET /status/{attempt_id}`, and
  `GET /history?attempt_id=...` use the aliased name
- HTTP responses and SSE payloads emit the configured aliases
- nested keys inside `payload`, `meta`, and `result` are not aliased

Batch-envelope workers also receive batch context on `TaskContext`:

- `context.batch_id`
- `context.batch_index`
- `context.batch_size`

Failed items from a batch envelope are retried individually when the worker has
`retry_policy=...`. See [docs/getting-started.md](docs/getting-started.md) for
full request/response examples and worker code.

## DLQ monitoring API

DLQ payload inspection is opt-in. Relayna stores message-level DLQ detail in
Redis and uses RabbitMQ only for live queue counts and replay transport, since
classic queues do not support a read-only payload peek over AMQP.

```python
from fastapi import FastAPI

from relayna.dlq import DLQService
from relayna.fastapi import create_dlq_router, create_relayna_lifespan, get_relayna_runtime

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        dlq_store_prefix="relayna-dlq",
    )
)

runtime = get_relayna_runtime(app)
if runtime.dlq_store is not None:
    app.include_router(
        create_dlq_router(
            dlq_service=DLQService(
                rabbitmq=runtime.rabbitmq,
                dlq_store=runtime.dlq_store,
                status_store=runtime.store,
            )
        )
    )
```

When workers also receive `dlq_store=...`, this adds:

- `GET /dlq/queues`
- `GET /dlq/messages`
- `GET /dlq/messages/{dlq_id}`
- `POST /dlq/messages/{dlq_id}/replay`

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

When multiple sharded deployments share the same RabbitMQ vhost, namespace the
aggregation queues with `aggregation_queue_template` and
`aggregation_queue_name_prefix`. The default shard queue names are global
durable queues, so local smoke runs or multiple environments can interfere if
they reuse the same queue names.

See [docs/getting-started.md](docs/getting-started.md) for concrete examples of
both topologies, including `AggregationWorkerRuntime`, `RetryPolicy`, and
retry/DLQ-enabled workers. The getting-started guide also documents every
`x-relayna-*` retry header with a concrete DLQ example, plus when to use
`context.publish_status(...)` vs `context.publish_aggregation_status(...)` for
child and parent workflows.

## Real-Stack Smoke Commands

These scripts exercise the library against real RabbitMQ and Redis services on
`localhost`:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/real_fastapi_status_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_task_worker_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_sharded_aggregation_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_alias_batch_task_smoke.py
```

## Public API

The v1 semver-stable API is the documented surface of these submodules:

- `relayna.topology`
- `relayna.contracts`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status_store`
- `relayna.status_hub`
- `relayna.sse`
- `relayna.history`
- `relayna.fastapi`
- `relayna.dlq`
- `relayna.observability`

The package root is intentionally minimal and only exports `relayna.__version__`.

## Docs and releases

- Documentation: [sarattha.github.io/relayna](https://sarattha.github.io/relayna/)
- Observability guide: [docs/observability.md](docs/observability.md)
- GitHub Releases: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

`relayna` is released under the MIT license. See [LICENSE](LICENSE).
