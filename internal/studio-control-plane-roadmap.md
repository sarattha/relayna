# Relayna Studio Control-Plane Roadmap

This internal-only file is the source of truth for the Relayna Studio control-plane roadmap. It is intentionally kept outside `docs/` and must not be linked from `mkdocs.yml` or treated as public product documentation.

## Status Summary

| # | Feature | Status | Last Updated |
| --- | --- | --- | --- |
| 1 | Service registry | partially_implemented | 2026-04-08 |
| 2 | Capability discovery and version handshake | implemented | 2026-04-09 |
| 3 | Federated API aggregation layer | implemented | 2026-04-09 |
| 4 | Cross-service identity model | implemented | 2026-04-10 |
| 5 | Aggregated event and observation ingestion | implemented | 2026-04-10 |
| 6 | Log pipeline | implemented | 2026-04-11 |
| 7 | Control-plane UI expansion | partially_implemented | 2026-04-08 |
| 8 | Auth, trust, and operator controls | planned | 2026-04-08 |
| 9 | Health and liveness model | partially_implemented | 2026-04-08 |
| 10 | Search and retention | partially_implemented | 2026-04-08 |

## Defaults And Assumptions

- Studio becomes a federated control plane with its own backend service.
- Studio backend proxies and normalizes data from registered Relayna services instead of the browser calling each service directly.
- Global task identity defaults to `service_id + task_id`, with optional correlation and lineage joins when services emit compatible metadata.
- Logs are treated as separate from Relayna status and observation payloads and require a pluggable backend.
- Studio user authentication starts with simple username/password authentication in the first implementation phase.
- Service-to-service trust can initially rely on the existing AKS environment and internal network controls instead of a new distributed auth scheme.
- This file is the single source of truth for these 10 features. Do not split it into multiple roadmap files unless this document is explicitly replaced.
- Internal roadmap status must be updated in the same PR that changes covered behavior.
- `implemented` means shipped in repo with tests covering the behavior.
- `partially_implemented` means code exists in repo but the target end state in this file is not complete.

## 1. Service Registry

- Status: implemented
- last_updated: 2026-04-10
- Goal: Give Studio a canonical inventory of Relayna-enabled services across environments.
- Why it exists: Studio cannot act as a control plane until it knows which services exist, where they live, and how to authenticate to them.
- Current state in repo: Relayna now ships a Redis-backed Studio service registry, CRUD router, and minimal Studio backend app in `src/relayna/studio/`. The frontend exposes service list and detail management in `apps/studio/src/App.tsx`. Capability refresh is wired as a dependency-gated `501` placeholder until feature 2 adds `GET /relayna/capabilities`.
- Target end state: Studio owns a persistent service catalog and operators can register, inspect, enable, disable, and refresh Relayna service entries.
- Planned API/interface additions:
  - Service registration model with fields `service_id`, `name`, `base_url`, `environment`, `tags`, `auth_mode`, `status`, `capabilities`, `last_seen_at`
  - Studio backend endpoints for create/list/get/update/delete service records
  - Studio backend endpoint to refresh service capabilities on demand
- Implementation phases:
  - Phase 1: Define service model and persistence schema
  - Phase 2: Add Studio backend registry CRUD endpoints
  - Phase 3: Add UI for service list and service detail
- Dependencies:
  - Capability discovery and version handshake
  - Auth, trust, and operator controls
- Open risks:
  - Duplicate service registration across environments
  - Base URL drift or stale auth configuration
  - Health status becoming stale without refresh policy
- Acceptance criteria:
  - Operators can register a Relayna service with a stable `service_id`
  - Studio can list all registered services with current registry metadata
  - Studio can mark a service unavailable without deleting the record
- Checklist:
  - [x] Define persistent service record schema
  - [x] Define service status lifecycle
  - [x] Add registry CRUD endpoints to Studio backend
  - [x] Add capability refresh endpoint
  - [x] Add service list UI
  - [x] Add service detail UI
  - [x] Add tests for registry CRUD and duplicate handling

## 2. Capability Discovery And Version Handshake

- Status: implemented
- last_updated: 2026-04-09
- Goal: Let Studio discover what each registered service can do before it tries to call service-specific Relayna endpoints.
- Why it exists: Not every service will expose the same Relayna endpoints, and Studio needs a stable compatibility contract.
- Current state in repo: Relayna exposes status, DLQ, workflow, and execution routes, but there is no single discovery endpoint or version handshake. See `src/relayna/api/execution_routes.py` and `src/relayna/api/workflow_routes.py`.
- Target end state: Every Relayna-enabled service can expose a capability document that advertises Relayna version, topology kind, route support, alias behavior, and optional feature flags.
- Planned API/interface additions:
  - `GET /relayna/capabilities`
  - Response fields:
    - `relayna_version`
    - `topology_kind`
    - `alias_config_summary`
    - `supported_routes`
    - `feature_flags`
    - `service_metadata`
- Implementation phases:
  - Phase 1: Define capability response schema
  - Phase 2: Add reusable route factory in `relayna.api`
  - Phase 3: Make Studio registry refresh store the capability document
- Dependencies:
  - Service registry
  - Federated API aggregation layer
- Open risks:
  - Backward compatibility for older services without the endpoint
  - Route support mismatch across Relayna versions
- Acceptance criteria:
  - Studio can detect whether a service supports status, DLQ, workflow, and execution graph routes
  - Studio can reject unsupported actions without trial-and-error HTTP calls
  - Services without capability endpoint degrade predictably
- Checklist:
  - [x] Define capability response schema
  - [x] Add reusable capabilities route
  - [x] Include route support and topology kind in response
  - [x] Include alias config summary in response
  - [x] Add Studio-side fallback behavior for older services
  - [x] Add tests for capability route and backward compatibility

## 3. Federated API Aggregation Layer

- Status: implemented
- last_updated: 2026-04-09
- Goal: Make Studio backend the single read surface for multi-service Relayna operations.
- Why it exists: Browsers should not coordinate direct calls to many services, normalize response shapes, or handle cross-service failures.
- Current state in repo: Studio now exposes a federated backend read surface for registered Relayna services, including service-scoped status/history/workflow/DLQ/execution-graph reads, exact-`task_id` cross-service search, and a composite task detail endpoint. The Studio frontend task inspector now reads via `/studio/tasks/{service_id}/{task_id}` instead of calling service base URLs directly. See `src/relayna/studio/federation.py`, `src/relayna/studio/app.py`, and `apps/studio/src/App.tsx`.
- Target end state: Studio backend exposes a normalized API that proxies and aggregates Relayna reads from registered services.
- Planned API/interface additions:
  - Service-scoped read endpoints:
    - `GET /studio/services/{service_id}/status/{task_id}`
    - `GET /studio/services/{service_id}/history`
    - `GET /studio/services/{service_id}/workflow/topology`
    - `GET /studio/services/{service_id}/dlq/messages`
    - `GET /studio/services/{service_id}/executions/{task_id}/graph`
  - Cross-service read endpoints:
    - `GET /studio/tasks/search`
    - `GET /studio/tasks/{service_id}/{task_id}`
- Implementation phases:
  - Phase 1: Add Studio backend HTTP client and normalized error model
  - Phase 2: Add service-scoped proxy routes
  - Phase 3: Add cross-service aggregation routes
- Dependencies:
  - Service registry
  - Capability discovery and version handshake
  - Cross-service identity model
- Open risks:
  - Partial failure across services
  - Inconsistent latency and auth behavior
  - Alias config mismatch across services
- Acceptance criteria:
  - Studio frontend only talks to Studio backend for control-plane reads
  - Studio backend can proxy Relayna endpoints for any registered service
  - Cross-service search returns normalized response items with `service_id`
- Checklist:
  - [x] Define Studio backend service client abstraction
  - [x] Define normalized error shape
  - [x] Add service-scoped proxy routes
  - [x] Add cross-service task search route
  - [x] Add tests for timeout, 404, and auth failure normalization

## 4. Cross-Service Identity Model

- Status: partially_implemented
- last_updated: 2026-04-08
- Goal: Establish the identity rules Studio uses to connect task status, lineage, retries, and graphs across services.
- Why it exists: Per-service `task_id` alone is not enough for a federated control plane.
- Current state in repo: Studio now exposes additive task identity models and normalized `task_ref` payloads across federated status/history/DLQ/execution-graph/task-search/task-detail responses. Cross-service joins are opt-in on `/studio/tasks/search` and `/studio/tasks/{service_id}/{task_id}` via `join=none|correlation|lineage|all`, with conservative request-time matching, ambiguity warnings, and frontend rendering in `apps/studio/src/App.tsx`.
- Target end state: Studio uses `service_id + task_id` as the default global key and can optionally join tasks by `correlation_id`, `meta.parent_task_id`, and workflow lineage metadata when available.
- Planned API/interface additions:
  - Normalized Studio task reference:
    - `service_id`
    - `task_id`
    - `correlation_id`
    - `parent_refs`
    - `child_refs`
  - Cross-service graph nodes and search results always include `service_id`
- Implementation phases:
  - Phase 1: Define normalized task reference type
  - Phase 2: Thread `service_id` through Studio backend responses
  - Phase 3: Add optional cross-service lineage joins
- Dependencies:
  - Federated API aggregation layer
  - Search and retention
- Open risks:
  - Conflicting or missing `correlation_id` semantics across services
  - Parent-child joins that produce false positives
- Acceptance criteria:
  - Any task returned by Studio can be addressed as `service_id + task_id`
  - Cross-service views never lose the owning service identity
  - Optional lineage joins are explicit and auditable
- Checklist:
  - [x] Define normalized task reference schema
  - [x] Add `service_id` to all Studio backend task-bearing responses
  - [x] Define correlation and lineage join rules
  - [x] Add tests for same-`task_id` collisions across services

## 5. Aggregated Event And Observation Ingestion

- Status: implemented
- last_updated: 2026-04-10
- Goal: Aggregate task movement and Relayna observations across services into Studio.
- Why it exists: Studio cannot show multi-service task movement from ad hoc polling alone.
- Current state in repo: Relayna now ships a merged service event feed via `GET /events/feed`, backed by shared status + observation feed persistence in `src/relayna/observability/`. Studio now exposes Redis-backed ingest/query/SSE routes and a pull-sync worker in `src/relayna/studio/`, and the frontend renders service activity plus task timelines in `apps/studio/src/App.tsx`.
- Target end state: Services either push normalized Relayna observations into Studio or Studio continuously ingests them into a control-plane store for live and historical operator views.
- Planned API/interface additions:
  - Studio ingest contract for normalized Relayna observations
  - Studio storage for service-scoped status and observation events
  - Optional service-side forwarder or pull-based sync worker
- Implementation phases:
  - Phase 1: Define ingestion envelope including `service_id`
  - Phase 2: Add Studio ingest endpoint or sync worker
  - Phase 3: Add live and historical event timeline queries
- Dependencies:
  - Cross-service identity model
  - Search and retention
  - Health and liveness model
- Open risks:
  - Duplicate ingestion
  - Event ordering drift between status and observations
  - Retention costs for high-volume services
- Acceptance criteria:
  - Studio can query task movements without directly reading a service Redis store
  - Ingested observation items retain `service_id`, `task_id`, and event type
  - Duplicate ingestion is safely deduplicated
- Checklist:
  - [x] Define normalized ingest envelope
  - [x] Decide push and pull support strategy
  - [x] Add ingest storage schema
  - [x] Add task timeline query endpoints
  - [x] Add tests for dedupe and out-of-order events

## 6. Log Pipeline

- Status: implemented
- last_updated: 2026-04-11
- Goal: Let Studio expose logs alongside Relayna task and observation views without conflating the two.
- Why it exists: Operators will expect logs in the control plane, but Relayna observations are not a full log backend.
- Current state in repo: Relayna provides `make_logging_sink(...)` and event serialization helpers in `src/relayna/observability/exporters.py`, while Studio now ships a pluggable read-only log query surface with per-service `log_config`, a Loki provider, normalized `/studio/services/{service_id}/logs` and `/studio/tasks/{service_id}/{task_id}/logs` routes, and service/task log panels in `apps/studio/src/App.tsx`.
- Target end state: Studio supports a pluggable log backend contract and can query logs scoped by service and task context.
- Planned API/interface additions:
  - Studio log provider abstraction
  - Normalized log query shape:
    - `service_id`
    - `task_id`
    - `correlation_id`
    - `timestamp`
    - `level`
    - `message`
    - `fields`
  - Service registry field for log backend configuration if needed
- Implementation phases:
  - Phase 1: Define log provider interface and Studio-side query normalization
  - Phase 2: Add first provider integration
  - Phase 3: Add log panels in Studio task and service views
- Dependencies:
  - Service registry
  - Cross-service identity model
  - Auth, trust, and operator controls
- Open risks:
  - Different log backends have incompatible query capabilities
  - Log lines may not carry `task_id` or `correlation_id`
- Acceptance criteria:
  - Studio can show logs for a service even though Relayna itself is not the log store
  - Log queries are clearly separate from Relayna status and observation queries
  - Missing log correlation does not break the rest of the control plane
- Checklist:
  - [x] Define Studio log provider interface
  - [x] Define normalized log response schema
  - [x] Add at least one pluggable provider implementation
  - [x] Add task-scoped and service-scoped log views
  - [x] Add tests for provider errors and missing correlation keys

## 7. Control-Plane UI Expansion

- Status: partially_implemented
- last_updated: 2026-04-08
- Goal: Expand Studio from a single execution-graph page into a full operator console.
- Why it exists: A control plane must show services, topology, DLQ, task search, task detail, live events, and graphs in one consistent UI.
- Current state in repo: Backend presenter helpers exist for execution, run, stage, topology, and DLQ views in `src/relayna/studio/`. The frontend now includes service list and service detail management backed by the Studio registry, while execution-graph inspection remains a manual direct-to-service tool in `apps/studio/src/App.tsx`.
- Target end state: Studio UI has service list, service detail, topology diagrams, task search, task detail, DLQ explorer, live event timeline, execution graph, and operator action surfaces.
- Planned API/interface additions:
  - Frontend routes:
    - `/services`
    - `/services/:serviceId`
    - `/services/:serviceId/topology`
    - `/services/:serviceId/dlq`
    - `/tasks/search`
    - `/tasks/:serviceId/:taskId`
  - Shared UI state model scoped by `service_id`
- Implementation phases:
  - Phase 1: Replace manual base URL input with Studio backend-driven service selection
  - Phase 2: Add service and topology pages
  - Phase 3: Add task detail, DLQ explorer, and live event views
- Dependencies:
  - Service registry
  - Federated API aggregation layer
  - Search and retention
- Open risks:
  - UI complexity expanding faster than backend normalization
  - Live updates requiring more than request/response polling
- Acceptance criteria:
  - Operators can navigate from service to task to graph without entering raw base URLs
  - Topology and execution graph are both available in Studio
  - DLQ and task search are first-class screens, not ad hoc debug forms
- Checklist:
  - [x] Add service list UI
  - [x] Add service detail UI
  - [ ] Add topology visualization page
  - [ ] Add task search UI
  - [ ] Add task detail view with status, timeline, graph, and logs
  - [ ] Add DLQ explorer UI
  - [ ] Add tests for navigation and service-scoped fetching

## 8. Auth, Trust, And Operator Controls

- Status: planned
- last_updated: 2026-04-08
- Goal: Make Studio safe to use as an operator console for read and write actions.
- Why it exists: Registry updates, DLQ replay, and workflow resume are control-plane actions that require trust boundaries and auditability.
- Current state in repo: Relayna exposes DLQ replay helpers and MCP ops helpers, but there is no central auth model, Studio-side RBAC, or audit trail. See `src/relayna/api/replay_routes.py` and `src/relayna/mcp/tools_ops.py`.
- Target end state: Studio has user auth, service auth, RBAC for operator actions, and an audit log for every write operation. The first shipped phase uses simple username/password authentication for Studio users, while service-to-service trust can initially depend on the existing AKS environment and internal network boundaries.
- Planned API/interface additions:
  - Studio auth and session integration with username/password in the first phase
  - Service auth modes in registry:
    - `none`
    - `static_token`
    - `oauth2_client_credentials`
    - `forward_user_token`
  - Audit record for write actions:
    - actor
    - action
    - target service
    - target task or DLQ item
    - timestamp
    - outcome
- Implementation phases:
  - Phase 1: Add simple username/password authentication for Studio users and define read versus write roles
  - Phase 2: Gate service registration and write actions while keeping service-to-service trust simple inside AKS
  - Phase 3: Add audit logging, operator confirmations, and optional stronger service auth modes if AKS assumptions no longer hold
- Dependencies:
  - Service registry
  - Federated API aggregation layer
- Open risks:
  - Username/password auth being too weak once Studio expands beyond the current AKS boundary
  - Services using incompatible auth schemes when service auth modes are introduced later
  - Dangerous replay or resume actions without guardrails
- Acceptance criteria:
  - Studio users must authenticate with username/password in the first shipped phase
  - Operator write actions require explicit auth and are auditable
  - Service credentials are not exposed to the browser when service auth modes are added
  - Studio can distinguish read-only users from operators
- Checklist:
  - [ ] Define username/password authentication flow for Studio users
  - [ ] Add Studio backend auth enforcement
  - [ ] Add RBAC for read versus write actions
  - [ ] Document AKS trust assumptions for initial service-to-service communication
  - [ ] Define service auth modes for the post-initial phase
  - [ ] Add audit log storage
  - [ ] Add confirmation UX for destructive or replay actions
  - [ ] Add tests for unauthorized and forbidden control-plane actions

## 9. Health And Liveness Model

- Status: partially_implemented
- last_updated: 2026-04-08
- Goal: Give Studio a reliable model for service health, runtime freshness, and control-plane reachability.
- Why it exists: Operators need to distinguish “service down”, “worker unhealthy”, “relayna route unavailable”, and “data stale”.
- Current state in repo: Relayna has minimal health-related primitives such as `WorkerHeartbeat`, stage health snapshots, and alert helpers. See `src/relayna/consumer/lifecycle.py`, `src/relayna/observability/stage_metrics.py`, and `src/relayna/observability/alerts.py`. There is no distributed liveness protocol.
- Target end state: Studio tracks service reachability, last capability refresh, observation freshness, and optional worker heartbeat information in a unified health model.
- Planned API/interface additions:
  - Studio service health document:
    - `registry_status`
    - `http_status`
    - `capability_status`
    - `observation_freshness`
    - `worker_health`
    - `last_checked_at`
  - Optional Relayna heartbeat endpoint or ingest shape
- Implementation phases:
  - Phase 1: Define Studio service health model
  - Phase 2: Add scheduled health refresh worker
  - Phase 3: Add optional service-side heartbeat reporting
- Dependencies:
  - Service registry
  - Capability discovery and version handshake
  - Aggregated event and observation ingestion
- Open risks:
  - False positives during deploys or transient network failures
  - Heartbeats without stable ownership semantics
- Acceptance criteria:
  - Studio shows clear distinction between unreachable service and stale data
  - Service detail page includes last successful check times
  - Worker heartbeat support remains optional and does not block control-plane adoption
- Checklist:
  - [ ] Define service health model
  - [ ] Add scheduled health check job
  - [ ] Add capability freshness tracking
  - [ ] Add observation freshness tracking
  - [ ] Define optional worker heartbeat contract
  - [ ] Add tests for stale, unreachable, and degraded states

## 10. Search And Retention

- Status: partially_implemented
- last_updated: 2026-04-08
- Goal: Let Studio search and retain control-plane data across services over useful time windows.
- Why it exists: A control plane needs historical lookup, not only live proxy reads.
- Current state in repo: Relayna already persists bounded local status history and bounded local observation history with TTLs. See `src/relayna/status/store.py` and `src/relayna/observability/store.py`. This is service-local and not searchable across services.
- Target end state: Studio owns searchable indexes and retention policies for service registry data, normalized task metadata, observations, and optional cached control-plane views.
- Planned API/interface additions:
  - Studio search endpoints:
    - `GET /studio/tasks/search`
    - `GET /studio/services/search`
  - Search fields:
    - `service_id`
    - `task_id`
    - `correlation_id`
    - `status`
    - `stage`
    - `time_range`
  - Retention settings for ingested control-plane data
- Implementation phases:
  - Phase 1: Define search document shape and indexed fields
  - Phase 2: Add Studio persistence for normalized task metadata
  - Phase 3: Add retention and pruning jobs
- Dependencies:
  - Cross-service identity model
  - Aggregated event and observation ingestion
  - Federated API aggregation layer
- Open risks:
  - Search results lagging behind live service state
  - High cardinality metadata increasing storage cost
- Acceptance criteria:
  - Operators can find tasks across services without knowing the raw service URL
  - Retention policies are explicit and configurable
  - Search continues to work when source services have already evicted local history
- Checklist:
  - [ ] Define indexed search document
  - [ ] Add Studio-side task metadata persistence
  - [ ] Add service and task search endpoints
  - [ ] Add retention policy configuration
  - [ ] Add pruning jobs
  - [ ] Add tests for search filters and retention expiry

## Update Policy

- Any PR that adds, changes, or completes work for one of the 10 roadmap features must update:
  - the `Status Summary` table
  - the relevant feature section
  - the `Change Log` section
- If a feature’s scope changes, update its `Target end state` and `Acceptance criteria` in the same PR.
- Use only these statuses:
  - `proposed`
  - `planned`
  - `in_progress`
  - `partially_implemented`
  - `implemented`
  - `deferred`
- Public docs under `docs/` remain separate. This file is internal planning and review material only.
- PR review guidance:
  - Changes that materially affect one of these 10 features should be rejected if this roadmap was not updated in the same PR.

## Change Log

- 2026-04-08: Created the internal Studio control-plane roadmap as the single source of truth for the 10 control-plane features and their tracking statuses.
- 2026-04-08: Refined the auth roadmap to start with simple username/password authentication for Studio users and rely on existing AKS trust boundaries for service-to-service communication in the initial phase.
- 2026-04-08: Shipped the first service-registry slice with Redis-backed service records, Studio backend CRUD routes, a dependency-gated capability refresh placeholder, and Studio service list/detail UI.
- 2026-04-09: Shipped feature 2 with `GET /relayna/capabilities`, typed capability documents, Studio-backed capability refresh storage, and deterministic legacy fallback handling for older services.
- 2026-04-10: Shipped feature 4 with normalized Studio task references, additive `task_ref` identity metadata across federated task-bearing responses, opt-in cross-service joins for correlation and lineage, ambiguity warnings, and Studio UI panels for identity context.
- 2026-04-10: Shipped feature 5 with service-side merged event feeds, Studio ingest/query/SSE routes, Redis-backed control-plane event storage, pull-sync support for `events.feed`, and Studio UI panels for service activity and task timelines.
