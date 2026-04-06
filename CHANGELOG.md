# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

## 1.3.4 - 2026-04-06

### Added

- First-class runtime execution graph support for every Relayna topology through `ExecutionGraph`, `ExecutionGraphService`, `build_execution_graph(...)`, and `GET /executions/{task_id}/graph`.
- Redis-backed observation persistence via `RedisObservationStore` plus `make_redis_observation_sink(...)` so workers can persist task-linked runtime observations for later graph reconstruction.
- Mermaid export via `execution_graph_mermaid(...)` and Studio backend view support via `build_execution_view(...)` for app rendering and docs/debug workflows.

### Changed

- `create_relayna_lifespan(...)` now exposes `observation_store`, `execution_graph_service`, and new observation-store configuration fields for HTTP runtimes that want execution-graph fidelity.
- Consumer and workflow observation events now carry the routing, retry, queue, and lineage metadata needed to reconstruct retries, DLQ edges, sharded aggregation children, and workflow stage transitions.
- `RedisStatusStore` now indexes child task ids from `meta.parent_task_id`, which lets aggregation execution graphs stitch child task timelines back onto the parent task graph.
- The README, observability guide, getting-started guide, component reference, and hosted docs now document execution graphs, Mermaid export, React Flow rendering, and the worker/runtime wiring required for full graphs.
- Release-install examples now reference `1.3.4`.
- Bumped the package version to `1.3.4`.

### Added

- `WorkflowStage` execution-contract fields for stage metadata, action schemas, allowed downstream stages, stage-local timeout/retry/inflight policy, and dedup key selection.
- Shared workflow-contract validation on workflow publish and consume paths, plus Redis-backed contract-store primitives for stage dedup/idempotency bookkeeping.

### Changed

- Workflow topology graph export, `/workflow/stages`, `/workflow/topology`, MCP topology inspection, and studio topology views now expose stage execution-contract metadata.
- `StageRegistry`, `StagePolicy`, and `TransitionRule` remain importable as compatibility adapters, but `WorkflowStage` is now the primary workflow contract surface.

## 1.3.3 - 2026-04-04

### Added

- First-class top-level `priority` support on `TaskEnvelope` and `WorkflowEnvelope`, with RabbitMQ publishing that maps the field to the AMQP message `priority` property.
- Batch-envelope priority handling that applies one shared AMQP priority when all enclosed tasks agree and rejects mixed-priority batches.

### Changed

- `TaskContext.manual_retry(...)`, `WorkflowContext.publish_to_stage(...)`, and `WorkflowContext.publish_workflow_message(...)` now preserve or accept explicit top-level priority values.
- Documentation now explains that priority scheduling requires `x-max-priority`, that queue max-priority values must be between `1` and `255`, and that Relayna rejects publishes above the configured queue max priority.
- Release-install examples now reference `1.3.3`.
- Bumped the package version to `1.3.3`.

### Fixed

- Relayna now clears `aio-pika`'s default priority value when no top-level priority is supplied, preserving the distinction between an unset AMQP priority and an explicit `priority=0`.
- Task and workflow publishes now fail client-side when message priority exceeds the configured `task_max_priority` or `workflow_max_priority`, instead of silently relying on broker-side capping.

## 1.3.2 - 2026-03-30

### Added

- Separate broker-visibility support for DLQ queue inspection via optional `create_dlq_router(..., broker_dlq_queue_names=...)` registration of `GET /broker/dlq/queues`.
- `ConsumerDLQRecordPersistFailed` observability events so DLQ index-write failures can be surfaced without changing best-effort DLQ publishing behavior.

### Changed

- Documentation now states explicitly that `GET /dlq/queues` means “queues known from indexed DLQ records plus live count lookup,” not “list all RabbitMQ DLQ queues.”
- Release-install examples now reference `1.3.2`.
- Bumped the package version to `1.3.2`.

### Fixed

- DLQ record persistence no longer fails silently from an operator perspective; index-write exceptions now emit observability signals while preserving broker dead-letter delivery.
- Added regression coverage for DLQ persist failures and for the distinction between index-backed `/dlq/queues` and broker-backed `/broker/dlq/queues`.

## 1.3.1 - 2026-03-29

### Fixed

- `WorkflowContext.publish_workflow_message(...)` now resolves workflow stages from AMQP topic wildcard bindings such as `planner.*.in` and `planner.#`, matching real RabbitMQ routing behavior instead of requiring exact binding-key equality.

### Changed

- Bumped the package version to `1.3.1`.
- Re-ran the real-stack smoke coverage against local RabbitMQ and Redis for FastAPI status, task worker, workflow, sharded aggregation, queue arguments, aliased batch tasks, manual retry routing, and DLQ replay flows.

## 1.3.0 - 2026-03-29

### Added

- First-class stage-inbox workflow topology support via `SharedStatusWorkflowTopology`, `WorkflowStage`, and `WorkflowEntryRoute`.
- Canonical stage-to-stage workflow transport via `WorkflowEnvelope`.
- Stage-aware RabbitMQ publishing helpers: `publish_workflow(...)`, `publish_to_stage(...)`, `publish_to_entry(...)`, `publish_workflow_message(...)`, and `ensure_workflow_queue(...)`.
- `WorkflowConsumer` and `WorkflowContext` for consuming named workflow stages and publishing downstream workflow hops while preserving shared status behavior.
- Workflow-specific observability events for stage startup, message receipt, downstream publish, ack, and failure.
- Real-stack workflow smoke coverage against local RabbitMQ and Redis.

### Changed

- Documentation now presents stage-inbox workflows as a first-class topology, including planner, re-planner, and writer flow diagrams plus end-to-end usage examples.
- MkDocs now loads Mermaid so workflow diagrams render in the generated docs.
- Bumped the package version to `1.3.0`.

### Fixed

- `WorkflowContext.publish_workflow_message(...)` now resolves destination stages from any valid stage binding key, including alternate entry keys such as re-planner routes, instead of only the canonical publish key.

## 1.2.4 - 2026-03-26

### Changed

- Bumped the package version to `1.2.4`.

## 1.2.3 - 2026-03-24

### Added

- First-class topology constructor fields for common RabbitMQ worker queue arguments, including consumer timeout, single-active-consumer, max priority, and queue type on task and sharded aggregation queues.
- Explicit queue-argument mapping escape hatches on topology constructors for task, aggregation, and status queue broker arguments that Relayna does not model directly.
- Real-stack queue-argument smoke coverage against local RabbitMQ and Redis, plus documentation for the new smoke command.

## 1.2.2 - 2026-03-23

### Added

- Manual handoff retry support through `TaskContext.manual_retry(...)`, including routed-task topologies for `task_type`-driven worker handoff while keeping one shared status timeline per `task_id`.
- Real-stack smoke coverage for routed manual retry handoff against local RabbitMQ and Redis.

### Changed

- The README, getting-started guide, and component reference now document routed task topologies in more detail, with concrete examples for handing a task off to a different `task_type`.
- Real-stack helper docs now include the routed manual retry smoke command.

## 1.2.1 - 2026-03-22

### Fixed

- `TaskConsumer` batch-envelope handling now fans the original batch message out into per-item queue messages before handler execution, preventing later failures from causing RabbitMQ to redeliver already-completed batch items.
- FastAPI status/history responses now keep JSON body field names aligned with payload aliases even when `http_aliases` uses a different route/query parameter name.

### Changed

- The README and getting-started guide now document the split between `http_aliases` and `field_aliases`, and explain the batch-envelope fan-out behavior with concrete examples.

## 1.2.0 - 2026-03-22

### Added

- Payload and FastAPI alias support via `relayna.contracts.ContractAliasConfig`, including aliased task/status inputs and aliased `/events`, `/status`, `/history`, and DLQ route shapes when the same config is wired through FastAPI.
- Batch task publishing through `RelaynaRabbitClient.publish_tasks(..., mode="batch_envelope")`, with per-item worker metadata on `TaskContext.batch_id`, `TaskContext.batch_index`, and `TaskContext.batch_size`.
- Real-stack smoke coverage for aliased batch-envelope task handling against local RabbitMQ and Redis.

### Changed

- The getting-started and README guides now include concrete aliasing and batch-envelope examples, including sample HTTP/SSE payloads and worker behavior notes.

## 1.1.6 - 2026-03-21

### Added

- Redis-backed DLQ indexing via `relayna.dlq.RedisDLQStore`, including persisted DLQ record detail for inspection and replay.
- `relayna.dlq.DLQService` plus `create_dlq_router(...)` for FastAPI queue summaries, DLQ message list/detail endpoints, and controlled replay.
- Optional `dlq_store_prefix` / `dlq_store_ttl_seconds` support in `create_relayna_lifespan(...)` so FastAPI apps can share a DLQ store with workers.

### Changed

- `TaskConsumer`, `AggregationConsumer`, and `AggregationWorkerRuntime` now accept an optional `dlq_store=...` to persist dead-letter records alongside RabbitMQ DLQ publishing.
- `RelaynaRabbitClient` now exposes passive queue inspection for DLQ message-count monitoring.
- Documentation and smoke coverage now include the DLQ monitoring API and replay workflow.

## 1.1.5 - 2026-03-19

### Changed

- `relayna` is now topology-only: `relayna.config`, `RelaynaTopologyConfig`, `RelaynaRabbitClient(config=...)`, `RelaynaRabbitClient.config`, and `create_relayna_lifespan(topology_config=...)` are removed.
- Documentation, tests, and packaging smoke checks now reference `relayna.topology` as the only supported configuration entrypoint.
- Packaging metadata now declares Python 3.14 support, and CI runs the full workflow on both Python 3.13 and 3.14.

### Fixed

- `SharedTasksSharedStatusShardedAggregationTopology.declare_queues(...)` now avoids a Python 3.13 `super(type, obj)` failure that can occur with `@dataclass(slots=True)` subclasses during application startup.

## 1.1.0 - 2026-03-18

### Added

- First-class RabbitMQ topology classes via `relayna.topology`.
- `SharedTasksSharedStatusTopology` as the explicit default topology.
- `SharedTasksSharedStatusShardedAggregationTopology` for shard-aware aggregation queues on the shared status exchange.
- `RelaynaRabbitClient.publish_aggregation_status(...)` for shard-routed aggregation work items that remain visible to `StatusHub`, `StreamHistoryReader`, and SSE consumers.
- `AggregationConsumer` and `AggregationWorkerRuntime` for running shard-owned aggregation workers outside the FastAPI lifecycle.

### Changed

- `RelaynaRabbitClient` and `create_relayna_lifespan(...)` now accept named topology objects as the primary API.
- Documentation now uses the named topology classes as the primary configuration examples.

## 1.0.1 - 2026-03-16

### Fixed

- SSE `/events/{task_id}` streams no longer risk stalling after keepalive timeouts when using Redis pubsub clients that expose `get_message(timeout=...)`.

## 1.0.0 - 2026-03-15

`relayna` is now documented and packaged as a public, semver-stable library.

### Stable public modules

The v1 public API consists of the documented symbols from:

- `relayna.contracts`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status_store`
- `relayna.status_hub`
- `relayna.sse`
- `relayna.history`
- `relayna.fastapi`
- `relayna.observability`

The package root remains intentionally minimal and only exports
`relayna.__version__`.

### Supported behaviors

- RabbitMQ task publishing and shared status fanout
- Redis-backed status history, deduplication, and pubsub
- Server-Sent Events replay plus live updates
- FastAPI lifecycle and route helpers
- RabbitMQ stream history replay for operational endpoints
- Best-effort observability hooks for runtime loops

### Distribution

GitHub Releases are the canonical installation source for v1:

- wheel artifact: `relayna-1.0.0-py3-none-any.whl`
- source artifact: `relayna-1.0.0.tar.gz`

See the release installation guide in the hosted docs for install commands and
upgrade notes.
