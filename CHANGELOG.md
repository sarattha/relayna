# Changelog

All notable changes to this project will be documented in this file.

## 1.4.8 - 2026-05-02

### Added

- Studio now supports Phase 2 Kubernetes infrastructure metrics through Prometheus-backed `metrics_config` on registered services.
- Added Studio backend metrics endpoints for service windows and approximate task windows, covering CPU, memory, requests, limits, restarts, OOMKilled signals, pod phase, readiness, and network I/O.
- Added Studio frontend metrics panels on service and task detail pages, including loading, empty, missing-config, provider-error, and manual task-window states.

### Changed

- Bumped the SDK package version to `1.4.8`, the Studio backend package version to `0.1.7`, and the Studio frontend package version to `0.1.9`.
- Updated release-install documentation to reference `relayna 1.4.8`, `relayna-studio 0.1.7`, and the matching frontend package version `0.1.9`.
- Documented example service registration payloads that combine Loki `log_config` with Prometheus `metrics_config`.

### Fixed

- Studio rejects unsupported high-cardinality task identity labels in Prometheus metrics selectors and normalizes Prometheus configuration/provider failures into Studio API errors.

## 1.4.7 - 2026-05-01

### Added

- Studio operator confirmation dialogs now guard service deletion, disable, and mark-unavailable actions; service deletion requires typing the exact service id before the destructive action is enabled.
- Shared Studio backend egress policy for registered service URLs and Loki URLs, using `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS` and `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS`.
- `RELAYNA_STUDIO_PUSH_INGEST_ENABLED`, defaulting to disabled, for deployments that intentionally opt into direct push ingestion at `POST /studio/ingest/events`.

### Changed

- Bumped the SDK package version to `1.4.7`, the Studio backend package version to `0.1.6`, and the Studio frontend package version to `0.1.8`.
- Updated release-install documentation to reference `relayna 1.4.7`, `relayna-studio 0.1.6`, and the matching frontend package version `0.1.8`.
- Documented AKS service DNS suffix allowlisting, explicit CIDR allowlisting for literal private IP targets, and pull-sync as the default internal Studio ingestion path.

### Fixed

- Studio now validates registered service `base_url` and Loki `log_config.base_url` at create/update time and immediately before backend-origin requests, preventing stored or caller-controlled URLs from bypassing the current egress policy.
- Federation reads, health refreshes, event pull-sync, capability refreshes, and Loki log queries now refuse disallowed loopback, link-local, private, multicast, unspecified, or otherwise untrusted literal IP targets unless explicitly CIDR-allowlisted.
- Direct Studio push event ingestion now returns `403` unless explicitly enabled, while registered-service pull-sync remains available by default.

## Studio Backend 0.1.4 - 2026-04-23

### Added

- JSON-aware Studio log rendering in both service and task panels: parseable JSON objects and arrays are pretty-printed, while plain-text and ANSI-styled logs continue to render unchanged.
- Operator documentation now recommends `structlog` JSON output for Loki-backed services so Studio can display structured logs directly.
- Manual `from`/`to` time-window controls for Studio service logs and service activity timelines, with matching backend query support on `GET /studio/services/{service_id}/events`.

### Changed

- Bumped the Studio backend package version to `0.1.4`.
- Kept the SDK package version at `1.4.6` while updating Studio-only functionality and documentation.
- Kept the Studio backend dependency floor at `relayna>=1.4.6`.
- Release-install documentation now references `relayna 1.4.6` and `relayna-studio 0.1.4`.
- Removed the direct `rich` dev dependency from the SDK project; `rich` remains only where pulled transitively by other tooling.

## 1.4.6 - 2026-04-22

### Added

- Detailed operator and integration documentation for Studio AKS Loki log configuration, including service-scoped app discovery, task-text matching, and task-window usage in the service and task views.
- Expanded broker DLQ documentation across the README and Studio guides, including service-side setup, capability advertisement, Studio federation expectations, and example requests for `/broker/dlq/messages`.

### Changed

- Bumped the SDK package version to `1.4.6` and the Studio backend package version to `0.1.3`.
- Updated the Studio backend package dependency floor to require `relayna>=1.4.6`.
- Release-install documentation now references `relayna 1.4.6` and `relayna-studio 0.1.3`.

## 1.4.5 - 2026-04-21

### Added

- Registered-services auto-refresh in the Studio frontend via shared `StudioServicesProvider` polling, keeping the control-plane registry view aligned with scheduled backend health refreshes without manual browser reloads.
- Source-aware Studio log pipeline support via `log_config.source_label`, normalized `source` fields on Studio log entries, and exact-match `source` filtering on `GET /studio/services/{service_id}/logs` and `GET /studio/tasks/{service_id}/{task_id}/logs`.
- Source badges and ANSI-rendered log message presentation in Studio service and task log panels, plus regression coverage for source-aware filtering and rendered log output.
- Route-level Studio UI regression coverage for registered-services polling, including silent background refresh and timer cleanup on unmount.

### Changed

- Bumped the SDK package version to `1.4.5` and the Studio backend package version to `0.1.2`.
- Updated the Studio backend package dependency floor to require `relayna>=1.4.5`.
- Release-install documentation and mock service capability payloads now reference `relayna 1.4.5`.
- The internal Studio roadmap now marks feature 6, Log pipeline, as implemented, and marks the overlapping source-aware log UI items in feature 7 complete.
- The internal Studio roadmap now marks feature 7, Control-plane UI expansion, as implemented after the remaining registered-services auto-refresh items landed.

### Fixed

- SDK lint regressions in broker DLQ inspection and FastAPI lifespan test imports so the root quality gates pass cleanly again.

## 1.4.4 - 2026-04-12

### Changed

- Bumped the SDK package version to `1.4.4` and the Studio backend package version to `0.1.1`.
- Updated the Studio backend package dependency floor to require `relayna>=1.4.4`.

### Fixed

- Studio pull-sync background processing now logs transient `sync_registered_services()` failures and continues running instead of terminating the worker for the rest of the process lifetime.
- Studio task search now uses an unambiguous encoded composite document id for `(service_id, task_id)`, preventing search-index collisions when either identifier contains `:`.

## 1.4.3 - 2026-04-12

### Added

- Redis-backed Studio search and retention support via `relayna.studio.search`, including retained task/service search documents, indexed `GET /studio/tasks/search` and `GET /studio/services/search`, startup backfill from retained Studio events, and a scheduled retention pruning worker.
- Studio frontend indexed search UX for retained task discovery and service discovery in `apps/studio/`, plus backend/frontend regression coverage for the new search contracts and retention behavior.

### Changed

- `create_studio_app(...)` now wires the Studio search subsystem and mounts the indexed search routes alongside the existing registry, federation, events, health, and logs surfaces.
- The internal Studio roadmap now marks feature 10, Search and retention, as implemented.
- Bumped the package version to `1.4.3`.

### Fixed

- `GET /studio/services/search` now resolves to the search handler before the catch-all `/studio/services/{service_id}` route.
- Studio search now treats offsetless ISO timestamps as UTC-aware, preventing `datetime-local` task-search filters from mixing naive and aware datetimes at runtime.

## 1.4.2 - 2026-04-12

### Added

- Studio health and liveness support via `relayna.studio.health`, including persisted service health documents, scheduled health refresh, service health routes, and merged health summaries on Studio service responses.
- Optional worker-heartbeat capability support through `health.workers` and `create_worker_health_router(...)` for Relayna services that want to expose worker liveness to Studio.

### Changed

- Studio service activity freshness now uses monotonic service-level status and observation timestamps, preventing out-of-order event ingestion from regressing health freshness.
- The Studio service detail page now computes “Latest observed activity” from the newer of the latest status and observation timestamps instead of always preferring status events.
- Bumped the package version to `1.4.2`.

### Fixed

- `make test` roadmap assertions now match the current internal roadmap shape, including feature 11.
- Worker-health route typing now satisfies static typechecking.

## 1.4.1 - 2026-04-11

### Added

- Route-based Studio operator-console UI in `apps/studio/`, including dedicated `/services`, `/services/{service_id}`, `/services/{service_id}/topology`, `/services/{service_id}/dlq`, `/tasks/search`, and `/tasks/{service_id}/{task_id}` screens.
- Shared Studio frontend API, typed route-safe models, and routed page modules for service detail, topology, DLQ exploration, task search, and federated task detail.
- Route-oriented frontend coverage for Studio navigation, service-scoped reads, task-scoped reads, and SSE lifecycle behavior.

### Changed

- The Studio frontend now treats routed control-plane screens as the primary UI instead of a single-page registry plus task-inspector flow.
- The internal Studio roadmap now marks feature 7, Control-plane UI expansion, as implemented.
- Bumped the package version to `1.4.1`.

### Fixed

- Registry create and update failures in the Studio services page now surface through the operator-visible error banner instead of failing silently.
- Failed service deletion from the Studio services page now keeps the edit context in place until the backend delete succeeds.

## 1.4.0 - 2026-04-11

### Added

- Studio log pipeline support via `relayna.studio.logs`, including `StudioLogQuery`, `StudioLogEntry`, `StudioLogListResponse`, `StudioLogQueryService`, `create_studio_logs_router(...)`, and a first pluggable `LokiLogProvider`.
- New Studio backend log routes at `GET /studio/services/{service_id}/logs` and `GET /studio/tasks/{service_id}/{task_id}/logs` with normalized service/task-scoped log query behavior.
- Per-service Studio registry `log_config` support so operators can configure Loki log access separately for each registered service.
- Studio UI log configuration fields plus service-level and task-level log panels in `apps/studio/` for querying logs alongside existing control-plane views.

### Changed

- `create_studio_app(...)` now wires the Studio log-query service and mounts the Studio log routes next to registry, federation, and events.
- The internal Studio roadmap now marks feature 6, Log pipeline, as implemented.
- Bumped the package version to `1.4.0`.

## 1.3.9 - 2026-04-11

### Fixed

- Studio pull-sync now advances the stored `events.feed` cursor on every successful non-empty sync, so services with an existing cursor continue catching up instead of repeatedly re-reading the same page window.
- Best-effort Studio observation forwarding now keeps the pending batch on non-2xx ingest responses and retries it on a later flush instead of silently dropping events.

### Changed

- Bumped the package version to `1.3.9`.

## 1.3.8 - 2026-04-10

### Added

- Merged Relayna service event-feed primitives via `RedisServiceEventFeedStore`, `GET /events/feed`, and the `events.feed` capability route id.
- Studio control-plane event ingestion via `POST /studio/ingest/events`, Redis-backed Studio event storage, service/task event query routes, and live SSE event streams.
- Studio pull-sync support for healthy registered services that advertise `events.feed`, plus a best-effort observation forwarder helper for services that want push ingestion.
- Studio UI panels for service recent activity and task-level merged timelines, including live updates from Studio-owned SSE routes.

### Changed

- `create_relayna_lifespan(...)` now wires an optional merged service event feed alongside status and observation persistence.
- `create_studio_app(...)` now mounts Studio event ingest/query/SSE routes and manages a background pull-sync worker.
- The internal Studio roadmap now marks feature 5, Aggregated event and observation ingestion, as implemented.

## 1.3.7 - 2026-04-10

### Added

- Cross-service Studio identity primitives via `StudioTaskPointer`, `StudioTaskRef`, `StudioTaskJoin`, `StudioJoinWarning`, and `JoinMode`.
- Additive `task_ref` normalization across federated Studio task-bearing responses, including status, history, DLQ message lists, execution graphs, task search, and task detail payloads.
- Opt-in cross-service join support on `GET /studio/tasks/search` and `GET /studio/tasks/{service_id}/{task_id}` through `join=none|correlation|lineage|all`.
- Studio task-detail UI panels for correlation id, parent refs, child refs, joined refs, and join warnings.

### Changed

- Studio federation now treats non-`capabilities_v1` services as legacy for route-level `404` detection, so legacy services fall back to history reads instead of being misclassified as missing tasks.
- Cross-service join resolution now skips emitting joins when candidate scans are incomplete and returns an explicit warning instead of treating partial scans as uniquely resolved.
- The internal Studio roadmap now marks feature 4, Cross-service identity model, as implemented.
- Bumped the package version to `1.3.7`.

## 1.3.6 - 2026-04-09

### Added

- Federated Studio control-plane reads via `relayna.studio.create_federation_router(...)` and `StudioFederationService`, including service-scoped proxy routes for status, history, workflow topology, DLQ messages, and execution graphs.
- Cross-service exact-`task_id` search at `GET /studio/tasks/search` plus composite task detail reads at `GET /studio/tasks/{service_id}/{task_id}`.
- Normalized Studio federation error responses and backend coverage for timeout, auth failure, unsupported route, and upstream not-found handling.

### Changed

- `create_studio_app(...)` now mounts the federated Studio read surface and manages one shared `httpx.AsyncClient` for upstream Relayna service reads.
- The Studio frontend task inspector now reads task details and execution graphs through `/studio/tasks/{service_id}/{task_id}` and no longer calls arbitrary Relayna service base URLs from the browser.
- The internal Studio roadmap now marks feature 3, Federated API aggregation layer, as implemented.
- Bumped the package version to `1.3.6`.

## 1.3.5 - 2026-04-09

### Added

- Redis-backed Studio service-registry primitives via `relayna.studio`, including `ServiceRecord`, `RedisServiceRegistryStore`, `create_service_registry_router(...)`, and `create_studio_app(...)`.
- Studio backend CRUD routes at `/studio/services` plus live capability refresh at `POST /studio/services/{service_id}/refresh`.
- Service-registry UI in `apps/studio/` for create, edit, inspect, enable, disable, mark-unavailable, and delete flows.
- Typed capability discovery via `CapabilityDocument`, `create_capabilities_router(...)`, and `GET /relayna/capabilities`.
- Explicit capability route-id exports and merge helpers so Relayna runtimes can declare supported status, DLQ, workflow, and execution routes without introspecting mounted FastAPI routes.

### Changed

- The Studio frontend now defaults to the control-plane service-registry surface while retaining the direct execution-graph inspector as a secondary tool until federated reads land.
- Studio capability refresh now stores live capability documents, synthesizes a deterministic legacy fallback document for services that return `404`/`405`/`501` on `/relayna/capabilities`, and returns `502` without overwriting stored data on network or schema failures.
- Shared topology-kind detection now lives in a reusable helper used by both execution-graph generation and the capability discovery route.
- `httpx` is now a runtime dependency because Studio capability refresh performs backend HTTP fetches in production.
- Release-install examples now reference `1.3.5`.
- Bumped the package version to `1.3.5`.

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
