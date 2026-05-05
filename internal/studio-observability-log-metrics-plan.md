# Relayna Studio Observability Plan

This internal-only file is the source of truth for the Relayna Studio logs,
metrics, runtime resource, and trace-correlation feature plan. It is
intentionally kept outside `docs/` and must not be linked from `mkdocs.yml` or
treated as public product documentation.

## Summary

The first production observability stack for Relayna Studio is:

```text
Relayna API pods / long-running worker pods
  -> stdout JSON logs + /metrics + OpenTelemetry spans
  -> Grafana Alloy
  -> Loki for logs
  -> Prometheus for metrics
  -> Tempo for traces
  -> Relayna Studio as the task-aware query and visualization layer
```

Relayna Studio must not ingest every log line or trace span. Studio should
query Loki, Prometheus, Tempo, and Relayna APIs, then merge those results into
service and task views. Relayna Redis remains the source of truth for task
lifecycle, status history, DLQ records, observations, and execution graphs.

Grafana may remain available for generic observability, but Relayna Studio is
the product-specific task and runtime UI.

## Key Decisions

- Use Alloy + Loki + Prometheus + Tempo + Relayna Studio as the first
  production stack.
- Use Option A, the safer default for Loki: keep `task_id` inside the JSON log
  body, not as a Loki label.
- Treat Case 2, many tasks per long-running worker pod, as the real usage model.
- For long-running worker pods, Kubernetes CPU/memory is pod/container-level
  only. Studio may show pod metrics over the task time window, but exact
  per-task CPU/memory requires Relayna runtime instrumentation.
- Keep task lifecycle, status history, DLQ, observations, and execution graph
  data in Relayna/Redis. Do not replace those stores with Loki.
- Keep tracing optional and no-op unless application code configures an
  OpenTelemetry SDK/exporter. Relayna core depends only on `opentelemetry-api`.

## Four-Phase Scope

| Phase | Scope | Backend | Studio surface |
| --- | --- | --- | --- |
| 1 | Centralized logs | Alloy + Loki | Service logs, task logs, level/source/text/window filters, JSON rendering. |
| 2 | Kubernetes infrastructure metrics | Prometheus + kube-state-metrics + cAdvisor | Service and task-window CPU, memory, restarts, OOM, readiness, pod phase, and network metrics. |
| 3 | Relayna runtime metrics and exact resource observations | Relayna `/metrics` + Redis observations | Aggregate task/queue/status/observation charts and exact per-task CPU/RSS samples. |
| 4 | Trace correlation | OpenTelemetry propagation + Tempo | Trace discovery, Studio span details, log filtering by trace ID, and task-to-span correlation. |

## Interfaces And Data Model

Relayna worker and API pods should emit `structlog` JSON logs to stdout.
Studio depends on the emitted field contract, not on a separate Relayna log
ingestion path. Minimum fields:

```text
service
app
task_id
correlation_id
stage
attempt
event
level
```

Extended fields:

```text
env
cluster
namespace
pod
container
workflow_id
worker_id
runtime
request_id
message
timestamp
```

Loki labels should stay low-cardinality:

```text
cluster
namespace
service
app
container
level
stage
```

These fields must not become default Loki labels:

```text
task_id
correlation_id
request_id
pod
worker_id
```

Studio service observability config should cover logs, metrics, and traces:

- Logs:
  - `backend`: `loki`
  - Loki base URL
  - optional tenant ID
  - service selector labels
  - source/app label
  - `task_match_mode`: `contains`
  - `task_match_template`: `{task_id}`
- Metrics:
  - `backend`: `prometheus`
  - metrics backend URL
  - namespace selector
  - service label
  - pod label
- Traces:
  - `backend`: `tempo`
  - Tempo base URL
  - optional public/browser URL
  - optional tenant ID
  - trace query path template

Studio endpoints should include or evolve toward:

```text
GET /studio/services/{service_id}/logs
GET /studio/tasks/{service_id}/{task_id}/logs
GET /studio/services/{service_id}/metrics
GET /studio/tasks/{service_id}/{task_id}/metrics
GET /studio/tasks/{service_id}/{task_id}/traces
```

Relayna API pods, Relayna worker pods, and the Studio backend should expose
`/metrics`.

## Phase 1: Centralized Logs

Goal: provide the fastest useful log visibility in Studio without changing
Relayna into a log ingestion system.

Implementation:

- Deploy Alloy + Loki in the observability namespace.
- Configure Alloy to collect `/var/log/containers/*.log`, parse CRI logs,
  attach Kubernetes metadata, parse JSON log bodies, drop noisy labels, and send
  to Loki.
- Collect Relayna worker pod logs, Relayna API pod logs, Studio backend logs,
  Kubernetes events, and optionally Redis/RabbitMQ logs.
- Configure Studio `log_config` so service logs query by Loki service selector.
- Configure task logs to use body matching, for example:

```logql
{service="document-worker"} |= "task-123"
```

- Studio displays service logs, task logs, time-window filtering, level
  filtering, and source/app filtering.

Producer contract:

- Registered services should keep using `structlog` and render JSON to stdout.
- Use snake_case field names consistently across API, worker, aggregation, and
  Studio backend logs.
- Required fields on every log event:

```text
service
app
event
level
timestamp
```

- Required fields when task context exists:

```text
task_id
correlation_id
stage
attempt
```

- Optional fields:

```text
env
cluster
namespace
workflow_id
worker_id
runtime
request_id
message
pod
container
```

Relayna provides lightweight `structlog`-compatible helpers in
`relayna.observability`:

- `bind_studio_log_context(...)` binds stable `service`, `app`, `env`, and
  `runtime` fields to a structlog-style logger.
- `observation_to_studio_log_fields(...)` converts Relayna observation objects
  into the Studio log field contract.
- `make_structlog_observation_sink(...)` emits Relayna observations through a
  structlog-style logger without replacing the service logging stack.
- `validate_studio_log_fields(...)` returns missing required contract fields for
  tests and fixtures.

Example API pod setup:

```python
import structlog
from relayna.observability import bind_studio_log_context

logger = bind_studio_log_context(
    structlog.get_logger(),
    service="document-service",
    app="document-api",
    env="prod",
    runtime="api",
)

logger.info(
    "request_received",
    request_id="req-123",
    correlation_id="corr-123",
)
```

Example worker observation sink:

```python
import structlog
from relayna.observability import bind_studio_log_context, make_structlog_observation_sink

worker_logger = bind_studio_log_context(
    structlog.get_logger(),
    service="document-service",
    app="document-worker",
    env="prod",
    runtime="worker",
)
observation_sink = make_structlog_observation_sink(
    worker_logger,
    service="document-service",
    app="document-worker",
    stage="parse",
)
```

Example aggregation worker setup:

```python
aggregation_logger = bind_studio_log_context(
    structlog.get_logger(),
    service="document-service",
    app="document-aggregation-worker",
    env="prod",
    runtime="aggregation",
)
```

Example Studio backend setup:

```python
studio_logger = bind_studio_log_context(
    structlog.get_logger(),
    service="relayna-studio",
    app="studio-backend",
    env="prod",
    runtime="studio-backend",
)
```

Alloy pipeline expectation:

1. Collect Kubernetes pod stdout from `/var/log/containers/*.log`.
2. Parse CRI/container log framing.
3. Attach Kubernetes metadata.
4. Parse the JSON body emitted by `structlog`.
5. Promote only low-cardinality labels.
6. Write to Loki.

Default Loki label allowlist:

```text
cluster
namespace
service
app
container
level
stage
```

High-cardinality fields that must remain in the JSON log body by default:

```text
task_id
correlation_id
request_id
pod
worker_id
```

Tasks:

- Define the required Relayna `structlog` JSON fields for API, worker,
  aggregation, and Studio backend emitters.
- Add or update runtime logging helpers so task lifecycle logs consistently
  include `task_id`, `correlation_id`, `stage`, `attempt`, `event`, `service`,
  `app`, and `level`.
- Document the expected Alloy log pipeline: Kubernetes log source, CRI parsing,
  Kubernetes metadata enrichment, JSON parsing, label selection, and Loki write.
- Define the Loki label allowlist and explicitly keep high-cardinality fields in
  the log body.
- Confirm Studio service registration can express the required Loki
  `log_config` for service selector labels, source/app filtering, and
  `contains` task matching.
- Verify the existing Studio Loki provider returns normalized log entries for
  service and task views with source, level, timestamp, message, and structured
  fields.
- Add or update frontend states for log loading, empty results, backend errors,
  source filtering, level filtering, and manual task time-window overrides.
- Add example local or AKS registration payloads for a Relayna service using
  Option A task matching.
- Add fixtures or tests proving representative `structlog` output contains the
  required field names.

Acceptance criteria:

- Studio service pages show logs for API and worker sources belonging to the
  registered service.
- Studio task pages show task-related logs through `task_id` body matching.
- Missing logs do not break task status, timeline, DLQ, or execution graph
  views.

Checklist:

- [x] Define Relayna `structlog` JSON log field contract.
- [x] Standardize API pod log fields.
- [x] Standardize worker pod log fields.
- [x] Standardize Studio backend log fields.
- [x] Define Alloy log collection pipeline.
- [x] Define Loki low-cardinality label allowlist.
- [x] Confirm `task_id` remains in the log body by default.
- [x] Configure Studio `log_config` examples for Loki.
- [x] Verify service-scoped log queries.
- [x] Verify task-scoped `contains` log queries.
- [x] Add source/app and level filtering coverage.
- [x] Add empty/error/loading UI coverage for logs.
- [x] Add backend tests for Loki provider errors and unsafe URLs.
- [x] Add frontend tests for service and task log views.
- [x] Add representative `structlog` contract tests.

## Phase 2: Kubernetes Metrics

Goal: add pod/container infrastructure metrics around Relayna service and task
windows.

Implementation:

- Add Prometheus plus Alloy metrics collection.
- Collect kubelet, cAdvisor, and Kubernetes API metrics.
- Include pod CPU, memory, limits/requests, restart count, OOMKilled events,
  pod phase, container readiness, and network I/O.
- Studio service views show API and worker pod CPU/memory, restarts, OOM
  signals, and pod state.
- Studio task views derive task lifecycle windows from Relayna Redis and show
  pod/container metrics around that window.
- The UI must clearly treat task-window pod metrics as approximate for
  long-running workers that process many tasks.

Example Studio service registration observability config:

```json
{
  "service_id": "document-service",
  "name": "Document Service",
  "base_url": "https://document-service.prod.svc.cluster.local",
  "environment": "prod",
  "tags": ["documents"],
  "auth_mode": "internal_network",
  "log_config": {
    "provider": "loki",
    "base_url": "https://loki.observability.svc.cluster.local",
    "service_selector_labels": {
      "service": "document-service",
      "namespace": "prod"
    },
    "source_label": "app",
    "level_label": "level",
    "task_match_mode": "contains",
    "task_match_template": "{task_id}"
  },
  "metrics_config": {
    "provider": "prometheus",
    "base_url": "https://prometheus.observability.svc.cluster.local",
    "namespace": "prod",
    "service_selector_labels": {
      "service": "document-service"
    },
    "runtime_service_label_value": "relayna",
    "namespace_label": "namespace",
    "pod_label": "pod",
    "container_label": "container",
    "step_seconds": 30,
    "task_window_padding_seconds": 120
  }
}
```

Tasks:

- Define the Studio metrics provider interface separately from the existing log
  provider interface.
- Define the service-level metrics query contract for CPU, memory, restart
  count, OOMKilled events, pod phase, readiness, and network I/O.
- Define the task-window metrics query contract using task lifecycle start/end
  timestamps from Relayna status or observations.
- Add a Prometheus-compatible provider that builds bounded PromQL queries from
  service metrics config and rejects unsupported high-cardinality task labels.
- Extend Studio service records or registry metadata with metrics backend
  configuration without breaking existing `log_config` behavior.
- Reuse the Studio backend egress allowlist model for Prometheus base
  URLs.
- Add backend normalization for metric series, units, query windows, warnings,
  partial results, and provider errors.
- Add service metrics UI panels for pod CPU, memory, restarts, OOMKilled
  events, phase, readiness, and network I/O.
- Add task metrics UI panels that clearly label pod-level task-window metrics as
  approximate for long-running workers.
- Add fixtures for overlapping tasks in the same worker pod to verify the UI
  does not imply exact per-task CPU/memory.

Acceptance criteria:

- Studio can show API pod and worker pod CPU/memory for a registered service.
- Studio can show restart, OOMKilled, pod phase, and readiness signals near a
  task lifecycle window.
- Operators can distinguish exact Relayna task state from approximate
  task-window pod metrics.

Checklist:

- [x] Define Studio metrics provider interface.
- [x] Define service metrics response schema.
- [x] Define task-window metrics response schema.
- [x] Add metrics config to service registry model.
- [x] Add Prometheus backend URL allowlist validation.
- [x] Implement Prometheus-compatible query provider.
- [x] Reject `task_id` as a metrics label in default config.
- [x] Normalize metric series and units.
- [x] Add service metrics backend routes.
- [x] Add task metrics backend routes.
- [x] Add service metrics UI panels.
- [x] Add task-window metrics UI panels.
- [x] Add approximation messaging for long-running workers.
- [x] Add backend tests for query generation and provider errors.
- [x] Add frontend tests for service and task metrics states.

## Phase 3: Relayna Task Metrics

Goal: expose aggregate Relayna runtime metrics and exact task-level resource
samples where pod-level metrics are not enough.

Instrument Relayna SDK/runtime with first-class Prometheus metrics:

```text
relayna_tasks_started_total
relayna_tasks_completed_total
relayna_tasks_failed_total
relayna_tasks_retried_total
relayna_tasks_dlq_total
relayna_task_duration_seconds
relayna_task_attempts
relayna_worker_active_tasks
relayna_worker_heartbeat_timestamp
relayna_queue_publish_total
relayna_status_events_published_total
relayna_observation_events_total
```

Use low-cardinality metric labels:

```text
service
stage
queue
status
worker_type
```

Never use `task_id` as a metric label.

For exact per-task resource data in long-running worker pods, add Relayna
runtime observations such as:

```text
task_start_cpu_process_seconds
task_end_cpu_process_seconds
task_start_memory_rss
task_end_memory_rss
```

Studio should add views for throughput, failure rate, retry rate, p95 task
duration, active tasks, worker saturation, queue lag, and DLQ trends.

Tasks:

- Choose the Relayna metrics implementation strategy and dependency boundary so
  SDK users can expose Prometheus metrics without forcing Studio dependencies.
- Add metric instruments for task starts, completions, failures, retries, DLQ
  movement, task duration, attempts, active tasks, worker heartbeat, queue
  publishes, status events, and observation events.
- Define low-cardinality label values for service, stage, queue, status, and
  worker type.
- Add guardrails and tests that prevent `task_id`, `correlation_id`, pod name,
  request ID, and worker ID from being metric labels.
- Expose `/metrics` from Relayna API integrations, worker runtimes, and Studio
  backend where applicable.
- Add runtime hooks to record active tasks and heartbeat state for long-running
  worker pods.
- Add optional process-level task resource observations for CPU and RSS sampled
  around task execution.
- Ensure exact task-level resource samples are stored as Relayna observations,
  not Prometheus labels.
- Add Studio aggregate runtime charts for throughput, failures, retries, DLQ,
  p95 duration, active tasks, saturation, and queue lag.
- Add Studio task detail rendering for exact per-task runtime samples when
  observations are present, with fallback to phase 2 pod-window metrics.

Acceptance criteria:

- Prometheus can scrape Relayna API pods, worker pods, and Studio backend
  `/metrics` endpoints.
- Studio can render aggregate task/runtime charts without high-cardinality
  labels.
- Exact per-task resource samples come from Relayna observations, not from
  Prometheus labels.

Checklist:

- [x] Select Relayna metrics dependency and export strategy.
- [x] Add task lifecycle counters.
- [x] Add task duration histogram.
- [x] Add task attempt metric.
- [x] Add active task gauge.
- [x] Add worker heartbeat metric.
- [x] Add queue publish metric.
- [x] Add status event metric.
- [x] Add observation event metric.
- [x] Add low-cardinality label definitions.
- [x] Add high-cardinality label guardrails.
- [x] Expose `/metrics` from API runtime integration.
- [x] Expose `/metrics` from worker runtime integration.
- [x] Expose `/metrics` from Studio backend.
- [x] Add task CPU process observation samples.
- [x] Add task RSS memory observation samples.
- [x] Add Studio aggregate runtime charts.
- [x] Add Studio exact task resource sample display.
- [x] Add SDK/runtime tests for metrics emission.
- [x] Add Studio tests for aggregate charts and sample fallback behavior.

## Phase 4: Trace Correlation

Goal: connect distributed Relayna task execution across API publish, queueing,
worker consumption, handler work, external calls, and result publication.

Implementation:

- Add OpenTelemetry tracing after logs and metrics are stable.
- Include these identifiers in logs and observations:

```text
trace_id
span_id
task_id
workflow_id
correlation_id
```

- Studio links API publish, queue wait, worker consume, handler execution,
  external API calls, result publish, logs, metrics, and execution graph.

Tasks:

- Define OpenTelemetry trace propagation points across API publish, RabbitMQ
  message headers/properties, worker consume, handler execution, status publish,
  and result publish.
- Define how `trace_id`, `span_id`, `task_id`, `workflow_id`, and
  `correlation_id` are attached to logs, observations, and task references.
- Add optional tracing hooks that are disabled or no-op unless tracing is
  configured by the downstream service.
- Define the trace backend contract for Studio, including Tempo or another
  OpenTelemetry-compatible trace query provider.
- Add Studio backend trace lookup by task context while keeping traces optional.
- Add Studio UI links between task timeline entries, logs, metrics,
  execution-graph nodes, and trace spans.
- Add fallback behavior for services without tracing enabled.
- Document the rollout order so tracing cannot block centralized logs,
  Kubernetes metrics, or Relayna task metrics.

Acceptance criteria:

- Tracing remains optional for the first production release and does not block
  phases 1-3.
- When traces are available, Studio can navigate from a task to related logs,
  metrics, lifecycle events, and trace spans.

Checklist:

- [x] Define trace propagation model.
- [x] Add trace context to API publish path.
- [x] Add trace context to broker message metadata.
- [x] Add trace context to worker consume path.
- [x] Add trace context to handler execution spans.
- [x] Add trace context to status/result publish spans.
- [x] Add trace identifiers to structured logs.
- [x] Add trace identifiers to Relayna observations.
- [x] Add no-op behavior when tracing is not configured.
- [x] Define Studio trace provider contract.
- [x] Add optional trace backend config.
- [x] Add task-to-trace backend lookup.
- [x] Add frontend links from task views to trace spans.
- [x] Add tests for trace-disabled behavior.
- [x] Add tests for trace-context propagation.
- [x] Add tests for Studio trace lookup and fallback UI.

## Test Plan

- Verify Alloy/Loki ingestion with sample Relayna API and worker JSON logs.
- Unit-test Studio Loki query generation for Option A body matching.
- Unit-test Prometheus query generation for service-level and task-window pod
  metrics.
- Add validation tests that reject `task_id` as a default metric label.
- Add backend tests for missing log/metrics config, backend failures, unsafe
  backend URLs, and empty result handling.
- Add frontend tests for service logs, task logs, service metrics, task-window
  metrics, and long-running-worker approximation messaging.
- Add integration fixtures for one service, one long-running worker pod,
  multiple task windows, and concurrent task overlap.

## Assumptions

- Target deployment is AKS.
- Worker pods are long-running and may process many tasks, including overlapping
  tasks.
- Existing Studio Loki log support remains the base for phase 1.
- Existing Relayna Redis/status/observation/DLQ/execution graph behavior remains
  authoritative for task lifecycle.
- This file is internal-only and must not be linked from public docs or
  `mkdocs.yml`.
