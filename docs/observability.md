# Observability

`relayna` exposes backend-agnostic runtime observations through async sink
callbacks. The library emits typed dataclass events from long-running loops such
as SSE streaming, worker consumption, and status fanout, but it does not ship a
logging backend, metrics registry, or tracing exporter.

`relayna.observability` owns the event model plus collector and exporter
helpers. It does not own status storage, FastAPI routes, or worker runtime
execution; those live in `relayna.status`, `relayna.api`, and
`relayna.consumer`.

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

If you want simple structured logs, convert dataclasses with `dataclasses.asdict`
before handing them to your logger.

```python
from dataclasses import asdict, is_dataclass
import json


async def sink(event) -> None:
    payload = asdict(event) if is_dataclass(event) else {"event": repr(event)}
    print(json.dumps(payload, default=str))
```

This is a good default when you want observability quickly without introducing a
metrics or tracing dependency.

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

`AggregationConsumer` and `AggregationWorkerRuntime` currently share the retry
and dead-letter observation events:

- `ConsumerRetryScheduled`
- `ConsumerDeadLetterPublished`
- `ConsumerDLQRecordPersistFailed`

Use these when you want to monitor:

- queue start-up and reconnect loops
- message validation failures
- handler exceptions
- lifecycle status automation
- retry and DLQ volume
- DLQ publish success paired with Redis index write failures

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
- The FastAPI lifespan helper does not currently accept observation sinks
  directly. If you want observability there, construct `StatusHub` or
  `SSEStatusStream` manually and pass the sink yourself.
- Observation dataclasses are intended for operational use, not as a stable
  wire protocol between services.
