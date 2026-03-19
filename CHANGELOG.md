# Changelog

All notable changes to this project will be documented in this file.

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
