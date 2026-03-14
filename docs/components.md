# Components

## `relayna.contracts`

Canonical message shapes and alias helpers:

- `TaskEnvelope`
- `StatusEventEnvelope`
- `TerminalStatusSet`
- `normalize_event_aliases`
- `denormalize_document_aliases`

Use this module when you want stable transport envelopes between services.

## `relayna.config`

`RelaynaTopologyConfig` centralizes RabbitMQ topology and queue settings.

Use it to define:

- task exchange and queue names
- status exchange and queue names
- prefetch count
- dead-letter exchange
- stream queue arguments

## `relayna.rabbitmq`

RabbitMQ publishing and routing helpers:

- `RelaynaRabbitClient`
- `TaskIdRoutingStrategy`
- `ShardRoutingStrategy`
- `DirectQueuePublisher`

Use `RelaynaRabbitClient` when you want the shared topology declared and
reused across publishers and consumers.

## `relayna.consumer`

Worker-side task consumption helpers:

- `TaskConsumer`
- `TaskHandler`
- `TaskContext`
- `FailureAction`
- `LifecycleStatusConfig`

Use this module when you want a shared worker loop with consistent envelope
validation, ack/reject behavior, and optional lifecycle status publishing.

## `relayna.status_store`

`RedisStatusStore` stores task history in Redis lists and publishes realtime
events over Redis pubsub. It also deduplicates status events using `event_id`
or a content hash.

## `relayna.status_hub`

`StatusHub` consumes the shared RabbitMQ status queue or stream and writes
normalized events into `RedisStatusStore`.

Use this as the bridge between RabbitMQ transport and Redis-backed clients.

## `relayna.sse`

`SSEStatusStream` combines replayed history with realtime pubsub updates for
client-facing event streams. It also supports keepalive comments and
`Last-Event-ID` resume. Relayna status publishers generate `event_id`
automatically when one is not provided.

## `relayna.history`

`StreamHistoryReader` replays RabbitMQ stream history for debugging and history
endpoints.

## `relayna.fastapi`

FastAPI integration helpers:

- `create_relayna_lifespan`
- `get_relayna_runtime`
- `create_status_router`

Use `create_relayna_lifespan` when you want Relayna to manage RabbitMQ, Redis,
and the `StatusHub` background task while your service still owns app creation
and route registration.

`create_status_router` provides a small FastAPI router with:

- `/events/{task_id}` for SSE
- `/status/{task_id}` for latest Redis-backed status lookup
- `/history` for stream replay
