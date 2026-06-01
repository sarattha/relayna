# Service Pod Log Panel

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Operators need a service-detail panel in Relayna Studio that continuously shows
the current Kubernetes pods for a registered service and lets them select one or
more pods to inspect all logs emitted by those pods. The panel should make it
easy to move from a logical service such as `service-api` to its API, worker,
aggregator, and other related pods without relying on task ids.

## Progress

- [x] (2026-06-01 08:12Z) Created goal and read the Relayna production freeze,
  implementation strategy, and ExecPlan instructions.
- [x] (2026-06-01 08:18Z) Inspected existing Studio service logs, metrics,
  service-detail UI, app wiring, and production freeze tests.
- [x] (2026-06-01 08:20Z) Add backend current-pod discovery through Prometheus using existing
  `metrics_config` service ownership selectors.
- [x] (2026-06-01 08:20Z) Add optional pod filtering to existing Loki service log queries.
- [x] (2026-06-01 08:20Z) Add the service-detail pod log panel with auto-refreshing pod list and
  pod selection.
- [x] (2026-06-01 08:20Z) Add backend and frontend tests for pod discovery, pod-filtered logs, and
  service-page UI behavior.
- [x] (2026-06-01 08:20Z) Focused tests passed:
  `uv run pytest tests/test_studio_logs.py tests/test_studio_metrics.py tests/test_production_freeze_routes.py`
  from `studio/backend`, and `npm test -- App.test.tsx` from `apps/studio`.
- [x] (2026-06-01 08:23Z) Run required verification for touched Studio backend and frontend areas.
  `$code-change-verification`, `make -C apps/studio test`, and
  `make -C apps/studio build` all passed.

## Surprises & Discoveries

- Observation: Studio already has Prometheus ownership selectors that map a
  registered service to Kubernetes pods through `metrics_config`.
  Evidence: `studio/backend/src/relayna_studio/metrics.py` contains
  `_pod_ownership_selector`.
- Observation: Existing Loki log entries preserve raw stream labels in
  `StudioLogEntry.fields.labels`, but the service log route does not currently
  accept a pod filter.
  Evidence: `studio/backend/src/relayna_studio/logs.py` normalizes `labels` into
  each log entry.
- Observation: The frontend freeze test checks exported symbol names, not
  TypeScript type field shapes, so keeping new frontend helpers local to
  `ServiceDetailPage.tsx` avoids expanding frontend exports.
  Evidence: `apps/studio/src/production-freeze.test.tsx`.
- Observation: Exact pod log filtering requires a Loki `pod` stream label, while
  the existing docs recommend omitting `pod` by default to control cardinality.
  Evidence: `docs/aks-observability.md`.

## Decision Log

- Decision: Use latest release tag `v1.4.17` as the compatibility boundary while
  respecting the repo's strict `v1.4.11` production freeze manifests.
  Rationale: The implementation-strategy skill says to judge compatibility
  against the latest release tag, while production-freeze-guard requires
  intentional manifest handling for route additions.
  Date/Author: 2026-06-01 / Codex.
- Decision: Add a narrow additive route under the existing metrics router for
  current service pods instead of deriving pods from recent logs.
  Rationale: Recent logs cannot reliably show quiet-but-running pods; Prometheus
  plus kube-state-metrics is the existing current-pod source.
  Date/Author: 2026-06-01 / Codex.
- Decision: Add an optional `pod` query parameter to service/task log queries
  that filters the Loki `pod` label when configured in Alloy.
  Rationale: This keeps existing log routes and response shapes stable while
  enabling pod-specific logs. Operators must keep a low-cardinality pod label in
  Loki if they want exact pod filtering.
  Date/Author: 2026-06-01 / Codex.

## Outcomes & Retrospective

Implementation is complete. Full SDK and Studio backend verification passed
through `$code-change-verification`, and Studio frontend test/build targets
passed. The remaining operational caveat is that pod-specific Loki filtering
requires an intentional `pod` stream label in Alloy/Loki.

## Context and Orientation

Relayna Studio is the React and Python control plane for Relayna services.
The Studio backend lives under `/Users/jobz/Works/relayna/studio/backend/`.
The Studio frontend lives under `/Users/jobz/Works/relayna/apps/studio/`.

Current service logs are rendered in
`/Users/jobz/Works/relayna/apps/studio/src/pages/ServiceDetailPage.tsx`.
They call `fetchServiceLogs` from
`/Users/jobz/Works/relayna/apps/studio/src/api.ts`, which queries the backend
route `GET /studio/services/{service_id}/logs` in
`/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/logs.py`.

Current service metrics are backed by Prometheus in
`/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/metrics.py`.
That module already has the service-to-pod ownership logic used for Kubernetes
metric panels.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.17`; additive Studio backend
route and additive log query parameter only. Existing service log and metrics
routes keep their paths, request parameters, and response fields. The backend
route freeze manifest will be updated intentionally because the requested UI
needs a new current-pod read surface.

## Plan of Work

Add private pod response models and a provider/query-service method in
`studio/backend/src/relayna_studio/metrics.py`. Use Prometheus instant query
`kube_pod_status_phase` joined with the existing pod ownership selector to
return current pods and phases for the registered service.

Extend `StudioLogQuery` and the router builder in
`studio/backend/src/relayna_studio/logs.py` with an optional `pod` value. The
Loki provider will add `pod="<selected pod>"` to label filters when present.

Update `apps/studio/src/pages/ServiceDetailPage.tsx` to add a "Service Pods"
panel. It will poll the new pod route on an interval, show current pods, allow
one active pod selection, and load service logs filtered by the selected pod.

Keep frontend API and type exports unchanged by using `requestJson` and local
types inside the service detail page. Add targeted backend and frontend tests.

## Concrete Steps

1. Edit backend metrics and logs modules.
2. Add/adjust tests in `studio/backend/tests/test_studio_metrics.py` and
   `studio/backend/tests/test_studio_logs.py`.
3. Update the backend route freeze manifest for the new route.
4. Edit `apps/studio/src/pages/ServiceDetailPage.tsx` and tests in
   `apps/studio/src/App.test.tsx`.
5. Run:

       cd /Users/jobz/Works/relayna
       make -C studio/backend format
       make -C studio/backend lint
       make -C studio/backend typecheck
       make -C studio/backend test
       make -C apps/studio test
       make -C apps/studio build

## Validation and Acceptance

Acceptance criteria:

- Service detail pages show a dedicated pod panel when `metrics_config` exists.
- The pod list refreshes automatically and can be manually reloaded.
- Selecting a pod queries service logs with `pod=<pod name>`.
- Clearing the selection returns to service-wide logs.
- Existing service log filters still work.
- Backend tests prove Prometheus pod discovery and Loki pod filtering.
- Frontend tests prove the pod panel loads pods and pod-filtered logs.

## Idempotence and Recovery

The new route is read-only. Failed Prometheus or Loki requests should surface as
existing Studio error states without mutating registry data. Verification
commands can be rerun safely. If the pod route fails in an environment without
`metrics_config`, the panel should show an unavailable state rather than
blocking the rest of the service page.

## Artifacts and Notes

No artifacts yet.

## Interfaces and Dependencies

The pod list depends on Prometheus and kube-state-metrics exposing
`kube_pod_status_phase` and `kube_pod_labels`. Pod-specific logs require Alloy
to retain a Loki stream label named `pod`; otherwise Studio can still list pods
but Loki cannot filter by individual pod.
