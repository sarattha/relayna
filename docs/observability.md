# Observability

`relayna` exposes backend-agnostic runtime observations through async sink
callbacks and low-cardinality Prometheus runtime metrics. The library emits
typed dataclass events from long-running loops such as SSE streaming, worker
consumption, and status fanout. It also ships a Prometheus metrics helper for
API and worker runtimes, but it does not ship a tracing exporter.

`relayna.observability` owns the event model plus collector and exporter
helpers. It does not own status storage, FastAPI routes, or worker runtime
execution; those live in `relayna.status`, `relayna.api`, and
`relayna.consumer`.

It now also owns the persisted observation store and execution-graph
reconstruction helpers that sit on top of those event streams.

For AKS deployments that use Relayna Studio, see
[AKS observability stack](aks-observability.md) for the full Redis, RabbitMQ,
Loki, Alloy, Prometheus, kube-state-metrics, registered-service, worker, and
Studio architecture.

## Prometheus runtime metrics

Relayna runtime metrics are aggregate service/runtime signals. They are safe for
Prometheus because their labels are low-cardinality:

- `service`
- `stage`
- `queue`
- `status`
- `worker_type`

The SDK exports:

- `relayna_tasks_started_total`
- `relayna_tasks_completed_total`
- `relayna_tasks_failed_total`
- `relayna_tasks_retried_total`
- `relayna_tasks_dlq_total`
- `relayna_task_duration_seconds`
- `relayna_task_attempts`
- `relayna_worker_active_tasks`
- `relayna_worker_heartbeat_timestamp`
- `relayna_queue_publish_total`
- `relayna_status_events_published_total`
- `relayna_observation_events_total`

Never add task identity as Prometheus labels. `task_id`, `correlation_id`,
`request_id`, `worker_id`, `pod`, `pod_name`, `container`, and `message_id` are
rejected by the Relayna metrics helper because they can create unbounded series.

FastAPI services can expose metrics on `/metrics`:

```python
from relayna.api import create_metrics_router, create_relayna_lifespan, get_relayna_runtime

app = FastAPI(lifespan=create_relayna_lifespan(topology=topology, redis_url=redis_url))
runtime = get_relayna_runtime(app)
app.include_router(create_metrics_router(runtime.metrics))
```

Worker-only processes can expose the same registry through a small helper HTTP
server:

```python
from relayna.api import start_metrics_http_server

start_metrics_http_server(runtime.metrics, port=8001)
```

Exact per-task CPU and RSS samples are stored as Relayna observations around
handler execution. Studio reads them from task detail/execution graph data; it
does not query Prometheus by `task_id`.

## How it works

Observability in `relayna` is built around two public concepts from
`relayna.observability`:

- `RelaynaObservation`
  A protocol implemented by all observation dataclasses.
- `ObservationSink`
  An async callback with the shape `async def sink(event) -> None`.

Every observation includes:

- `component`
  One of `"sse"`, `"consumer"`, or `"status_hub"`.
- `timestamp`
  A UTC `datetime` recorded when the event object was created.

Important behavior:

- sinks are async-only
- sink failures are suppressed by Relayna
- observations are best-effort and never block the main workflow intentionally
- Relayna does not include raw message bodies in observation events
- execution graphs become `full` only when task-linked observations were
  persisted; otherwise the graph service falls back to status and DLQ data and
  marks the graph `partial`

## Basic usage

```python
from relayna.consumer import TaskConsumer
from relayna.observability import RelaynaObservation


async def sink(event: RelaynaObservation) -> None:
    print(event)


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    consume_timeout_seconds=1.0,
    observation_sink=sink,
)
await consumer.run_forever()
```

The same sink pattern works with:

- `TaskConsumer`
- `AggregationConsumer`
- `AggregationWorkerRuntime`
- `SSEStatusStream`
- `StatusHub`

## Persisting observations for execution graphs

Plain observation sinks are enough for logging and metrics, but execution
graphs need durable observation history that can be joined later with status
history and DLQ records.

Use `RedisObservationStore` and `make_redis_observation_sink(...)` when you
want Relayna to reconstruct what happened for a task after the worker has
already finished:

```python
from redis.asyncio import Redis

from relayna.consumer import RetryPolicy, TaskConsumer
from relayna.observability import RedisObservationStore, make_redis_observation_sink

redis = Redis.from_url("redis://localhost:6379/0")
observation_store = RedisObservationStore(
    redis,
    prefix="relayna-observations",
    ttl_seconds=86400,
    history_maxlen=500,
)

consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    retry_policy=RetryPolicy(max_retries=3, delay_ms=30000),
    observation_sink=make_redis_observation_sink(observation_store),
)
await consumer.run_forever()
```

Important behavior:

- only observations with a non-empty `task_id` are persisted
- persistence is deduplicated per task and event payload
- history is bounded by `history_maxlen`
- store retention follows `ttl_seconds`
- FastAPI and workers must use the same Redis instance and observation-store
  prefix if you want `GET /executions/{task_id}/graph` to return `full`
  execution graphs

Example with multiple components:

```python
from relayna.consumer import TaskConsumer
from relayna.status import SSEStatusStream
from relayna.status import StatusHub


async def sink(event) -> None:
    print(event.component, event)


consumer = TaskConsumer(
    rabbitmq=client,
    handler=handle_task,
    consume_timeout_seconds=1.0,
    observation_sink=sink,
)
hub = StatusHub(rabbitmq=client, store=store, observation_sink=sink)
stream = SSEStatusStream(store=store, observation_sink=sink)
```

## Filtering by event type

Use normal Python `isinstance(...)` checks against the observation dataclasses.

```python
from relayna.observability import (
    ConsumerRetryScheduled,
    SSEStreamStarted,
    StatusHubStoredEvent,
    TaskMessageAcked,
)


async def sink(event) -> None:
    if isinstance(event, SSEStreamStarted):
        print("stream started", event.task_id, event.resume_requested)
    elif isinstance(event, TaskMessageAcked):
        print("task acked", event.task_id)
    elif isinstance(event, ConsumerRetryScheduled):
        print("retry scheduled", event.task_id, event.retry_attempt, event.max_retries)
    elif isinstance(event, StatusHubStoredEvent):
        print("stored event", event.task_id, event.status)
```

## Structured logging example

If you want JSON logs that Loki can aggregate and Studio can render as
structured payloads, `structlog` is a practical default.

```python
import structlog

from relayna.observability import make_logging_sink

logger = structlog.get_logger("relayna")


async def sink(event) -> None:
    await make_logging_sink(logger)(event)
```

Configure `structlog` with a JSON renderer in your service so each emitted log
line stays parseable upstream. Studio log panels will pretty-print parseable
JSON objects and arrays, and fall back to plain-text/ANSI-safe rendering for
all other lines.

## Event groups

### SSE events

`SSEStatusStream` can emit:

- `SSEStreamStarted`
- `SSEResumeRequested`
- `SSEHistoryReplayed`
- `SSEKeepaliveSent`
- `SSELiveEventSent`
- `SSEMalformedPubsubPayload`
- `SSEStreamEnded`

Use these when you want to understand:

- how often clients reconnect with `Last-Event-ID`
- how many history events are replayed
- whether streams are mostly idle keepalives or live updates
- when malformed Redis pubsub payloads are being ignored

### Consumer events

`TaskConsumer` emits:

- `TaskConsumerStarted`
- `TaskMessageReceived`
- `TaskMessageAcked`
- `TaskMessageRejected`
- `TaskHandlerFailed`
- `TaskLifecycleStatusPublished`
- `TaskConsumerLoopError`
- `ConsumerRetryScheduled`
- `ConsumerDeadLetterPublished`
- `ConsumerDLQRecordPersistFailed`

`AggregationConsumer` and `AggregationWorkerRuntime` also emit aggregation-
specific task-linked events:

- `AggregationMessageReceived`
- `AggregationMessageAcked`
- `AggregationHandlerFailed`
- `AggregationRetryScheduled`
- `AggregationDeadLetterPublished`

They still share the generic DLQ persistence failure event:

- `ConsumerDLQRecordPersistFailed`

Use these when you want to monitor:

- queue start-up and reconnect loops
- message validation failures
- handler exceptions
- lifecycle status automation
- retry and DLQ volume
- aggregation child-task lineage
- DLQ publish success paired with Redis index write failures

Workflow runtimes emit richer stage and message lineage events:

- `WorkflowStageStarted`
- `WorkflowMessageReceived`
- `WorkflowMessagePublished`
- `WorkflowStageAcked`
- `WorkflowStageFailed`

Those workflow-specific observations are what let the execution graph layer
produce explicit `workflow_message`, `stage_attempt`, `entered_stage`, and
`stage_transitioned_to` structures instead of flattening the workflow into one
generic task-attempt timeline.

### Status hub events

`StatusHub` emits:

- `StatusHubStarted`
- `StatusHubStoredEvent`
- `StatusHubMalformedMessage`
- `StatusHubStoreWriteFailed`
- `StatusHubLoopError`

Use these when you want visibility into the RabbitMQ-to-Redis bridge, especially
for malformed status payloads or Redis write failures.

## Practical patterns

Use one sink for multiple outcomes:

- logging
  Serialize each event and write it to your app logger.
- metrics
  Increment counters based on event type or `component`.
- tracing
  Attach event fields to spans or breadcrumb-style traces.
- debugging
  Print or buffer recent events while developing a worker or SSE endpoint.

Example metrics-style sink:

```python
from collections import Counter

counts = Counter()


async def sink(event) -> None:
    counts[type(event).__name__] += 1
    counts[f"component:{event.component}"] += 1
```

## Notes and limitations

- Observability is opt-in. Relayna does nothing unless you pass an
  `observation_sink=...`.
- The FastAPI lifespan helper does not accept a generic observation sink
  directly, but it now can provision `runtime.observation_store` and
  `runtime.execution_graph_service` when `observation_store_prefix=...` is
  configured.
- Observation dataclasses are intended for operational use, not as a stable
  wire protocol between services.
- For the full runtime graph model, route behavior, node kinds, and rendering
  options, see [Execution graphs](execution-graphs.md).
