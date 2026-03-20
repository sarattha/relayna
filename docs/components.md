# Components

## Public modules

These modules are part of the documented v1 API surface.

## `relayna.topology`

RabbitMQ topology definitions and routing strategies:

- `RelaynaTopology`
- `SharedTasksSharedStatusTopology`
- `SharedTasksSharedStatusShardedAggregationTopology`
- `TaskIdRoutingStrategy`
- `ShardRoutingStrategy`

Use this module to choose the topology shape your service should run with.
For sharded topologies, `aggregation_queue_template` and
`aggregation_queue_name_prefix` let you namespace aggregation queues per
deployment or test environment.

## `relayna.contracts`

Canonical message envelopes and compatibility helpers:

- `TaskEnvelope`
- `StatusEventEnvelope`
- `TerminalStatusSet`
- `normalize_event_aliases`
- `denormalize_document_aliases`

## `relayna.rabbitmq`

RabbitMQ publishing and topology helpers:

- `RelaynaRabbitClient`
- `RetryInfrastructure`
- `DirectQueuePublisher`
- `declare_stream_queue`

Use this module when you want topology-driven exchange and queue declaration
plus JSON task, status, and aggregation-status publishing. It also provides
raw queue publishing and retry/DLQ queue declaration helpers for worker paths.

## `relayna.consumer`

Worker-side helpers:

- `TaskConsumer`
- `AggregationConsumer`
- `AggregationWorkerRuntime`
- `TaskContext`
- `TaskHandler`
- `AggregationHandler`
- `FailureAction`
- `LifecycleStatusConfig`
- `RetryPolicy`
- `RetryStatusConfig`

This module provides validated task delivery, shard-aware aggregation
consumption, optional lifecycle status publishing, broker-delayed retry and
dead-letter behavior, and a helper runtime for aggregation workers outside
FastAPI.

## `relayna.status_store`

`RedisStatusStore` stores task history in Redis lists, deduplicates status
events, and publishes live updates over Redis pubsub.

## `relayna.status_hub`

`StatusHub` consumes shared RabbitMQ status traffic and writes normalized events
into `RedisStatusStore`.

## `relayna.sse`

`SSEStatusStream` combines replayed Redis history with live pubsub delivery for
client-facing SSE streams. It also supports `Last-Event-ID` resume and optional
output adapters such as `document_output_adapter`.

## `relayna.history`

`StreamHistoryReader` replays status history directly from a RabbitMQ stream for
bounded operational endpoints and debugging reads.

## `relayna.fastapi`

FastAPI integration helpers:

- `create_relayna_lifespan`
- `get_relayna_runtime`
- `create_status_router`
- `sse_response`

## `relayna.observability`

Structured observation types and helper functions for feeding runtime events
into logging, metrics, tracing, or debugging sinks.
