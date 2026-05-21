# Runtime Backpressure Signals

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should expose clear runtime pressure signals so operators and runtime
policies can see when queues, workers, dependencies, retry paths, or DLQs are
under strain. Backpressure is not one metric. It is a set of signals that tell
the runtime whether to accept more work, slow retries, shift capacity, alert
operators, or protect downstream systems.

After this feature, Relayna can report pressure at task, queue, workflow stage,
service, and dependency levels in a way that Studio, metrics backends, and
runtime policies can consume.

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [x] (2026-05-21) Confirmed freeze boundary and implementation strategy before
  code changes.
- [x] (2026-05-21) Inventoried current metrics, observations, health, DLQ,
  task lease, and RabbitMQ inspection surfaces.
- [x] (2026-05-21) Defined pressure signal model and severity thresholds.
- [x] (2026-05-21) Implemented signal collection, API exposure, tests, and
  Studio federation.

## Surprises & Discoveries

- Observation: Relayna already has metrics and observability packages that can
  host pressure signals.
  Evidence: `src/relayna/metrics.py` and `src/relayna/observability/`.
- Observation: Studio planning already expects Prometheus metrics and exact
  runtime observations.
  Evidence: `internal/studio-observability-log-metrics-plan.md`.
- Observation: The first task lease implementation already adds worker health
  lease summaries that can feed pressure signals.
  Evidence: `src/relayna/api/health_routes.py` includes active lease summaries,
  and `studio/backend/src/relayna_studio/health.py` treats expired active leases
  as stale worker health.

## Decision Log

- Decision: Treat backpressure as structured runtime state, not only as
  Prometheus metrics.
  Rationale: Prometheus is useful for dashboards and alerts, but adaptive retry,
  policy evaluation, and Studio need current structured decisions with reasons.
  Date/Author: 2026-05-20 / Codex.
- Decision: Start with read-only signals before automatic throttling.
  Rationale: Observability-first delivery reduces risk during the production
  freeze and gives operators a baseline before policies act on the signals.
  Date/Author: 2026-05-20 / Codex.
- Decision: Add SDK and Studio read-only routes in the first implementation.
  Rationale: Operators need an immediately consumable structured surface, and
  the route/federation additions are additive under the approved freeze
  perimeter.
  Date/Author: 2026-05-21 / Codex.

## Outcomes & Retrospective

Implemented read-only runtime backpressure signals. Relayna now has structured
pressure signal models, collectors for queues, workers, leases, and DLQs, a
runtime backpressure route/capability, low-cardinality Prometheus pressure
metrics, and Studio federation for service-level backpressure snapshots.
Runtime enforcement remains out of scope; no throttling or retry behavior
changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/metrics.py` provides metric recording hooks.
- `src/relayna/observability/stage_metrics.py` and
  `src/relayna/observability/collectors.py` provide runtime observation
  surfaces.
- `src/relayna/rabbitmq/client.py` and `src/relayna/rabbitmq/declarations.py`
  know queue, exchange, and routing topology.
- `src/relayna/dlq/service.py` summarizes dead-letter queue state.
- `src/relayna/api/health_routes.py` and `src/relayna/api/events_routes.py`
  expose runtime status to services and Studio.

Relevant Studio areas:

- `studio/backend/src/relayna_studio/health.py`
- `studio/backend/src/relayna_studio/federation.py`
- `studio/backend/src/relayna_studio/metrics.py`

Definitions:

- Pressure signal: a structured measurement with severity, scope, timestamp,
  reason, and optional remediation hint.
- Saturation: workers are near or over useful capacity.
- Consumer lag: queued or unacknowledged work is growing faster than workers
  complete it.
- Retry storm: retry traffic dominates useful work.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. Backpressure signals are
primarily additive, but route response shapes, status metadata, metric names,
and policy behavior are compatibility-sensitive. Use `$production-freeze-guard`
and `$implementation-strategy` before editing code.

Default compatibility posture: expose additive optional fields and new routes
without altering existing health semantics. Do not use signals to throttle work
until a separate compatibility decision approves enforcement.

## Plan of Work

Define a `RuntimePressureSignal` model. Fields should include `scope`,
`scope_id`, `kind`, `severity`, `value`, optional `threshold`, `observed_at`,
`reason`, and `recommended_action`.

Collect initial signals from sources that already exist or are easy to add:

- RabbitMQ queue depth and unacknowledged counts where management inspection is
  configured.
- Worker heartbeat freshness and worker counts.
- In-flight leases once task leases exist.
- Retry attempt rates and scheduled retry counts.
- DLQ growth and replay conflicts.
- Redis and RabbitMQ dependency health.
- Status stream lag and event persistence failures.

Expose signals through SDK health or observability routes and Studio
federation. Publish Prometheus metrics with low-cardinality labels such as
`service`, `scope`, `kind`, and `severity`.

Keep policy consumption optional. Adaptive retry and runtime policy engine can
depend on this model after it is stable.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "metrics|health|DLQQueueSummary|queue_depth|worker_health|Observation" src/relayna studio/backend/src tests studio/backend/tests
    make test
    make -C studio/backend test

Full implementation verification:

    make format
    make lint
    make typecheck
    make test
    make -C studio/backend format
    make -C studio/backend lint
    make -C studio/backend typecheck
    make -C studio/backend test

## Validation and Acceptance

Acceptance criteria:

- Runtime pressure signals can be generated without configured enforcement.
- Signals include stable scope, kind, severity, value, and timestamp fields.
- Missing optional signal sources degrade cleanly.
- Prometheus metrics avoid high-cardinality labels such as `task_id`.
- Studio can display service-level and queue-level pressure without breaking
  existing health views.
- Adaptive retry can consume pressure signals through a testable interface.

Expected tests:

- Unit tests for signal severity calculation and threshold handling.
- Collector tests with missing RabbitMQ or Redis signal sources.
- API tests for additive signal response shapes.
- Studio backend tests for federation and rendering model changes.

## Idempotence and Recovery

Signal collection should be read-only and safe to run repeatedly. Failed
collectors should return degraded signals or warnings instead of failing the
entire health response. Enforcement should remain separate so read-only signal
collection cannot accidentally throttle runtime work.

## Artifacts and Notes

Candidate signal kinds:

    queue_depth_high
    consumer_lag_high
    unacked_high
    retry_rate_high
    dlq_growth_high
    worker_saturation_high
    dependency_unhealthy
    lease_expiry_rate_high

Candidate severities:

    normal
    warning
    critical
    unknown

## Interfaces and Dependencies

Potential SDK interfaces:

- `RuntimePressureSignal`
- `RuntimePressureSnapshot`
- `PressureSignalCollector`
- `PressureSeverity`

Potential route:

- `GET /runtime/backpressure`
