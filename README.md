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
- Task execution graph reconstruction with Mermaid and Studio rendering support

## Requirements

- Python `>=3.13`
- RabbitMQ
- Redis

## Install

GitHub Releases are the canonical installation source for v1.

Install the latest SDK wheel directly:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.5/relayna-1.4.5-py3-none-any.whl
```

Or install from the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.5/relayna-1.4.5.tar.gz
```

For local development in this repository:

```bash
uv sync --extra dev
```

## Quickstart

```python
from fastapi import FastAPI

from relayna.api import create_relayna_lifespan, create_status_router, get_relayna_runtime
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

## SDK vs Studio

`relayna` is the service-side SDK. It owns:

- RabbitMQ topology declarations and publish helpers
- Worker runtimes, retry behavior, and workflow transport
- Redis-backed status, DLQ, and observability persistence
- FastAPI route factories for status, capabilities, workflow, execution graph,
  events feed, and optional worker health

`relayna-studio` is the separate control-plane deployment. It owns:

- A centralized service registry
- Federated reads across registered Relayna services
- Centralized event ingestion, health refresh, and retained task search
- The Studio backend under `studio/backend/` and the React frontend under
  `apps/studio/`

The practical boundary is simple: your service integrates `relayna`; operators
deploy Studio separately when they want a centralized control plane.

## Execution Graphs

Relayna can reconstruct a per-task execution graph from persisted runtime
observations, Redis status history, DLQ records, and aggregation lineage.

The graph model is topology-aware:

- shared-task topologies render task attempts, retries, DLQ edges, and status
  publishes
- sharded aggregation topologies attach child tasks with `aggregated_into`
  edges
- workflow topologies add explicit `workflow_message` and `stage_attempt`
  nodes for stage-to-stage causality

Wire the route into FastAPI:

```python
from fastapi import FastAPI
from redis.asyncio import Redis

from relayna.api import create_execution_router, create_relayna_lifespan, get_relayna_runtime
from relayna.consumer import RetryPolicy, TaskConsumer
from relayna.observability import RedisObservationStore, make_redis_observation_sink

observation_store_prefix = "relayna-observations"
worker_observation_store = RedisObservationStore(
    Redis.from_url("redis://localhost:6379/0"),
    prefix=observation_store_prefix,
    ttl_seconds=86400,
    history_maxlen=500,
)

consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
    observation_sink=make_redis_observation_sink(worker_observation_store),
)

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        observation_store_prefix=observation_store_prefix,
        observation_store_ttl_seconds=86400,
        observation_history_maxlen=500,
    )
)
runtime = get_relayna_runtime(app)
app.include_router(create_execution_router(execution_graph_service=runtime.execution_graph_service))
```

This adds `GET /executions/{task_id}/graph`. When observation history is
available, `summary.graph_completeness` is `"full"`. When only status and DLQ
history are available, the graph still returns but is marked `"partial"`.

Relayna also ships:

- `execution_graph_mermaid(graph)` for docs and debugging output
- the separate `relayna-studio` deployment package, which exposes
  `build_execution_view(graph)` for Studio payloads and the React Flow app in
  `apps/studio/`

See [docs/execution-graphs.md](docs/execution-graphs.md) for the full response
shape, node and edge kinds, topology-specific behavior, and rendering examples.

## Package ownership

Relayna v2 is organized around package roots with explicit responsibility
boundaries:

- `relayna.topology`
  Owns topology declarations, routing strategies, workflow topology shapes, and
  topology graph helpers.
- `relayna.contracts`
  Owns canonical task, status, and workflow envelopes plus alias and
  compatibility helpers.
- `relayna.rabbitmq`
  Owns RabbitMQ transport behavior: client lifecycle, declarations, publish
  helpers, routing resolution, and retry infrastructure.
- `relayna.consumer`
  Owns worker runtimes, handler contexts, lifecycle helpers, middleware, and
  idempotency support.
- `relayna.status`
  Owns user-visible status state: Redis latest/history storage, the
  RabbitMQ-to-Redis `StatusHub`, SSE delivery, and bounded stream replay.
- `relayna.api`
  Owns FastAPI runtime wiring and route factories. It composes `relayna.status`,
  `relayna.rabbitmq`, and optional `relayna.dlq`; it does not own transport or
  storage primitives itself.
- `relayna.dlq`
  Owns DLQ models, Redis-backed indexing, replay orchestration, and queue
  summary helpers.
- `relayna.observability`
  Owns typed runtime observation events plus collector and exporter helpers,
  execution graph contracts, Mermaid export, and Redis-backed observation
  persistence for task-linked graph reconstruction.
- `relayna.workflow`
  Owns workflow control-plane concepts such as policies, transitions, fan-in,
  lineage, replay, and diagnostics.
- `relayna.mcp`
  Owns MCP-facing resources, adapters, and operator tools built on top of the
  runtime packages.

`relayna.storage` exists as an internal package for Redis models, repository
helpers, and retention behavior. It is not part of the documented public API.

Studio deployment is now packaged separately as `relayna-studio`. The SDK keeps
the runtime and contract packages; the deployable Studio backend and frontend do
not ship under the root `relayna` distribution. The current Studio backend
package version is `0.1.2`, and it requires `relayna>=1.4.5`.

If you are migrating an existing v1 codebase, use the dedicated guide:
[docs/migration-v1-to-v2.md](docs/migration-v1-to-v2.md).

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
        "priority": 8,
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
            "priority": 5,
            "payload": {"kind": "demo"},
        },
        {
            "attempt_id": "attempt-124",
            "request_id": "req-124",
            "source_service": "billing-api",
            "job_type": "invoice.render",
            "priority": 5,
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

If you use top-level task `priority`, every task in a `mode="batch_envelope"`
publish must use the same priority value or omit it entirely. Relayna rejects
mixed-priority batch envelopes because RabbitMQ exposes only one AMQP priority
per published message.

RabbitMQ scheduling only uses that priority when the destination queue was
declared with `x-max-priority`, which Relayna exposes through topology fields
such as `task_max_priority` and `workflow_max_priority`.

Relayna validates queue max-priority settings client-side and requires them to
be in the range `1..255`. It also rejects task or workflow publishes whose
top-level `priority` exceeds the configured `task_max_priority` or
`workflow_max_priority`.

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
from relayna.api import create_dlq_router, create_relayna_lifespan, get_relayna_runtime

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

Priority queue max values must be between `1` and `255`.

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

The v2 public package roots are:

- `relayna.topology`
- `relayna.contracts`
- `relayna.workflow`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status`
- `relayna.observability`
- `relayna.api`
- `relayna.mcp`
- `relayna.dlq`

The package root is intentionally minimal and only exports `relayna.__version__`.
Import concrete functionality from the package roots above rather than from
`relayna` itself.

Studio deployment and presenter helpers now live in the separate
`relayna-studio` package.

## Docs and releases

- Documentation: [sarattha.github.io/relayna](https://sarattha.github.io/relayna/)
- Getting started: [docs/getting-started.md](docs/getting-started.md)
- Studio backend: [docs/studio-backend.md](docs/studio-backend.md)
- Studio frontend: [docs/studio-frontend.md](docs/studio-frontend.md)
- Observability guide: [docs/observability.md](docs/observability.md)
- Execution graph guide: [docs/execution-graphs.md](docs/execution-graphs.md)
- GitHub Releases: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

`relayna` is released under the MIT license. See [LICENSE](LICENSE).
