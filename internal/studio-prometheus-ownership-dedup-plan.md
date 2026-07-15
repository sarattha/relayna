# Deduplicate Studio Prometheus Pod Ownership

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with `PLANS.md` at the repository
root.

## Purpose / Big Picture

Studio operators should be able to open service and pod metric views even when
Prometheus contains more than one `kube_pod_labels` series for the same
configured namespace and pod labels. Studio will reduce the filtered ownership
vector to one series per join key before arithmetic joins, preserve the existing
route response shapes, and expose useful but sanitized upstream Prometheus error
details for diagnosis. The behavior is observable through generated PromQL,
provider regression tests, the production Studio UI, and a real Prometheus
query that previously returned HTTP 422.

## Progress

- [x] (2026-07-15 12:55Z) Created branch `agent/fix-studio-prometheus-ownership` from `origin/main` at `f20dfd5`.
- [x] (2026-07-15 12:55Z) Reviewed issue #109, current PromQL construction, release boundary `v1.4.28`, freeze rules, and existing tests.
- [x] (2026-07-15 13:00Z) Implemented `max by` ownership reduction for range metric joins and one-series `topk by` selection for the metadata-preserving instant pod-list join.
- [x] (2026-07-15 13:00Z) Added bounded, control-character-safe Prometheus 4xx diagnostics and regression coverage for range and instant queries.
- [x] (2026-07-15 13:04Z) Passed focused tests, SDK and Studio formatting/lint/type checks, 419 SDK tests with one skip, and 244 Studio backend tests.
- [x] (2026-07-15 13:04Z) Validated through Computer Use against a real Prometheus 3.7.3 scrape: 10 ownership series including a duplicate UID resolved to 9 pods and 9 CPU, memory, and phase UI series after reload.
- [ ] Commit, push, open a ready-for-review pull request, and wait for the first code review.
- [ ] Address actionable first-review feedback, reply, resolve threads, and rerun verification.
- [ ] Bump the release to `1.4.29`, update changelog and operator documentation, rerun required verification, and finalize the pull request.

## Surprises & Discoveries

- Observation: the existing Studio backend test suite passes because it asserts
  PromQL strings but does not evaluate binary-match cardinality.
  Evidence: all 241 backend tests passed on the branch baseline while the
  ownership RHS remained unaggregated.
- Observation: the pod-list query copies selector-label candidates with
  `group_left(...)`, and the frontend reads those labels for the pod source
  caption.
  Evidence: `PrometheusMetricsProvider._build_pod_list_query` constructs the
  label list and `servicePodSource` reads `app`, `component`, `container`, or the
  first `label_` value.
- Observation: Prometheus 3.7.3 rejects the raw duplicate RHS with
  `many-to-many matching not allowed`, while the generated `max by` join returns
  the expected metric and `topk by(...)(1, ...)` keeps one display-label series.
  Evidence: live `/api/v1/query` responses from the Docker-backed engine and the
  Chrome Studio walkthrough both passed with the duplicate fixture active.

## Decision Log

- Decision: treat `v1.4.28` as the compatibility and production-freeze boundary.
  Rationale: the affected Studio metrics behavior shipped before and is present
  in `v1.4.28`.
  Date/Author: 2026-07-15 / Codex.
- Decision: implement this as a backward-compatible bug fix with no new route,
  export, configuration field, response field, persisted format, or freeze
  manifest change.
  Rationale: uniqueness is an internal PromQL invariant; existing clients should
  receive the same response models and successful data instead of an upstream
  422 failure.
  Date/Author: 2026-07-15 / Codex.
- Decision: keep detailed Prometheus diagnostics server-side safe for the
  existing 502 response path by sanitizing and bounding upstream strings.
  Rationale: provider exceptions are currently returned as HTTP details, so raw
  query URLs or unbounded upstream bodies must not leak.
  Date/Author: 2026-07-15 / Codex.
- Decision: use `max by(namespace, pod)` for range joins and
  `topk by(namespace, pod)(1, ...)` only for instant pod discovery.
  Rationale: `max` removes volatile ownership dimensions across every range
  step; `topk` provides the same cardinality guarantee for the instant query
  while retaining one original series so `group_left` can preserve the existing
  pod source label shown by the frontend.
  Date/Author: 2026-07-15 / Codex.

## Outcomes & Retrospective

Work is in progress. This section will record the final implementation, real
environment evidence, PR/review outcome, release metadata changes, and any
remaining risks.

## Context and Orientation

The Studio backend lives under `studio/backend/`. Its Prometheus provider in
`studio/backend/src/relayna_studio/metrics.py` builds service ownership from
`kube_pod_labels` and joins Kubernetes platform metrics on configured namespace
and pod label names. Prometheus `group_left` permits multiple metric series on
the left but requires a unique ownership series on the right for each match
key. Extra labels such as pod UID or scrape identity can leave multiple right
side series and cause a many-to-many execution error returned as HTTP 422.

Regression tests live in `studio/backend/tests/test_studio_metrics.py`. The
Studio frontend reads the pod-list response in
`apps/studio/src/pages/ServiceDetailPage.tsx`. Operator documentation is in
`docs/studio-backend.md` and `docs/aks-observability.md`. Release metadata is in
the root and Studio package manifests, `CHANGELOG.md`, and release/install docs.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.28`; preserve all Studio
backend routes, response models, configuration models, frontend contract types,
and existing label behavior where it is safe. The fix changes only generated
PromQL and provider error detail. No shim or migration is needed because no
persisted or wire format changes. The production freeze manifests must remain
unchanged unless a later diff proves that the public perimeter changed.

## Plan of Work

Add a helper in `metrics.py` that takes the fully filtered ownership expression
and reduces it by `config.namespace_label` and `config.pod_label`. Use the
unique vector for every platform arithmetic join. For the instant pod-list
query, preserve only deterministic display metadata while maintaining exactly
one RHS series for each join key; if safe preservation is not possible, update
the frontend fallback deliberately rather than retaining a cardinality hazard.

Add a bounded parser for Prometheus error responses. It will retain status,
endpoint kind, range parameters, `errorType`, and a sanitized/truncated error
message without exposing the configured base URL. Cover range and instant query
failures.

Expand `test_studio_metrics.py` to assert custom join-label aggregation,
multi-selector/conflict-name filtering, pod-list uniqueness, duplicate-series
fixtures, and sanitized 422 diagnostics. Use a real PromQL engine locally if
available so at least one test demonstrates that a duplicate ownership vector
fails before normalization and succeeds after it.

After local verification, use Computer Use to inspect the production Studio
service metrics UI and verify pod discovery and charts against its configured
Prometheus. Avoid mutating production data. Publish a ready-for-review PR that
resolves issue #109. Monitor it until the first review, address all actionable
threads requested by the user, reply and resolve them, then update version,
changelog, and docs and run the verification stack again.

## Concrete Steps

From `/Users/jobz/Works/relayna`:

    uv run --directory studio/backend pytest tests/test_studio_metrics.py
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Use Docker-backed Prometheus tooling or the existing real environment for
PromQL evaluation. Use Git and GitHub only after the working tree and diff have
been reviewed explicitly.

## Validation and Acceptance

Acceptance requires generated platform and pod-list joins to have one ownership
series per configured namespace/pod key; custom label names must not be
hard-coded. Nine distinct pods must remain nine pods, while a duplicate
ownership series for one pod must not cause HTTP 422. Split-by-pod and aggregate
metrics must keep their existing result labels and values. Prometheus 4xx errors
must expose bounded diagnostic context without leaking the base URL. Existing
route and freeze tests must pass.

The final proof consists of focused regression tests, the mandatory SDK/Studio
verification script, relevant frontend checks if frontend behavior changes, a
real-environment Computer Use walkthrough, successful PR checks, and resolution
of the first actionable code review.

## Idempotence and Recovery

All tests and formatters are safe to rerun. The PromQL helper is a pure string
builder. No migration or production write is required. If the real environment
is unavailable, preserve local and Docker-backed evidence and report the access
blocker instead of changing external configuration. If PR review requests
conflict, stop before implementing the conflicting behavior and document the
tradeoff.

## Artifacts and Notes

Issue: `https://github.com/sarattha/relayna/issues/109`.

Baseline verification on 2026-07-15: 241 Studio backend tests passed before the
fix, demonstrating the gap in cardinality evaluation coverage.

## Interfaces and Dependencies

No new runtime dependency is planned. `PrometheusMetricsProvider` remains the
provider implementation, `PrometheusMetricsConfig` remains the configuration
contract, and `StudioMetricsResponse` plus `StudioServicePodListResponse`
remain the response contracts. PromQL must use configured namespace and pod
label names throughout.
