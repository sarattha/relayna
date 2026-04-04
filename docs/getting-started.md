# Getting started

## Requirements

You need:

- Python `>=3.13`
- RabbitMQ
- Redis

## Install from GitHub Releases

Install the wheel:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.3.3/relayna-1.3.3-py3-none-any.whl
```

Or install the source distribution:

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.3.3/relayna-1.3.3.tar.gz
```

For local work in this repository:

```bash
uv sync --extra dev
```

## Topology overview

`relayna` now uses named topology classes as the primary RabbitMQ configuration
API.

- `SharedTasksSharedStatusTopology`: one shared task queue and one shared status
  queue/stream.
- `SharedTasksSharedStatusShardedAggregationTopology`: the same shared task and
  shared status plane, plus shard-bound aggregation worker queues on the status
  exchange.
- `RoutedTasksSharedStatusTopology`: task queues are bound by `task_type`, while
  status stays on one shared queue/stream.
- `RoutedTasksSharedStatusShardedAggregationTopology`: routed task queues plus
  shard-bound aggregation worker queues on the shared status exchange.
- `SharedStatusWorkflowTopology`: one workflow topic exchange, one durable inbox
  queue per consuming stage, and one shared status queue/stream.

## Topology naming guidance

Shared topology resources are usually namespaced by exchange, queue, and Redis
prefix values. For sharded topologies, remember that the default aggregation
queue names such as `aggregation.queue.0` and
`aggregation.queue.shards.1-3` are global durable queues. If multiple
deployments, smoke tests, or local stacks share one RabbitMQ vhost, give the
aggregation queues a deployment-specific prefix too.

## Queue argument configuration

Topology constructors include a small curated set of first-class RabbitMQ queue
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

Status queues keep their existing dedicated stream and expiry settings, but all
topologies also expose explicit mapping escape hatches for broker-specific queue
arguments:

- `task_queue_arguments_overrides`
- `task_queue_kwargs`
- `aggregation_queue_arguments_overrides`
- `aggregation_queue_kwargs`
- `status_queue_arguments_overrides`
- `status_queue_kwargs`

Native queue-argument field mapping:

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

These topology timeout fields configure RabbitMQ queue arguments. They are
separate from the runtime-side `consume_timeout_seconds` setting on
`TaskConsumer`, `AggregationConsumer`, and `AggregationWorkerRuntime`, which
controls how long the local consumer loop waits for the next message before it
re-enters the loop.

`status_stream_initial_offset` is also native, but it affects the consumer
argument `x-stream-offset` rather than queue declaration arguments.

Example: task queue native fields only

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

Example: task queue native fields plus broker-specific kwargs

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

Example: sharded aggregation queue native fields plus kwargs

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

Example: status queue stream settings plus kwargs

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

Relayna raises `ValueError` when the same RabbitMQ argument key is configured
through more than one source for the same queue family.

## Example: shared tasks + shared status

This is the default setup for task workers, shared status history, and FastAPI
status endpoints.

```python
from fastapi import FastAPI

from relayna.dlq import DLQService
from relayna.fastapi import create_dlq_router, create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.rabbitmq import RelaynaRabbitClient
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

client = RelaynaRabbitClient(topology=topology)
await client.initialize()
await client.publish_task({"task_id": "task-123", "payload": {"kind": "demo"}})

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

This exposes:

- `GET /events/{task_id}`
- `GET /history`
- `GET /status/{task_id}`

For a detailed observability walkthrough, see [Observability](observability.md).

### Contract aliases

Use `ContractAliasConfig` when external producers or API clients should use
different top-level envelope field names.

```python
from relayna.contracts import ContractAliasConfig

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

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        alias_config=alias_config,
    )
)
runtime = get_relayna_runtime(app)
app.include_router(
    create_status_router(
        sse_stream=runtime.sse_stream,
        history_reader=runtime.history_reader,
        latest_status_store=runtime.store,
        alias_config=alias_config,
    )
)
```

## Example: routed tasks + shared status

Use `RoutedTasksSharedStatusTopology` when different workers should consume
different `task_type` values from the same task exchange while still publishing
into one shared status stream.

Each routed worker gets its own topology instance:

```python
from relayna.topology import RoutedTasksSharedStatusTopology

generate_topology = RoutedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.generate.queue",
    task_types=("draft.generate",),
    status_exchange="status.exchange",
    status_queue="status.queue",
)

review_topology = RoutedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.review.queue",
    task_types=("draft.review",),
    status_exchange="status.exchange",
    status_queue="status.queue",
)
```

Important details:

- the routed workers share `tasks_exchange`, `status_exchange`, and
  `status_queue`
- each routed worker uses its own `tasks_queue`
- each routed worker declares one or more `task_types` that it owns
- `RelaynaRabbitClient.publish_task(...)` routes by `TaskEnvelope.task_type`
  under routed topologies, so `task_type` is required

That layout is what makes manual handoff retry possible: one worker can
republish the same `task_id` under a different `task_type`, and RabbitMQ will
deliver it to another routed worker queue.

Relayna keeps the transport canonical internally and only applies aliases at the
edges:

- inbound task and status payloads accept aliased top-level names such as
  `attempt_id`, `request_id`, `source_service`, and `job_type`
- workers still receive canonical envelope fields such as
  `TaskEnvelope.task_id`, `TaskEnvelope.correlation_id`, `TaskEnvelope.service`,
  and `TaskEnvelope.task_type`
- `/events/{attempt_id}`, `/status/{attempt_id}`, and `/history?attempt_id=...`
  use the alias when the same config is passed into FastAPI
- history, latest-status, and SSE bodies expose the aliased names instead of
  the canonical keys
- aliasing is top-level only; nested keys inside `payload`, `meta`, or `result`
  are not renamed

Example latest-status response with aliasing enabled:

```json
{
  "attempt_id": "attempt-123",
  "event": {
    "attempt_id": "attempt-123",
    "request_id": "req-123",
    "source_service": "billing-api",
    "job_type": "invoice.render",
    "status": "completed",
    "message": "Task completed.",
    "event_id": "8e53f4...",
    "meta": {}
  }
}
```

Example SSE event with aliasing enabled:

```text
id: 8e53f4...
event: status
data: {"attempt_id":"attempt-123","request_id":"req-123","source_service":"billing-api","job_type":"invoice.render","status":"completed","message":"Task completed.","event_id":"8e53f4...","meta":{}}
```

Example worker view of the same task after Relayna normalizes aliases:

```python
async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    assert task.task_id == "attempt-123"
    assert task.correlation_id == "req-123"
    assert task.service == "billing-api"
    assert task.task_type == "invoice.render"
```

If your HTTP route parameter should differ from the payload alias, add
`http_aliases`:

```python
alias_config = ContractAliasConfig(
    field_aliases={
        "task_id": "attempt_id",
        "correlation_id": "request_id",
    },
    http_aliases={"task_id": "attemptId"},
)
```

With that config, request bodies still use `attempt_id`, while FastAPI routes
and query parameters use `attemptId`.

Example split between HTTP naming and JSON body naming:

- request path: `GET /status/attempt-123` using the route template
  `GET /status/{attemptId}`
- request query: `GET /history?attemptId=attempt-123`
- response body:

```json
{
  "attempt_id": "attempt-123",
  "event": {
    "attempt_id": "attempt-123",
    "status": "completed"
  }
}
```

That split is intentional: `http_aliases` controls path/query parameter names,
while `field_aliases` controls JSON payload and response field names.

### Batch publishing

Use `publish_tasks(...)` when you want one client call to submit several tasks.
Relayna supports two modes.

#### Individual mode

`mode="individual"` publishes one RabbitMQ message per task.

```python
await client.publish_tasks(
    [
        {
            "attempt_id": "task-1",
            "request_id": "req-1",
            "source_service": "bulk-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
        {
            "attempt_id": "task-2",
            "request_id": "req-2",
            "source_service": "bulk-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
    ],
    mode="individual",
)
```

#### Batch-envelope mode

`mode="batch_envelope"` publishes one RabbitMQ message containing several task
items plus a `batch_id`.

```python
await client.publish_tasks(
    [
        {
            "attempt_id": "task-1",
            "request_id": "req-1",
            "source_service": "bulk-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
        {
            "attempt_id": "task-2",
            "request_id": "req-2",
            "source_service": "bulk-api",
            "job_type": "invoice.render",
            "payload": {"kind": "demo"},
        },
    ],
    mode="batch_envelope",
    batch_id="batch-123",
    meta={"source": "bulk-api"},
)
```

Example batch-envelope transport body published by Relayna:

```json
{
  "batch_id": "batch-123",
  "tasks": [
    {
      "task_id": "task-1",
      "correlation_id": "req-1",
      "service": "bulk-api",
      "task_type": "invoice.render",
      "payload": {
        "kind": "demo"
      }
    },
    {
      "task_id": "task-2",
      "correlation_id": "req-2",
      "service": "bulk-api",
      "task_type": "invoice.render",
      "payload": {
        "kind": "demo"
      }
    }
  ],
  "meta": {
    "source": "bulk-api"
  }
}
```

Even if producers publish aliased fields such as `attempt_id`,
`request_id`, `source_service`, or `job_type`, Relayna normalizes the envelope
to canonical top-level fields before it reaches the worker.

When a `TaskConsumer` with `retry_policy=...` receives that batch envelope, it
does not run all items inline under the original delivery. Relayna first fans
the envelope back out into one task-queue message per item, preserving:

- `task_id`
- `batch_id`
- `batch_index`
- `batch_size`

That means later failures do not cause RabbitMQ to redeliver earlier successful
items from the same original batch envelope.

Example per-item message shape after fan-out:

```json
{
  "task_id": "task-1",
  "correlation_id": "req-1",
  "service": "bulk-api",
  "task_type": "invoice.render",
  "payload": {
    "kind": "demo"
  },
  "spec_version": "1.0"
}
```

Example headers on that per-item message:

```json
{
  "task_id": "task-1",
  "batch_id": "batch-123",
  "batch_index": 0,
  "batch_size": 2
}
```

### Task worker example

```python
from relayna.consumer import RetryPolicy, TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(
        status="processing",
        message=f"Batch {context.batch_id} item {context.batch_index + 1}/{context.batch_size} started."
        if context.batch_id is not None
        else "Task processing started.",
    )
    await context.publish_status(
        status="completed",
        message="Task processing completed.",
        result={
            "batch_id": context.batch_id,
            "batch_index": context.batch_index,
            "batch_size": context.batch_size,
        },
    )


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
    consume_timeout_seconds=1.0,
    alias_config=alias_config,
)
await consumer.run_forever()
```

Set `consume_timeout_seconds=None` when you want the worker runtime to block
indefinitely waiting for the next message instead of waking up every second.
That reduces idle wake-ups, but `stop()` becomes best-effort for standalone
consumers and may not complete until a message arrives or the task is canceled.

Inside the handler:

- `task.task_id` is always canonical, even when the producer sent `attempt_id`
- `context.batch_id` is set only for batch-envelope deliveries
- `context.batch_index` is zero-based within the batch
- `context.batch_size` is the total number of items in that envelope

Batch-envelope consumption requires `retry_policy`. When one item fails, Relayna
retries only that failed per-item message instead of retrying the original
batch envelope.

Example status response for the first item in a batch:

```json
{
  "attempt_id": "attempt-123",
  "event": {
    "attempt_id": "attempt-123",
    "status": "completed",
    "message": "Task processing completed.",
    "result": {
      "batch_id": "batch-123",
      "batch_index": 0,
      "batch_size": 2
    }
  }
}
```

When `retry_policy` is enabled more generally, Relayna creates a broker-delayed
retry queue and a per-source DLQ. Malformed JSON and invalid envelopes go
straight to the DLQ; handler failures retry until `max_retries` is exhausted,
then dead-letter.

### Retry and DLQ headers

Relayna keeps retry metadata in RabbitMQ message headers. The task or
aggregation payload body is not rewritten.

Headers added by Relayna:

- `headers["x-relayna-retry-attempt"]`
  The current retry attempt number on the republished message.
  `0` means the original delivery. `1` means the first delayed retry.
- `headers["x-relayna-max-retries"]`
  The configured retry limit for this consumer. Relayna compares the current
  attempt against this value to decide whether to retry again or dead-letter.
- `headers["x-relayna-source-queue"]`
  The original worker queue that owns the message. Relayna uses this to make it
  clear which queue the retry or DLQ message came from.
- `headers["x-relayna-failure-reason"]`
  A short machine-readable failure category such as `handler_error`,
  `malformed_json`, or `invalid_envelope`.
- `headers["x-relayna-exception-type"]`
  The Python exception type name when one exists, such as `RuntimeError` or
  `ValidationError`. Relayna sets this to `null`/`None` style absence when the
  failure was not caused by a raised exception, such as malformed JSON decode.

Example DLQ message metadata after a handler fails on the last allowed retry:

```python
headers = {
    "x-relayna-retry-attempt": 3,
    "x-relayna-max-retries": 3,
    "x-relayna-source-queue": "tasks.queue",
    "x-relayna-failure-reason": "handler_error",
    "x-relayna-exception-type": "RuntimeError",
}
```

Example task body in that same DLQ message:

```json
{
  "task_id": "task-123",
  "payload": {
    "kind": "demo"
  }
}
```

That example means:

- the task has already been retried three times
- the consumer limit was three retries
- the message originated from `tasks.queue`
- the final failure came from application handler code
- the thrown exception type was `RuntimeError`

### DLQ monitoring router

If you want a dashboard or internal tool to investigate dead-lettered messages,
enable the optional Redis-backed DLQ index in FastAPI and pass the same store to
workers.

```python
from redis.asyncio import Redis

from relayna.consumer import RetryPolicy, TaskConsumer
from relayna.dlq import DLQService, RedisDLQStore
from relayna.fastapi import create_dlq_router, create_relayna_lifespan, get_relayna_runtime

dlq_store_prefix = "relayna-dlq"
worker_dlq_store = RedisDLQStore(Redis.from_url("redis://localhost:6379/0"), prefix=dlq_store_prefix)

consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
    consume_timeout_seconds=None,
    dlq_store=worker_dlq_store,
)

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        dlq_store_prefix=dlq_store_prefix,
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

This exposes:

- `GET /dlq/queues`
- `GET /dlq/messages`
- `GET /dlq/messages/{dlq_id}`
- `POST /dlq/messages/{dlq_id}/replay`
- `GET /broker/dlq/queues` when `broker_dlq_queue_names=...` is configured

Important limitation:

- Relayna does not read live DLQ payloads directly from RabbitMQ because
  classic queues do not support a read-only payload peek over AMQP
- the DLQ router uses Redis for message detail and RabbitMQ only for live queue
  counts and replay transport

`GET /dlq/queues` is intentionally index-backed. It means “queues known from
indexed DLQ records plus live count lookup,” not “list all RabbitMQ DLQ
queues.”

If you want broader broker visibility, configure `broker_dlq_queue_names=...`
and use `GET /broker/dlq/queues`. That endpoint inspects the configured
candidate queue names together with queue names already present in the DLQ
index. Broker-only queues appear with `indexed_count=0` and
`last_indexed_at=null`.

## Example: multi-stage workflow + shared status

Use `SharedStatusWorkflowTopology` when work should move through multiple named
stages such as planner, re-planner, search, writer, and file-creation steps.

This topology is stage-inbox based:

- one workflow topic exchange carries stage-to-stage work
- each consuming stage owns one durable inbox queue
- producers publish to a stage routing key or named entry route
- status events still publish on the shared status exchange so `StatusHub`,
  `StreamHistoryReader`, and SSE stay unchanged

This is the recommended first-class model because the durable queue belongs to
the stage that consumes it. Retry, DLQ, lag, and autoscaling therefore stay
attached to the worker that owns the work rather than to an upstream producer.

### Routing convention

The examples below use these conventions:

- planner-stage routing keys: `planner.<stage>.in`
- replanner entry routes: `replanner.<stage>.in`
- writer-stage routing keys: `writer.<stage>.in`
- one stage may bind multiple routing keys when both planner and replanner
  traffic should land in the same inbox queue

### Planner Flow

```mermaid
flowchart LR
    API["API"] -->|publish planner.topic_planner.in| EX["workflow exchange"]
    EX --> TPQ["topic_planner inbox queue"]
    TPQ --> TP["Topic Planner Agent"]

    TP -->|publish planner.docsearch_planner.in| EX
    EX --> DSPQ["docsearch_planner inbox queue"]
    DSPQ --> DSP["Docsearch Planner Agent"]

    DSP -->|publish planner.planner_assembler.in| EX
    EX --> PAQ["planner_assembler inbox queue"]
    PAQ --> PA["Planner Assembler"]

    PA -->|publish planner.document_date_finder.in| EX
    EX --> DDFQ["document_date_finder inbox queue"]
    DDFQ --> DDF["Document Date Finder Agent"]

    TP -. publish status .-> SX["status exchange"]
    DSP -. publish status .-> SX
    PA -. publish status .-> SX
    DDF -. publish status .-> SX
```

### Re-planner Flow

The re-planner path reuses the same consuming planner stages. The only thing
that changes is the entry route and, where needed, extra binding keys on the
shared planner inbox queue.

```mermaid
flowchart LR
    API["API"] -->|publish replanner.docsearch_planner.in| EX["workflow exchange"]
    API -->|publish replanner.planner_assembler.in| EX

    EX --> DSPQ["docsearch_planner inbox queue"]
    DSPQ --> DSP["Docsearch Planner Agent"]

    EX --> PAQ["planner_assembler inbox queue"]
    PAQ --> PA["Planner Assembler"]

    DSP -->|publish planner.planner_assembler.in| EX

    DSP -. publish status .-> SX["status exchange"]
    PA -. publish status .-> SX
```

### Writer Flow

```mermaid
flowchart LR
    API["API"] -->|publish writer.docsearcher.in| EX["workflow exchange"]
    API -->|publish writer.websearcher.in| EX

    EX --> DSQ["docsearcher inbox queue"]
    DSQ --> DS["DocSearcher Agent"]

    EX --> WSQ["websearcher inbox queue"]
    WSQ --> WS["WebSearcher Agent"]

    DS -->|publish writer.researcher_aggregator.in| EX
    WS -->|publish writer.researcher_aggregator.in| EX

    EX --> RAQ["researcher_aggregator inbox queue"]
    RAQ --> RA["Researcher Aggregator"]

    RA -->|publish writer.writer.in| EX
    EX --> WQ["writer inbox queue"]
    WQ --> W["Writer Agent"]

    W -->|publish writer.writer_aggregator.in| EX
    EX --> WAQ["writer_aggregator inbox queue"]
    WAQ --> WA["Writer Aggregator"]

    WA -->|publish writer.file_creator.in| EX
    EX --> FCQ["file_creator inbox queue"]
    FCQ --> FC["File Creator Agent"]

    FC -->|publish writer.writer_assembler.in| EX
    EX --> WASQ["writer_assembler inbox queue"]
    WASQ --> WAS["Writer Assembler"]
```

### Constructing the topology

```python
from relayna.topology import SharedStatusWorkflowTopology, WorkflowEntryRoute, WorkflowStage

topology = SharedStatusWorkflowTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    workflow_exchange="ca.workflow.exchange",
    status_exchange="ca.status.exchange",
    status_queue="ca.status.queue",
    workflow_consumer_timeout_ms=120000,
    workflow_queue_type="quorum",
    stages=(
        WorkflowStage(
            name="topic_planner",
            queue="cq.topic_planner.in_queue",
            binding_keys=("planner.topic_planner.in",),
            publish_routing_key="planner.topic_planner.in",
        ),
        WorkflowStage(
            name="docsearch_planner",
            queue="cq.docsearch_planner.in_queue",
            binding_keys=(
                "planner.docsearch_planner.in",
                "replanner.docsearch_planner.in",
            ),
            publish_routing_key="planner.docsearch_planner.in",
        ),
        WorkflowStage(
            name="planner_assembler",
            queue="cq.planner_assembler.in_queue",
            binding_keys=(
                "planner.planner_assembler.in",
                "replanner.planner_assembler.in",
            ),
            publish_routing_key="planner.planner_assembler.in",
        ),
        WorkflowStage(
            name="document_date_finder",
            queue="cq.document_date_finder.in_queue",
            binding_keys=("planner.document_date_finder.in",),
            publish_routing_key="planner.document_date_finder.in",
        ),
        WorkflowStage(
            name="docsearcher",
            queue="cq.docsearcher.in_queue",
            binding_keys=("writer.docsearcher.in",),
            publish_routing_key="writer.docsearcher.in",
        ),
        WorkflowStage(
            name="websearcher",
            queue="cq.websearcher.in_queue",
            binding_keys=("writer.websearcher.in",),
            publish_routing_key="writer.websearcher.in",
        ),
        WorkflowStage(
            name="researcher_aggregator",
            queue="cq.researcher_aggregator.in_queue",
            binding_keys=("writer.researcher_aggregator.in",),
            publish_routing_key="writer.researcher_aggregator.in",
        ),
        WorkflowStage(
            name="writer",
            queue="cq.writer.in_queue",
            binding_keys=("writer.writer.in",),
            publish_routing_key="writer.writer.in",
        ),
        WorkflowStage(
            name="writer_aggregator",
            queue="cq.writer_aggregator.in_queue",
            binding_keys=("writer.writer_aggregator.in",),
            publish_routing_key="writer.writer_aggregator.in",
        ),
        WorkflowStage(
            name="file_creator",
            queue="cq.file_creator.in_queue",
            binding_keys=("writer.file_creator.in",),
            publish_routing_key="writer.file_creator.in",
        ),
        WorkflowStage(
            name="writer_assembler",
            queue="cq.writer_assembler.in_queue",
            binding_keys=("writer.writer_assembler.in",),
            publish_routing_key="writer.writer_assembler.in",
        ),
    ),
    entry_routes=(
        WorkflowEntryRoute(
            name="planner_entry",
            routing_key="planner.topic_planner.in",
            target_stage="topic_planner",
        ),
        WorkflowEntryRoute(
            name="replanner_docsearch_entry",
            routing_key="replanner.docsearch_planner.in",
            target_stage="docsearch_planner",
        ),
        WorkflowEntryRoute(
            name="replanner_assembler_entry",
            routing_key="replanner.planner_assembler.in",
            target_stage="planner_assembler",
        ),
        WorkflowEntryRoute(
            name="writer_docsearch_entry",
            routing_key="writer.docsearcher.in",
            target_stage="docsearcher",
        ),
        WorkflowEntryRoute(
            name="writer_websearch_entry",
            routing_key="writer.websearcher.in",
            target_stage="websearcher",
        ),
    ),
)
```

### Publishing to a normal entry route

Use `publish_to_entry(...)` when a producer wants the topology to own the
workflow routing decision for a named ingress route.

```python
from relayna.contracts import WorkflowEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

client = RelaynaRabbitClient(topology=topology, connection_name="planner-api")
await client.initialize()

await client.publish_to_entry(
    WorkflowEnvelope(
        task_id="task-001",
        stage="topic_planner",
        action="build-plan",
        priority=7,
        payload={"query": "new compliance workflow"},
        meta={"source": "api"},
    ),
    route="planner_entry",
)
```

### Publishing to a re-planner entry route

Use a different named entry route when re-planner traffic should enter further
down the same planner pipeline.

```python
await client.publish_to_entry(
    WorkflowEnvelope(
        task_id="task-001",
        stage="docsearch_planner",
        action="replan-docsearch",
        priority=6,
        payload={"new_documents": ["doc-100", "doc-200"]},
        meta={"reason": "documents_changed"},
    ),
    route="replanner_docsearch_entry",
)

await client.publish_to_entry(
    WorkflowEnvelope(
        task_id="task-002",
        stage="planner_assembler",
        action="replan-assemble",
        priority=4,
        payload={"existing_documents": ["doc-001", "doc-002"]},
        meta={"reason": "old_documents_only"},
    ),
    route="replanner_assembler_entry",
)
```

### Planner-stage worker with `WorkflowConsumer`

```python
from relayna.consumer import RetryPolicy, RetryStatusConfig, WorkflowConsumer, WorkflowContext
from relayna.contracts import WorkflowEnvelope


async def handle_docsearch_planner(message: WorkflowEnvelope, context: WorkflowContext) -> None:
    await context.publish_status(status="processing", message="Docsearch planner started.")

    docs = [
        {"doc_id": "doc-123", "title": "Policy Handbook"},
        {"doc_id": "doc-456", "title": "Retention Matrix"},
    ]

    await context.publish_to_stage(
        "planner_assembler",
        payload={"documents": docs, "source_action": message.action},
        action="assemble-plan",
        meta={"planner_stage": context.stage},
        priority=5,
    )


consumer = WorkflowConsumer(
    rabbitmq=client,
    stage="docsearch_planner",
    handler=handle_docsearch_planner,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=15000),
    retry_statuses=RetryStatusConfig(enabled=True),
    consume_timeout_seconds=1.0,
)
await consumer.run_forever()
```

### Publishing directly to a stage

Use `publish_to_stage(...)` when the caller already knows the destination stage
and does not need a named entry-route abstraction.

```python
await client.publish_to_stage(
    WorkflowEnvelope(
        task_id="task-010",
        stage="writer",
        action="draft",
        priority=8,
        payload={"outline": ["intro", "body", "summary"]},
        meta={"source_stage": "researcher_aggregator"},
    ),
    stage="writer",
)
```

### Writer fan-in example

Multiple upstream stages can target the same inbox queue. That is the normal
way to model fan-in under the stage-inbox topology.

```python
from relayna.consumer import WorkflowContext
from relayna.contracts import WorkflowEnvelope


async def handle_docsearcher(message: WorkflowEnvelope, context: WorkflowContext) -> None:
    await context.publish_status(status="searching", message="Docsearch results ready.")
    await context.publish_to_stage(
        "researcher_aggregator",
        payload={"source": "docsearcher", "documents": [{"doc_id": "doc-100"}]},
        action="aggregate-research",
    )


async def handle_websearcher(message: WorkflowEnvelope, context: WorkflowContext) -> None:
    await context.publish_status(status="searching", message="Websearch results ready.")
    await context.publish_to_stage(
        "researcher_aggregator",
        payload={"source": "websearcher", "documents": [{"doc_id": "web-200"}]},
        action="aggregate-research",
    )
```

In this model:

- both upstream workers publish to `writer.researcher_aggregator.in`
- `researcher_aggregator` owns the only durable inbox queue for that stage
- retries and backlog are measured on that consumer-owned inbox queue

## Example: shared tasks + shared status + sharded aggregation

Use this topology when normal task workers stay on one shared task queue, but
aggregation work should be distributed across shard-owned queues such as
`aggregation.queue.0`, `aggregation.queue.1`, or subset queues like
`aggregation.queue.shards.1-3`.

Aggregation messages are still published on the shared status exchange, so
`StatusHub`, `StreamHistoryReader`, and SSE clients can see them like normal
status events.

```python
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.topology import SharedTasksSharedStatusShardedAggregationTopology

topology = SharedTasksSharedStatusShardedAggregationTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
    shard_count=4,
    aggregation_queue_template="aggregation.queue.dev.{shard}",
    aggregation_queue_name_prefix="aggregation.queue.dev.shards",
)

client = RelaynaRabbitClient(topology=topology)
await client.initialize()
```

### Task worker publishes aggregation work

```python
from relayna.consumer import RetryPolicy, RetryStatusConfig, TaskConsumer, TaskContext
from relayna.contracts import TaskEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(status="processing", message="Sub-task started.")

    await context.publish_aggregation_status(
        status="aggregating",
        message="Partial result ready for shard aggregation.",
        meta={"parent_task_id": "root-task-123"},
        result={"chunk_id": task.task_id, "value": 42},
    )


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=15000),
    retry_statuses=RetryStatusConfig(enabled=True),
    consume_timeout_seconds=1.0,
)
await consumer.run_forever()
```

`meta.parent_task_id` is required for aggregation publishing. `relayna`
computes the shard from that parent id, writes `meta.aggregation_shard`, sets
`meta.aggregation_role="aggregation"`, and publishes the event to a routing key
like `agg.0`.

### Choosing `publish_status(...)` vs `publish_aggregation_status(...)`

This is the most important rule:

- `TaskContext.publish_status(...)` publishes a normal shared status event for
  the current context task
- `TaskContext.publish_aggregation_status(...)` publishes an aggregation input
  event for the current context task, routed by `meta.parent_task_id`
- `TaskContext.manual_retry(...)` republishes the current task under the same
  `task_id` for a different routed worker, usually by changing `task_type`

In both helpers, the canonical `task_id` stays the current context task id.
`meta.parent_task_id` does not replace `task_id`; it only describes the parent
relationship and shard-routing key.

That means:

- use `publish_status(...)` when you want to say something about the current
  child task itself
- use `publish_aggregation_status(...)` when the current child task is emitting
  input for parent-level aggregation workers
- if you need to publish a status whose real `task_id` is the parent task, do
  not use the context helper; publish an explicit `StatusEventEnvelope` with the
  parent task id through `rabbitmq.publish_status(...)`
- use `manual_retry(...)` when the current worker decides the task should be
  handed off to another routed task worker under the same `task_id`

`manual_retry(...)` is separate from broker retry/DLQ behavior. It republishes a
new task message, emits a `manual_retrying` status for the current `task_id`,
and preserves manual-retry lineage metadata for the next worker.

When top-level task `priority` is present, `manual_retry(...)` preserves it by
default. Pass `priority=...` to override the republished task priority.

#### Manual handoff retry example

Use `manual_retry(...)` when the current worker decides the task should be
reprocessed by a different routed worker with different settings.

Source worker:

```python
from relayna.consumer import TaskContext
from relayna.contracts import TaskEnvelope


async def generate(task: TaskEnvelope, context: TaskContext) -> None:
    score = float(task.payload.get("quality_score", 0))
    if score < 0.9:
        await context.manual_retry(
            task_type="draft.review",
            priority=7,
            payload_merge={
                "mode": "strict",
                "review_required": True,
            },
            reason="quality gate failed",
            meta={"quality_score": score},
        )
        return

    await context.publish_status(status="completed", message="Draft accepted.")
```

If you publish tasks with `mode="batch_envelope"` and use top-level
`priority`, every enclosed task must share the same priority value. Relayna
rejects mixed-priority batch envelopes because RabbitMQ only accepts one AMQP
message priority per published delivery.

RabbitMQ only schedules by priority when the destination queue was declared
with `x-max-priority`, using Relayna topology fields such as
`task_max_priority` or `workflow_max_priority`.

Relayna also fails client-side when a task or workflow publish requests a
top-level `priority` above the configured queue max priority for that topology.

Target worker:

```python
from relayna.consumer import TaskContext
from relayna.contracts import TaskEnvelope


async def review(task: TaskEnvelope, context: TaskContext) -> None:
    assert task.task_type == "draft.review"
    assert context.manual_retry_count == 1
    assert context.manual_retry_previous_task_type == "draft.generate"

    await context.publish_status(
        status="processing",
        message="Review worker accepted manual retry handoff.",
        meta={
            "review_mode": task.payload["mode"],
            "handoff_reason": context.manual_retry_reason,
        },
    )

    await context.publish_status(status="completed", message="Review completed.")
```

Result:

- the republished task keeps the same `task_id`
- the republished task can use a different `task_type`, payload, or `service`
- Relayna emits `manual_retrying` for the original worker instead of `completed`
- the next worker sees lineage on `TaskContext.manual_retry_count`,
  `manual_retry_previous_task_type`, `manual_retry_source_consumer`, and
  `manual_retry_reason`
- status history, latest-status, and SSE remain on one timeline because all
  events still use the same `task_id`

Operational rule:

- the source and target workers must use routed topologies that share the same
  exchanges/status plane but bind different task queues to different
  `task_type` values
- if no queue is bound for the target `task_type`, RabbitMQ will not have a
  routed worker queue to deliver that handoff message to

Header behavior:

- broker retry headers such as `x-relayna-retry-attempt` are not reused
- manual handoff lineage is carried in separate `x-relayna-manual-retry-*`
  headers and reflected in status metadata under `meta.manual_retry`
- normal broker retry still applies independently if the target worker later
  fails and has `retry_policy=...`

#### Child task status example

Use `publish_status(...)` for ordinary child lifecycle updates:

```python
async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_status(
        status="processing",
        message="Child task started.",
    )

    await context.publish_status(
        status="completed",
        message="Child task finished successfully.",
    )
```

Result:

- `task_id` is the child task id from `context`
- the event goes to the shared status stream
- SSE, latest-status, and history readers will treat it as normal status for
  that child task

#### Child emits aggregation input example

Use `publish_aggregation_status(...)` when a child has partial output that a
parent-level aggregation worker should consume:

```python
async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.publish_aggregation_status(
        status="aggregating",
        message="Partial result ready for parent aggregation.",
        meta={"parent_task_id": "root-task-123"},
        result={"chunk_id": task.task_id, "value": 42},
    )
```

Result:

- `task_id` is still the child task id from `context`
- `meta.parent_task_id` tells Relayna which parent/root task this child belongs to
- the event is routed to the aggregation shard queue for that parent
- aggregation workers can process it, while `StatusHub`, SSE, and history can
  still observe it from the shared status exchange

#### Parent task status example

If you want a status event whose canonical identity is the parent task itself,
publish it explicitly instead of using the child context helper:

```python
from relayna.contracts import StatusEventEnvelope


async def handle_task(task: TaskEnvelope, context: TaskContext) -> None:
    await context.rabbitmq.publish_status(
        StatusEventEnvelope(
            task_id="root-task-123",
            status="aggregation-complete",
            message="Parent task finished aggregation.",
            meta={"source_child_task_id": task.task_id},
            correlation_id="root-task-123",
        )
    )
```

Result:

- `task_id` is the parent task id, not the child task id
- clients querying `/events/root-task-123` or `/status/root-task-123` will see
  this as a parent-task status
- this does not route back into aggregation queues

#### Practical decision guide

Use `context.publish_status(...)` when:

- the status belongs to the current task in the handler context
- you want normal status history, SSE, and latest-status behavior
- you are inside an aggregation worker and want to publish bookkeeping or final
  statuses without feeding the aggregation queue again

Use `context.publish_aggregation_status(...)` when:

- the current task is emitting work or partial output for parent aggregation
- you need shard routing based on `meta.parent_task_id`
- the event is intended to be consumed by `AggregationConsumer`

Use `context.rabbitmq.publish_status(StatusEventEnvelope(...))` when:

- the event should belong to a different task id than the current context task
- you are publishing a real parent/root status from child or aggregation logic

Use `context.manual_retry(...)` when:

- the current task should stay on the same logical `task_id`
- the next processing attempt should go to a different routed `task_type`
- you want shared history/SSE/latest-status continuity across the handoff
- you need the next worker to see standardized handoff lineage

### Aggregation worker runtime example

```python
import asyncio

from relayna.consumer import AggregationWorkerRuntime, RetryPolicy, RetryStatusConfig, TaskContext
from relayna.contracts import StatusEventEnvelope


async def aggregate(event: StatusEventEnvelope, context: TaskContext) -> None:
    shard = event.meta.get("aggregation_shard")
    await context.publish_status(
        status="aggregation-processed",
        message=f"Aggregation worker handled shard {shard}.",
        meta={
            "parent_task_id": event.meta["parent_task_id"],
            "aggregation_shard": shard,
        },
    )


runtime = AggregationWorkerRuntime(
    topology=topology,
    handler=aggregate,
    shard_groups=[[0], [1, 2, 3]],
    retry_policy=RetryPolicy(max_retries=3, delay_ms=15000),
    retry_statuses=RetryStatusConfig(enabled=True),
    consume_timeout_seconds=None,
)

await runtime.start()
try:
    await asyncio.Event().wait()
finally:
    await runtime.stop()
```

`shard_groups=[[0], [1, 2, 3]]` means:

- one aggregation consumer owns shard `0`
- a second aggregation consumer owns shards `1`, `2`, and `3`

`consume_timeout_seconds=None` makes each aggregation consumer block waiting for
the next event. `AggregationWorkerRuntime.stop()` still falls back to task
cancellation if a blocking consumer does not exit within the runtime stop
timeout.

### Direct aggregation-queue probe example

Use this when you want to validate shard routing directly against RabbitMQ in a
real environment.

```python
import json

from relayna.contracts import StatusEventEnvelope
from relayna.rabbitmq import RelaynaRabbitClient

probe = RelaynaRabbitClient(topology=topology, connection_name="aggregation-probe")
await probe.initialize()

queue_name = await probe.ensure_aggregation_queue(shards=[0, 1, 2, 3])
await probe.publish_aggregation_status(
    StatusEventEnvelope(
        task_id="agg-task-123",
        status="aggregating",
        message="Aggregation input ready.",
        meta={"parent_task_id": "parent-123"},
    )
)

channel = await probe.acquire_channel(prefetch=1)
queue = await channel.declare_queue(
    queue_name,
    durable=True,
    arguments=topology.aggregation_queue_arguments() or None,
)
message = await queue.get(timeout=5.0, fail=True)
payload = json.loads(message.body.decode("utf-8"))
await message.ack()
```

`payload["meta"]["aggregation_shard"]` tells you which shard routing key was
used, and the fact that the queue received the message proves that the
aggregation queue binding is live.

### Custom SSE terminal statuses

If your aggregation workflow finishes on a status other than `completed` or
`failed`, pass a `TerminalStatusSet` to FastAPI so `/events/{task_id}` streams
terminate correctly.

```python
from relayna.contracts import TerminalStatusSet
from relayna.fastapi import create_relayna_lifespan

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        sse_terminal_statuses=TerminalStatusSet({"aggregation-processed"}),
    )
)
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

## End-to-end composition

For the default shared topology:

1. Publish tasks with `RelaynaRabbitClient`.
2. Consume tasks with `TaskConsumer`.
3. Publish status updates from handlers with `TaskContext.publish_status(...)`.
4. Run `StatusHub` to bridge shared status messages into `RedisStatusStore`.
5. Expose `create_status_router(...)` in your FastAPI service for history and SSE.

For the sharded aggregation topology:

1. Publish tasks with the same shared task plane.
2. Task workers publish aggregation work with `publish_aggregation_status(...)`.
3. Aggregation workers consume shard-owned queues through `AggregationWorkerRuntime`.
4. `StatusHub`, `StreamHistoryReader`, and SSE still observe aggregation events from the shared status exchange.

## Real-environment smoke commands

If RabbitMQ is running at `localhost:5672` and Redis at `localhost:6379`, these
scripts exercise both topology families against the real stack:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/real_fastapi_status_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_task_worker_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_workflow_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_sharded_aggregation_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_queue_args_smoke.py
```
