# Studio Backend

This guide covers the deployable `relayna-studio` backend packaged under
`studio/backend/`. It is the centralized control-plane service that federates
registered Relayna services for the Studio frontend.

If you are integrating a downstream service with the SDK first, start with
[Getting Started](getting-started.md).

## Role In The Architecture

The Studio backend is the single API surface the browser should talk to for
control-plane operations.

It owns:

- a Redis-backed service registry
- Redis-backed event, health, and task-search stores
- a federated read layer that proxies registered Relayna services
- background workers for event pull sync, health refresh, and retention pruning

The intended request flow is:

```text
browser -> Studio frontend -> /studio/* -> Studio backend -> registered Relayna services
```

The browser should not call each registered service directly.

## Package And Runtime Model

The backend is packaged separately from the SDK:

- source package root: `studio/backend/src/relayna_studio`
- distribution name: `relayna-studio`
- CLI entrypoint: `relayna-studio`
- ASGI app import: `relayna_studio.asgi:app`

Repo-backed build targets:

```bash
docker build -f studio/backend/Dockerfile -t relayna-studio-backend .
```

The backend image builds both wheels from the repo root:

- `relayna`
- `relayna-studio`

That split matters operationally: downstream services consume the SDK, while the
central control plane runs the backend package.

## Runtime Configuration

The backend reads configuration from `StudioBackendSettings.from_env()`.

### Required

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_REDIS_URL` | none | Required Redis connection for registry, events, health, and search state. |

### Optional network and process settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_TITLE` | `Relayna Studio Backend` | FastAPI app title and operator-facing name. |
| `RELAYNA_STUDIO_HOST` | `0.0.0.0` | Bind address for `uvicorn`. |
| `RELAYNA_STUDIO_PORT` | `8000` | Listening port. |
| `RELAYNA_STUDIO_APP_STATE_KEY` | `studio` | Key used on `app.state` for the runtime object. |
| `RELAYNA_STUDIO_FEDERATION_TIMEOUT_SECONDS` | `5.0` | Timeout used by the backend HTTP client when talking to registered services. |

### Optional registry and capability settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_REGISTRY_PREFIX` | `studio:services` | Redis prefix for persisted service registry entries. |
| `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS` | unset | Comma-separated host suffix allowlist for capability refresh targets. |
| `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS` | unset | Comma-separated CIDR allowlist for capability refresh targets. |
| `RELAYNA_STUDIO_CAPABILITY_STALE_AFTER_SECONDS` | `180` | Threshold after which a cached capability snapshot is considered stale. |

### Optional event ingestion settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_EVENT_STORE_PREFIX` | `studio:events` | Redis prefix for retained Studio events. |
| `RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS` | `86400` | TTL for retained events. Use `none`, `null`, or `off` to disable TTL. |
| `RELAYNA_STUDIO_EVENT_HISTORY_MAXLEN` | `5000` | Max retained event history length. |
| `RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS` | `5.0` | Interval for the background pull-sync worker. Use `none`, `null`, or `off` to disable the worker. |

### Optional health settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_HEALTH_STORE_PREFIX` | `studio:health` | Redis prefix for service health snapshots. |
| `RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS` | `60.0` | Interval for health refresh polling. The default Studio frontend services screen polls `/studio/services` on a similar cadence so updated health summaries appear without a manual page reload. Use `none`, `null`, or `off` to disable. |
| `RELAYNA_STUDIO_OBSERVATION_STALE_AFTER_SECONDS` | `300` | Threshold after which ingested observations are considered stale. |
| `RELAYNA_STUDIO_WORKER_HEARTBEAT_STALE_AFTER_SECONDS` | `90` | Threshold after which worker heartbeats are considered stale. |

### Optional search and retention settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_TASK_SEARCH_INDEX_PREFIX` | `studio:search` | Redis prefix for task search index state. |
| `RELAYNA_STUDIO_TASK_INDEX_TTL_SECONDS` | `86400` | TTL for search index entries. |
| `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS` | `60.0` | Interval for retention pruning. Use `none`, `null`, or `off` to disable. |

### Practical recommendations

- Local development:
  - keep defaults for intervals and prefixes
  - use `redis://localhost:6379/0`
- Shared dev or staging:
  - keep intervals enabled
  - use a dedicated Redis DB or dedicated prefixes
  - set capability refresh allowlists explicitly
- Production:
  - set explicit allowlists for capability refresh
  - use deployment-specific Redis isolation
  - keep retention and health intervals intentional rather than relying on
    defaults

## Redis Requirements And Data Use

Redis is mandatory for the Studio backend. It stores:

- service registry records
- retained event envelopes
- service health snapshots
- task search index state

Important prefixes:

- registry: `studio:services`
- events: `studio:events`
- health: `studio:health`
- task search: `studio:search`

TTL behavior:

- event retention uses `RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS`
- task index retention uses `RELAYNA_STUDIO_TASK_INDEX_TTL_SECONDS`
- prune behavior is driven by `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS`

If you share one Redis instance across environments, change prefixes or DBs so
local and non-local control-plane state cannot collide.

## Local Source Run

From the repo root, prepare Python dependencies:

```bash
uv sync --extra dev
```

From `studio/backend/`, run the packaged backend target:

```bash
make run RELAYNA_STUDIO_REDIS_URL=redis://localhost:6379/0
```

Or run directly from source:

```bash
PYTHONPATH=src:studio/backend/src \
RELAYNA_STUDIO_REDIS_URL=redis://localhost:6379/0 \
uv run python -m relayna_studio
```

The CLI entrypoint ultimately serves `uvicorn relayna_studio.asgi:app`.

## Docker Run

Build:

```bash
docker build -f studio/backend/Dockerfile -t relayna-studio-backend .
```

Run:

```bash
docker run --rm -p 8000:8000 \
  -e RELAYNA_STUDIO_REDIS_URL=redis://host.docker.internal:6379/0 \
  -e RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS=.svc.local,.cluster.local \
  relayna-studio-backend
```

The container defaults to:

- host: `0.0.0.0`
- port: `8000`

## Service Registration And Backend Expectations

Each registered service record is anchored by:

- `service_id`
- `name`
- `base_url`
- `environment`
- `tags`
- `auth_mode`
- optional `log_config`

Operational expectations:

- `service_id` should remain stable across deploys
- `base_url` must be reachable from the Studio backend, not merely from a
  developer browser
- `base_url` must be `http` or `https` and must not include query strings,
  fragments, or user info
- `environment` should be meaningful to operators, not incidental
- `auth_mode` should reflect how the backend is expected to reach the service

Capability refresh guardrails:

- `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS` filters allowed host suffixes
- `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS` filters allowed IP
  networks

If these are set too tightly, refresh attempts can fail even when the service is
otherwise healthy.

### Loki `log_config` for AKS service/app/task views

Studio log queries are backend-driven. The browser never talks to Loki
directly. Instead, each registered service may carry a `log_config` that tells
the Studio backend how to query Loki for that service.

For the AKS deployment pattern introduced in the latest log-scoping feature,
the intended mapping is:

- one stable `service` label for the logical Relayna service
- one `app` label for the concrete emitter or workload, such as API, worker,
  aggregator, or scaled-job pods
- task IDs embedded in log line text when they are not available as Loki labels

Recommended registration shape:

```json
{
  "service_id": "checker-service",
  "name": "Checker Service",
  "base_url": "http://checker-service-api.default.svc.cluster.local:8000",
  "environment": "prod-aks",
  "tags": ["checker", "aks"],
  "auth_mode": "internal_network",
  "log_config": {
    "provider": "loki",
    "base_url": "http://loki.default.svc.cluster.local:3100",
    "tenant_id": null,
    "service_selector_labels": {
      "service": "checker-service"
    },
    "source_label": "app",
    "task_match_mode": "contains",
    "task_match_template": "{task_id}",
    "task_id_label": null,
    "correlation_id_label": "correlation_id",
    "level_label": "level"
  }
}
```

Field-by-field guidance:

- `service_selector_labels`
  - the base Loki selector for service-scoped reads
  - for AKS, this is usually `{ "service": "<logical-service-name>" }`
- `source_label`
  - the Loki label used to distinguish emitters inside one service scope
  - for AKS, this is usually `"app"`
- `task_match_mode`
  - `"label"` when `task_id` exists as a Loki label
  - `"contains"` when the task ID appears as plain text in the log line
  - `"regex"` when task matching requires a structured log pattern
- `task_match_template`
  - optional template rendered with `{task_id}`
  - default behavior is effectively the raw task id when omitted
- `correlation_id_label`
  - optional exact-match Loki label for correlation-scoped narrowing
- `level_label`
  - optional exact-match Loki label for level filtering

How the backend turns that config into Loki queries:

- service page, all logs for the service:
  - `{service="checker-service"}`
- service page, filtered to one app:
  - `{app="checker-service-api",service="checker-service"}`
- task page, task ID inside line text:
  - `{service="checker-service"} |= "checker_endjdbdgsjmaksdhdsdsdd"`
- task page, bounded to a lifecycle window:
  - same query plus Loki `start` and `end` derived from the task timeline or
    manual operator overrides

Important operational behavior:

- the backend merges all matching Loki streams into one time-ordered response
- source/app suggestions in the UI are discovered from returned log entries; the
  backend does not maintain a separate app catalog
- task pages now pass optional `from` and `to` values to the backend, which the
  Loki provider maps to `start` and `end`
- when `task_match_mode` is `"contains"` or `"regex"`, Studio does not require
  `task_id_label`

### Broker DLQ setup for Studio federation

Studio can federate live broker DLQ inspection through:

- `/studio/services/{service_id}/broker/dlq/messages`

That only works when the registered Relayna service exposes:

- `GET /broker/dlq/messages`
- a capability document advertising `broker.dlq.messages`

Minimal service-side setup:

```python
from fastapi import FastAPI

from relayna.api import (
    BROKER_DLQ_CAPABILITY_ROUTE_IDS,
    DLQ_CAPABILITY_ROUTE_IDS,
    STATUS_CAPABILITY_ROUTE_IDS,
    create_capabilities_router,
    create_dlq_router,
    create_relayna_lifespan,
    get_relayna_runtime,
    merge_capability_route_ids,
)
from relayna.dlq import DLQService, RabbitMQManagementDLQInspector

BROKER_DLQ_QUEUE_NAMES = [
    "checker.tasks.queue.dlq",
    "checker.aggregation.queue.0.dlq",
]

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
        dlq_store_prefix="checker-dlq",
        broker_message_inspector=RabbitMQManagementDLQInspector(
            base_url="http://rabbitmq:15672",
            username="guest",
            password="guest",
            vhost="/",
        ),
    )
)
runtime = get_relayna_runtime(app)

app.include_router(
    create_capabilities_router(
        topology=topology,
        supported_routes=merge_capability_route_ids(
            STATUS_CAPABILITY_ROUTE_IDS,
            DLQ_CAPABILITY_ROUTE_IDS,
            BROKER_DLQ_CAPABILITY_ROUTE_IDS,
        ),
        service_title="Checker Service",
    )
)

if runtime.dlq_store is not None:
    app.include_router(
        create_dlq_router(
            dlq_service=DLQService(
                rabbitmq=runtime.rabbitmq,
                dlq_store=runtime.dlq_store,
                status_store=runtime.store,
                broker_message_inspector=runtime.broker_message_inspector,
            ),
            broker_dlq_queue_names=BROKER_DLQ_QUEUE_NAMES,
        )
    )
```

What each part does:

- `broker_message_inspector=RabbitMQManagementDLQInspector(...)`
  - enables live message reads from RabbitMQ Management API
- `broker_dlq_queue_names=[...]`
  - allowlists the DLQ queues the route may inspect
- `BROKER_DLQ_CAPABILITY_ROUTE_IDS`
  - advertises `broker.dlq.messages` in the service capability document so
    Studio can enable broker mode in the UI

Example service-side verification:

```bash
curl -s "http://localhost:8000/broker/dlq/messages?limit=20"
curl -s "http://localhost:8000/broker/dlq/messages?queue_name=checker.tasks.queue.dlq&limit=20"
curl -s "http://localhost:8000/broker/dlq/messages?task_id=task-123&limit=20"
curl -s http://localhost:8000/relayna/capabilities
```

Example Studio-side verification after registration:

```bash
curl -s "http://localhost:8000/studio/services/checker-service/broker/dlq/messages?task_id=task-123&limit=20"
```

Broker-mode reminders:

- this route is emergency/live inspection, not the indexed DLQ source of truth
- responses do not contain indexed `dlq_id`, replay state, or cursor pagination
- indexed `/studio/services/{service_id}/dlq/messages` should remain the default
  operator view when Redis-backed DLQ indexing is healthy

## Background Workers

The backend can run three periodic workers:

- pull sync worker
  - ingests events from registered services into the central event store
  - controlled by `RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS`
- health refresh worker
  - refreshes service health snapshots and staleness state
  - controlled by `RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS`
  - its updated summaries are surfaced in the Studio services screen through frontend polling of `/studio/services`
- retention worker
  - prunes search-related retained state
  - controlled by `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS`

Disable a worker by setting its interval to `none`, `null`, or `off`.

Examples:

```bash
export RELAYNA_STUDIO_PULL_SYNC_INTERVAL_SECONDS=off
export RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS=null
export RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS=none
```

## Operator-Facing API Surface

All backend routes live under `/studio/*`.

The main operator surfaces are:

- registry CRUD
  - `/studio/services`
  - `/studio/services/{service_id}`
  - `/studio/services/{service_id}/refresh`
- health
  - `/studio/services/{service_id}/health/refresh`
- events
  - `/studio/services/{service_id}/events`
  - `/studio/tasks/{service_id}/{task_id}/events`
- logs
  - `/studio/services/{service_id}/logs`
  - `/studio/tasks/{service_id}/{task_id}/logs`
- search
  - `/studio/services/search`
  - `/studio/tasks/search`
- federated detail
  - `/studio/tasks/{service_id}/{task_id}`
- federated workflow and DLQ reads
  - `/studio/services/{service_id}/workflow/topology`
  - `/studio/services/{service_id}/dlq/messages`
  - `/studio/services/{service_id}/broker/dlq/messages`
- federated execution view
  - `/studio/services/{service_id}/executions/{task_id}/graph`

The frontend contract that consumes these routes is documented in
[Studio Frontend](studio-frontend.md).

## Verification

With the backend running on `localhost:8000`, verify the core surfaces:

```bash
curl -s http://localhost:8000/studio/services
curl -s "http://localhost:8000/studio/services/search?limit=20"
curl -s "http://localhost:8000/studio/tasks/search?task_id=task-123"
```

After at least one service is registered:

```bash
curl -s -X POST http://localhost:8000/studio/services/my-service/refresh
curl -s -X POST http://localhost:8000/studio/services/my-service/health/refresh
curl -s http://localhost:8000/studio/tasks/my-service/task-123
curl -s http://localhost:8000/studio/services/my-service/workflow/topology
curl -s http://localhost:8000/studio/services/my-service/dlq/messages
curl -s "http://localhost:8000/studio/services/my-service/broker/dlq/messages?task_id=task-123"
curl -s "http://localhost:8000/studio/services/my-service/logs?limit=20&source=runtime-worker"
curl -s "http://localhost:8000/studio/tasks/my-service/task-123/logs?limit=50&source=api&from=2026-04-22T10:00:00Z&to=2026-04-22T10:15:00Z"
```

Studio log queries normalize a required `source` field on each log entry, sourced from the service's configured `log_config.source_label` when present and falling back to `"unknown"` per entry when the upstream stream omits that label. Both service-scoped and task-scoped log routes also accept an optional exact-match `source` query parameter; requesting it without `log_config.source_label` configured returns `422`.

Task-scoped log routes also accept optional `from` and `to` timestamps. Studio
uses these automatically on the task detail page when it can derive a queued to
terminal lifecycle window from status history or timeline events, and operators
can override that window manually in the UI.

Studio renders ANSI-styled log messages in the browser without changing the raw backend payload. Escape sequences remain in the API response and are interpreted only by the frontend log panels.

Broker DLQ inspection is intentionally separate from indexed DLQ reads:

- `/studio/services/{service_id}/dlq/messages`
  - reads indexed Relayna DLQ records
  - includes replay/index metadata such as `dlq_id` and replay state
  - supports cursor pagination and indexed filters
- `/studio/services/{service_id}/broker/dlq/messages`
  - reads live broker messages through the registered service
  - is available only when the service advertises `broker.dlq.messages`
  - returns a read-only broker payload shape without `dlq_id`, replay state, or cursor pagination

## Troubleshooting

### Redis misconfiguration

Symptoms:

- backend fails at startup
- registry appears empty after restart
- event and search views never retain state

Checks:

- confirm `RELAYNA_STUDIO_REDIS_URL`
- confirm Redis is reachable from the runtime environment
- confirm prefixes or DB selection are not colliding with another deployment

### Capability refresh blocked

Symptoms:

- service registration succeeds
- refresh endpoint fails
- capabilities remain stale or missing

Checks:

- confirm the service `base_url` is reachable from the backend host
- confirm the host suffix matches `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_HOSTS`
- confirm the resolved IP is within `RELAYNA_STUDIO_CAPABILITY_REFRESH_ALLOWED_NETWORKS`

### Service unavailable vs stale capability vs stale observation

- `unavailable`
  - the backend cannot successfully read the service right now
- stale capability
  - the service record exists, but the capability snapshot is older than
    `RELAYNA_STUDIO_CAPABILITY_STALE_AFTER_SECONDS`
- stale observation
  - the service may still exist, but ingested activity is older than
    `RELAYNA_STUDIO_OBSERVATION_STALE_AFTER_SECONDS`

These states are different. A service can be registered but stale, or reachable
for some reads while still showing stale activity.

## Relationship To Downstream Services

The backend works best when downstream services:

- expose `GET /relayna/capabilities`
- expose `GET /relayna/health/workers` when they want Studio to track worker liveness
- emit events into `GET /events/feed`
- expose execution graph, workflow, DLQ, and worker health routes where
  relevant
- keep `service_id`, task IDs, correlation IDs, and base URLs stable

That service-side integration, including worker heartbeat wiring for Studio, is
covered in [Getting Started](getting-started.md).
