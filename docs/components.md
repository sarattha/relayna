# Components

## Public modules

These packages are part of the documented v2 API surface.

## `relayna.topology`

RabbitMQ topology definitions and routing strategies:

- `RelaynaTopology`
- `SharedTasksSharedStatusTopology`
- `SharedTasksSharedStatusShardedAggregationTopology`
- `RoutedTasksSharedStatusTopology`
- `RoutedTasksSharedStatusShardedAggregationTopology`
- `SharedStatusWorkflowTopology`
- `WorkflowStage`
- `WorkflowEntryRoute`
- `TaskIdRoutingStrategy`
- `ShardRoutingStrategy`
- `TaskTypeRoutingStrategy`

This package owns topology declarations only. Use it to choose the queue and
exchange shape your service should run with. It does not own publishing,
consumer runtimes, or Redis-backed status storage.

For workflow topologies, `WorkflowStage` is the primary contract surface. It
now carries both transport configuration and optional execution-contract
metadata such as accepted/produced actions, downstream stage constraints,
timeouts, retry overrides, local inflight limits, and dedup/idempotency keys.

For sharded topologies, `aggregation_queue_template` and
`aggregation_queue_name_prefix` let you namespace aggregation queues per
deployment or test environment. Topology constructors also expose first-class
worker queue argument fields such as `task_consumer_timeout_ms`,
`task_single_active_consumer`, `task_max_priority`, `task_queue_type`,
`aggregation_consumer_timeout_ms`, `aggregation_single_active_consumer`,
`aggregation_max_priority`, and `aggregation_queue_type`, plus explicit mapping
escape hatches such as `task_queue_kwargs`, `aggregation_queue_kwargs`, and
`status_queue_kwargs` for broker-specific queue arguments. The
getting-started guide lists the full native field-to-RabbitMQ-argument mapping
for task, status, and aggregation queues, including the generic
`*_queue_arguments_overrides` and `*_queue_kwargs` escape hatches.

## `relayna.contracts`

Canonical message envelopes and compatibility helpers:

- `TaskEnvelope`
- `StatusEventEnvelope`
- `WorkflowEnvelope`
- `TerminalStatusSet`
- `normalize_event_aliases`
- `denormalize_document_aliases`

This package owns wire-format contracts only. It does not own RabbitMQ
transport, FastAPI routes, or worker execution.

## `relayna.rabbitmq`

RabbitMQ publishing and topology helpers:

- `RelaynaRabbitClient`
- `RetryInfrastructure`
- `DirectQueuePublisher`
- `declare_stream_queue`

This package owns RabbitMQ transport behavior: connection lifecycle, queue and
exchange declarations, topology-driven publishing, raw queue publishing, and
retry/DLQ broker infrastructure. Relayna retry metadata is carried in RabbitMQ
`x-relayna-*` headers rather than rewriting the payload body.

## `relayna.consumer`

Worker-side helpers:

- `TaskConsumer`
- `WorkflowConsumer`
- `AggregationConsumer`
- `AggregationWorkerRuntime`
- `TaskContext`
- `WorkflowContext`
- `TaskHandler`
- `WorkflowHandler`
- `AggregationHandler`
- `FailureAction`
- `LifecycleStatusConfig`
- `RetryPolicy`
- `RetryStatusConfig`

This module provides validated task delivery, shard-aware aggregation
consumption, optional lifecycle status publishing, broker-delayed retry and
dead-letter behavior, manual handoff retry through `TaskContext.manual_retry`,
stage-aware workflow delivery, and a helper runtime for aggregation workers
outside FastAPI. `TaskConsumer`, `WorkflowConsumer`, `AggregationConsumer`, and
`AggregationWorkerRuntime` also expose `consume_timeout_seconds` to control how
long the local runtime waits for the next message before looping again.

This package owns worker execution and handler context. It does not own the
FastAPI runtime or the RabbitMQ topology declarations themselves.

## `relayna.status`

Status storage, hub, streaming, and stream-history readers:

- `RedisStatusStore`
- `StatusHub`
- `SSEStatusStream`
- `StreamHistoryReader`

This package is the unified home for user-facing progress state, Redis-backed
history, SSE delivery, and bounded stream replay. It also owns the
RabbitMQ-to-Redis `StatusHub` bridge.

## `relayna.api`

FastAPI integration helpers:

- `create_relayna_lifespan`
- `get_relayna_runtime`
- `create_status_router`
- `sse_response`
- `create_execution_router`
- `create_workflow_router`
- `create_dlq_router`

This package owns FastAPI runtime composition only: lifespan setup, runtime
lookup, and route factories. It composes the lower-level runtime packages and
does not own RabbitMQ transport, Redis storage, or workflow contracts directly.

## `relayna.workflow`

Workflow control-plane helpers:

- `StageRegistry`
- `StagePolicy`
- `WorkflowAction`
- `TransitionRule`
- `FanInProgress`
- `WorkflowRunState`
- `ReplayRequest`
- `WorkflowDiagnosis`

This package owns orchestration concepts that sit above message transport:
fan-in state, run-state read models, replay, and operator diagnostics.
`StageRegistry`, `StagePolicy`, and `TransitionRule` remain available as
compatibility adapters, but `WorkflowStage` is the preferred configuration
surface for new workflow definitions.

## `relayna.mcp`

MCP-facing adapters, resources, and operator tools for exposing Relayna
runtime state to agentic clients. It sits on top of the runtime packages rather
than owning transport or storage primitives itself.

## `relayna.studio`

Backend-side view builders for workflow topology, run detail, stage health,
execution graph, and DLQ explorer payloads consumed by the Studio frontend.
This package is the backend presenter layer; the actual frontend app lives in
`apps/studio/`.

## `relayna.dlq`

Dead-letter models, persistence, service operations, and replay helpers.

This package owns DLQ state and replay orchestration. It is separate from
`relayna.rabbitmq` so broker infrastructure and DLQ indexing/replay remain
distinct concerns.

## `relayna.observability`

Structured observation types and helper functions for feeding runtime events
into logging, metrics, tracing, or debugging sinks. See
[Observability](observability.md) for detailed event groups and usage examples.
This package also owns persisted task-linked observation storage and execution
graph reconstruction:

- `RedisObservationStore`
- `make_redis_observation_sink`
- `ExecutionGraph`
- `ExecutionGraphNode`
- `ExecutionGraphEdge`
- `ExecutionGraphSummary`
- `ExecutionGraphService`
- `build_execution_graph`
- `execution_graph_mermaid`

## Internal packages

`relayna.storage` contains internal Redis models, repository helpers, and
retention logic shared by the public runtime packages. It is intentionally not
part of the documented public API surface.
