# Service Pod Metrics Panel

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Operators need a dedicated metrics panel on each Studio service page that shows
pod-level infrastructure graphs for every pod belonging to the service: API
pods, workers, aggregators, and any other Kubernetes pod matched by the
service's metrics selector. The panel must support quick time ranges including
15 minutes, 1 hour, 12 hours, 24 hours, 1 week, and 1 month, plus custom ranges,
and it must allow scoping the graphs to one selected pod.

## Progress

- [x] (2026-06-01 08:50Z) Created the goal and reviewed production freeze and
  implementation-strategy guidance.
- [x] (2026-06-01 08:50Z) Inspected existing Prometheus metrics provider,
  service-detail metrics UI, frontend API types, and metrics tests.
- [x] Extend the existing service metrics route with additive pod-level query
  controls.
- [x] Add a dedicated pod metrics graph panel to service detail pages.
- [x] Add backend and frontend tests for pod-scoped and split-by-pod metrics.
- [x] Run required Studio backend and frontend verification.

## Surprises & Discoveries

- Observation: The existing service metrics route aggregates Kubernetes metrics
  across all owned pods.
  Evidence: `studio/backend/src/relayna_studio/metrics.py` uses `_owned_pod_sum`
  and `_owned_pod_sum_by`.
- Observation: The prior pod list work already gives a current pod selector and
  `metrics_config` ownership selectors, so this feature can reuse that context.
  Evidence: `GET /studio/services/{service_id}/pods` and
  `ServiceDetailPage.tsx` service pod state.

## Decision Log

- Decision: Reuse `GET /studio/services/{service_id}/metrics` with additive
  query parameters `split_by_pod` and `pod`.
  Rationale: The response shape already supports multiple labeled series and
  time-series points, so no new route or response family is required.
  Date/Author: 2026-06-01 / Codex.
- Decision: Keep default service metrics unchanged and only return per-pod
  series when `split_by_pod=true` or a `pod` filter is provided.
  Rationale: Existing dashboard summary behavior remains compatible while the
  new panel can request graph-ready per-pod data.
  Date/Author: 2026-06-01 / Codex.

## Outcomes & Retrospective

Implementation and verification are complete.

## Context and Orientation

Studio backend metrics live in
`/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/metrics.py`.
The service detail frontend lives in
`/Users/jobz/Works/relayna/apps/studio/src/pages/ServiceDetailPage.tsx`.
Frontend API helpers and exported types live in
`/Users/jobz/Works/relayna/apps/studio/src/api.ts` and
`/Users/jobz/Works/relayna/apps/studio/src/types.ts`.

The existing service metrics route returns `StudioMetricsResponse`, which is a
list of `StudioMetricSeries` with labels and points. That shape can represent
pod-level lines by including `pod` labels on each series.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.17`; additive Studio backend
query parameters and frontend behavior only. Existing metrics callers that omit
the new parameters keep the same aggregate behavior and response shape.

## Plan of Work

Extend `StudioMetricsQuery` with optional `pod` and `split_by_pod` fields. When
pod-level mode is requested, Prometheus platform queries should group by the
configured pod label and optionally filter to a single pod. Runtime Relayna
aggregate metrics remain aggregate because they are not Kubernetes pod metrics.

Update frontend metrics fetching to pass the new query parameters and add a
dedicated "Pod Metrics" panel with compact SVG line charts. The panel will use
the current service pod list for pod choices, default to all pods, and expose
the requested quick ranges and custom range inputs.

## Concrete Steps

1. Patch backend metrics query model, query builder, and route query parsing.
2. Patch frontend API helper and service detail page.
3. Add targeted tests in `studio/backend/tests/test_studio_metrics.py` and
   `apps/studio/src/App.test.tsx`.
4. Run:

       cd /Users/jobz/Works/relayna
       bash .codex/skills/code-change-verification/scripts/run.sh
       make -C apps/studio test
       make -C apps/studio build

## Validation and Acceptance

Acceptance criteria:

- The existing Kubernetes Metrics summary still loads aggregate service values.
- A new Pod Metrics panel appears on service detail pages when metrics are
  configured.
- Operators can choose all pods or one pod and see graph lines for CPU, memory,
  network receive/transmit, restarts, OOM killed, readiness, and phase.
- The panel supports 15m, 1h, 12h, 24h, 1w, 1mo, and custom time ranges.
- Backend tests prove PromQL includes pod grouping and pod filters only when
  requested.
- Frontend tests prove pod metric requests include range and pod parameters.

## Idempotence and Recovery

The feature is read-only. Failed Prometheus requests surface in the panel and
do not modify registry data. Verification commands can be rerun safely.

## Artifacts and Notes

- Backend focused check:
  `cd /Users/jobz/Works/relayna/studio/backend && uv run pytest tests/test_studio_metrics.py -q`
  passed with 13 tests.
- Frontend focused check:
  `cd /Users/jobz/Works/relayna/apps/studio && npm test -- App.test.tsx`
  passed with 40 tests.
- Frontend build:
  `cd /Users/jobz/Works/relayna/apps/studio && npm run build`
  passed.
- Mandatory backend verification:
  `cd /Users/jobz/Works/relayna && bash .codex/skills/code-change-verification/scripts/run.sh`
  passed.
- Full frontend checks:
  `cd /Users/jobz/Works/relayna && make -C apps/studio test` and
  `make -C apps/studio build` passed.

## Interfaces and Dependencies

Pod metrics depend on Prometheus/cAdvisor/kube-state-metrics labels matching
the service's `metrics_config.namespace_label`, `pod_label`, and
`service_selector_labels`.
