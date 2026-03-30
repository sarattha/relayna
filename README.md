# relayna

`relayna` is a Python library for shared RabbitMQ, Redis, and FastAPI plumbing
around task processing and live status delivery.

It provides:

- RabbitMQ task publishing and shared status fanout
- Broker-delayed retry and dead-letter utilities for worker consumers
- Named RabbitMQ topology classes for default and sharded aggregation flows
- First-class stage-inbox workflow topology for multi-stage agent pipelines
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
pip install https://github.com/sarattha/relayna/releases/download/v1.3.1/relayna-1.3.1-py3-none-any.whl
```

Or install from the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.3.1/relayna-1.3.1.tar.gz
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
    task_consumer_timeout_ms=600000,
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
- if `http_aliases` differs from `field_aliases`, routes/query params follow
  `http_aliases` but JSON bodies still follow `field_aliases`
- nested keys inside `payload`, `meta`, and `result` are not aliased

Batch-envelope workers also receive batch context on `TaskContext`:

- `context.batch_id`
- `context.batch_index`
- `context.batch_size`

Failed items from a batch envelope are retried individually when the worker has
`retry_policy=...`. Relayna does not execute the whole batch under one RabbitMQ
delivery: it fans the envelope out into one queue message per task before
running handlers, which avoids rerunning already-completed items when a later
item fails. See [docs/getting-started.md](docs/getting-started.md) for full
request/response examples and worker code.

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
            ),
            broker_dlq_queue_names=["tasks.queue.dlq", "aggregation.queue.0.dlq"],
        )
    )
```

When workers also receive `dlq_store=...`, this adds:

- `GET /dlq/queues`
- `GET /dlq/messages`
- `GET /dlq/messages/{dlq_id}`
- `POST /dlq/messages/{dlq_id}/replay`
- `GET /broker/dlq/queues` when `broker_dlq_queue_names=...` is configured

`GET /dlq/queues` reports queues known from indexed DLQ records and then augments
those queue names with live RabbitMQ counts. It does not discover every DLQ
queue that exists in RabbitMQ.

If you need broader visibility, configure `broker_dlq_queue_names=...` and use
`GET /broker/dlq/queues`. That endpoint inspects the configured candidate queue
names plus any queue names already present in the DLQ index. Broker-only queues
appear with `indexed_count=0` and `last_indexed_at=null`.

## Topologies

`relayna` currently ships five first-class topology classes:

- `SharedTasksSharedStatusTopology`
  One shared task queue and one shared status queue/stream.
- `SharedTasksSharedStatusShardedAggregationTopology`
  The same shared task and status plane, plus shard-owned aggregation worker
  queues bound to the status exchange.
- `RoutedTasksSharedStatusTopology`
  Shared status queue/stream plus routed task worker queues bound by `task_type`.
- `RoutedTasksSharedStatusShardedAggregationTopology`
  Routed task worker queues plus the shared status plane and shard-owned
  aggregation worker queues.
- `SharedStatusWorkflowTopology`
  One workflow topic exchange, one durable inbox queue per consuming stage, and
  one shared status queue/stream.

Aggregation messages published through the sharded aggregation topology stay on
the shared status exchange, so `StatusHub`, `StreamHistoryReader`, and SSE
consumers still observe them.

`SharedStatusWorkflowTopology` keeps workflow traffic and user-visible status
traffic on separate lanes:

- workflow messages move stage-to-stage over the workflow topic exchange
- each consuming stage owns one durable inbox queue
- status events still publish on the shared status exchange so existing
  `StatusHub`, history, and SSE behavior stays unchanged

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
child and parent workflows, and how to use `context.manual_retry(...)` to hand
the same `task_id` off to a different routed `task_type`.

Topology constructors also expose a small curated set of RabbitMQ queue
arguments for worker-owned queues:

- `task_consumer_timeout_ms`
- `task_single_active_consumer`
- `task_max_priority`
- `task_queue_type`
- `aggregation_consumer_timeout_ms`
- `aggregation_single_active_consumer`
- `aggregation_max_priority`
- `aggregation_queue_type`

For broker-specific queue arguments that Relayna does not model directly, use
the explicit mapping escape hatches such as `task_queue_kwargs`,
`task_queue_arguments_overrides`, `aggregation_queue_kwargs`, and
`status_queue_kwargs`.

Native queue-argument fields currently map like this:

- Task queue:
  `tasks_message_ttl_ms` -> `x-message-ttl`
- Task queue:
  `dead_letter_exchange` -> `x-dead-letter-exchange`
- Task queue:
  `task_consumer_timeout_ms` -> `x-consumer-timeout`
- Task queue:
  `task_single_active_consumer` -> `x-single-active-consumer`
- Task queue:
  `task_max_priority` -> `x-max-priority`
- Task queue:
  `task_queue_type` -> `x-queue-type`
- Status queue:
  `status_use_streams=True` -> `x-queue-type=stream`
- Status queue:
  `status_queue_ttl_ms` -> `x-expires`
- Status queue:
  `status_stream_max_length_gb` -> `x-max-length-bytes`
- Status queue:
  `status_stream_max_segment_size_mb` -> `x-stream-max-segment-size-bytes`
- Aggregation queue:
  `aggregation_consumer_timeout_ms` -> `x-consumer-timeout`
- Aggregation queue:
  `aggregation_single_active_consumer` -> `x-single-active-consumer`
- Aggregation queue:
  `aggregation_max_priority` -> `x-max-priority`
- Aggregation queue:
  `aggregation_queue_type` -> `x-queue-type`

Those topology timeout fields configure RabbitMQ queue arguments. They are
distinct from the runtime option `consume_timeout_seconds`, which controls how
long `TaskConsumer` and `AggregationConsumer` wait locally for the next message
before the consumer loop iterates again.

Generic queue-argument mappings are:

- Task queue:
  `task_queue_arguments_overrides`
- Task queue:
  `task_queue_kwargs`
- Status queue:
  `status_queue_arguments_overrides`
- Status queue:
  `status_queue_kwargs`
- Aggregation queue:
  `aggregation_queue_arguments_overrides`
- Aggregation queue:
  `aggregation_queue_kwargs`

`status_stream_initial_offset` remains a native topology field, but it affects
the stream consumer argument `x-stream-offset`, not queue declaration
arguments.

```python
topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    task_consumer_timeout_ms=600000,
    task_queue_kwargs={"x-overflow": "reject-publish"},
)
```

Task queue example with only native fields:

```python
topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    tasks_message_ttl_ms=30000,
    dead_letter_exchange="tasks.dlx",
    task_consumer_timeout_ms=600000,
    task_single_active_consumer=True,
    task_max_priority=10,
    task_queue_type="quorum",
)
```

Sharded aggregation example with native fields plus broker-specific kwargs:

```python
topology = SharedTasksSharedStatusShardedAggregationTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    shard_count=4,
    aggregation_consumer_timeout_ms=120000,
    aggregation_single_active_consumer=True,
    aggregation_queue_type="quorum",
    aggregation_queue_kwargs={"x-delivery-limit": 20},
)
```

Status queue example with stream settings plus generic queue args:

```python
topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    status_use_streams=True,
    status_stream_max_length_gb=2,
    status_stream_max_segment_size_mb=64,
    status_queue_kwargs={"x-initial-cluster-size": 3},
)
```

If the same RabbitMQ argument key is configured twice for one queue family,
Relayna raises `ValueError` instead of silently overriding it.

## Real-Stack Smoke Commands

These scripts exercise the library against real RabbitMQ and Redis services on
`localhost`:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/real_fastapi_status_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_task_worker_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_sharded_aggregation_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_queue_args_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_alias_batch_task_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_manual_retry_routed_smoke.py
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
