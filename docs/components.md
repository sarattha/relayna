# Components

## Public modules

These modules are part of the documented v1 API surface.

## `relayna.contracts`

Canonical message envelopes and compatibility helpers:

- `TaskEnvelope`
- `StatusEventEnvelope`
- `TerminalStatusSet`
- `normalize_event_aliases`
- `denormalize_document_aliases`

## `relayna.config`

`RelaynaTopologyConfig` centralizes RabbitMQ topology and queue settings for
task and status traffic.

## `relayna.rabbitmq`

RabbitMQ publishing and topology helpers:

- `RelaynaRabbitClient`
- `TaskIdRoutingStrategy`
- `ShardRoutingStrategy`
- `DirectQueuePublisher`

Use this module when you want shared exchange and queue declaration plus JSON
task and status publishing.

## `relayna.consumer`

Worker-side task consumption helpers:

- `TaskConsumer`
- `TaskContext`
- `TaskHandler`
- `FailureAction`
- `LifecycleStatusConfig`

This module provides validated task delivery, consistent ack/reject behavior,
and optional lifecycle status publishing.

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
