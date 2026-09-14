# Service load testing in Studio

Maintain this living plan according to /Users/jobz/Works/relayna/PLANS.md.

## Purpose / Big Picture

An administrator opens a service's Load testing workspace, selects an approved operation, supplies typed request inputs and bounded load settings, reviews a plan, starts it, and follows execution, task links, service logs and AKS pod metrics through the Studio hostname. Ampule Chamber owns execution and evidence; Studio owns authentication, service selection and input validation.

## Progress

- [x] (2026-09-14) Read preparation, runtime contracts, Studio authentication and freeze rules; working tree clean; created codex/studio-chamber-load-testing.
- [x] (2026-09-14) Implemented backend adapter, schema validation, service/environment bindings, retained history and idempotent execution.
- [x] (2026-09-14) Implemented native service workspace, structured inputs, review/start/cancel, task links, logs and pod charts.
- [x] (2026-09-14) Added backend/frontend regression tests, profile example, deployment documentation and narrow additive route/page/feature freeze entries.
- [x] (2026-09-14) Mandatory verification passed: SDK 686 passed/9 skipped, backend 304 passed/15 skipped; frontend 117 passed and production build passed. Computer Use validated desktop and 390-pixel layouts, with viewport and document width both 390.
- [x] (2026-09-14) Opened draft PR https://github.com/sarattha/relayna/pull/127 on codex/studio-chamber-load-testing.

## Surprises & Discoveries

The user named v0.10.0; the available release and preparation artifact identify v1.10.0. Asked for clarification; proceed with the documented v1.10.0 unless corrected. Chamber serves templates, not a reusable React component library. Its API includes immutable plans, idempotent starts and bounded job output. Studio capability documents do not contain request schemas. Kubernetes attach configuration needs explicit workload and context mapping; guessing these from a display name is unsafe.

## Decision Log

2026-09-14: Add native Studio service Load testing pages backed by a narrowly scoped server adapter; reuse Chamber API/evidence projections and Studio telemetry/chart components. No iframe or second public hostname.

2026-09-14: Service profiles are operator-owned configuration keyed by immutable Studio service ID and environment. Profiles pin Chamber configuration, request schema, Kubernetes target and maximum load. Browsers supply only validated request values and load sizes. Unsupported schema forms fail closed. Existing services without profiles show setup guidance.

2026-09-14: User explicitly requested the integration, authorizing narrowly additive Studio production-perimeter changes. Preserve released APIs and SDK behavior. Use existing Studio admin/CSRF middleware and Redis persistence for service-bound plans/jobs; never expose Chamber credentials or accept arbitrary upstream paths.

## Outcomes & Retrospective

Implementation and verification complete; draft PR #127 is open for review. Chamber 1.10.0 successfully planned the generated Relayna configuration using its actual planner. Computer Use exercised a synthetic local preview through the actual Studio adapter; no live load was generated. Real AKS execution remains unverified and requires deployment configuration. Multipart supports approved Chamber file fixtures rather than new browser uploads.

## Context and Orientation

Repository /Users/jobz/Works/relayna contains the SDK (service runtime), Studio backend under studio/backend/src/relayna_studio and React frontend under apps/studio/src. The external /Users/jobz/Works/ampule-chamber repository contains the v1.10.0 execution API and Kubernetes attach examples. Studio's registry identifies services; Loki supplies logs and Prometheus supplies pod metrics. Chamber's job owns the execution run and retained output.

## Compatibility Boundary

Strict production boundary v1.4.30; latest available release v1.7.0. Additive Studio routes, optional deployment environment variables and a new page are authorized by the integration request. Update only their freeze manifest entries, with this compatibility note. Existing service records, SDK public imports, task/status/workflow contracts and RabbitMQ protocols remain compatible. New Redis keys use a dedicated prefix; no migration of existing data.

## Plan of Work

Add studio/backend/src/relayna_studio/load_testing.py for profile loading, schema validation, bounded Chamber calls, service-bound plan/start/status/cancel routes and run history. Wire it in app.py using existing runtime dependencies. Add a native LoadTestingPage and a structured input component, a service detail link and app route. Reuse service log and metric APIs. Add backend/frontend tests and docs/studio-load-testing.md with a sample configuration.

## Concrete Steps

From /Users/jobz/Works/relayna run focused tests while developing, then:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Validate the running UI through Computer Use with explicitly synthetic local fixtures; exercise typed fields, review/start, retained failure/cancel states, links and narrow layout. Commit, push and use gh pr create --draft with the repository template.

## Validation and Acceptance

Invalid inputs, excess load, disabled/wrong-environment services and foreign plan/run IDs must fail before execution. Retries reuse the same Chamber idempotency key. Secrets, configuration paths and arbitrary target selection stay server-side. After reload, service history resumes polling a run. Readonly users can inspect but cannot plan/start/cancel. Missing telemetry is explicit; logs and pod metrics use the run time window, and exact evidence task IDs link to Studio task details. All required checks pass and the draft PR records validation limits.

## Idempotence and Recovery

Creating a plan does not execute traffic. Starts use the bound plan identity as the persistent idempotency key. A network failure can be retried against the same plan. Cancellation requests Chamber cancellation and keeps polling until terminal. Revert the branch and remove optional deployment configuration to disable the integration; existing Studio records are unaffected.

## Artifacts and Notes

Chamber preparation: /Users/jobz/Works/ampule-chamber/docs/internal/phases/phase-03-traffic-and-chaos/artifacts/studio-integration-hardening.md.

## Interfaces and Dependencies

Use httpx and redis already present in Studio, add standards-based JSON Schema validation for service request contracts. Optional RELAYNA_STUDIO_CHAMBER_URL, RELAYNA_STUDIO_CHAMBER_TOKEN and RELAYNA_STUDIO_CHAMBER_PROFILES_PATH configure backend-only integration. Chamber API: POST /api/v1/plans, POST /api/v1/runs, GET /api/v1/jobs/{id}, POST /api/v1/jobs/{id}/cancel, GET /api/v1/runs/{id}.

2026-09-14 validation findings: actual Chamber planning rejected `agents.mode: disabled`; corrected the example to the supported `off` value and reran planning successfully. Guard against `experiment` and separate `traffic.load` settings overriding the reviewed observe-only journey. Visual checks moved the sticky run list below the header and added pod-color legends.

Final review: disabled services retain cancellation access for existing jobs. Terminal telemetry windows use Chamber run timestamps when available. Regression coverage passed in the final full verification run.

## OpenAPI follow-up — 14 September 2026

The user requested automatic request schemas. Preserve manual profile schemas as overrides; when omitted, fetch the registered service's /openapi.json (operator-configurable relative openapi_path), match its pinned method/path/encoding, resolve local references and normalize common OpenAPI 3.0/3.1 request bodies. Never follow remote references, redirects or OpenAPI servers. Reuse the existing outbound allowlist. Bound document size and recursion. Keep runtime targets/load limits/lifecycle settings explicit. Surface individual import failures alongside usable profiles. Carry a schema revision from form to plan and reject stale forms; reviewed plans keep their snapshot. This extends the unreleased PR #127 interface directly, with no change to released SDK contracts or new routes. The user explicitly approved breaking the production freeze if needed. This extension adds schema provenance/revision and import errors to unreleased load-test responses and a revision field to plan requests, with no new routes.

- [x] Implement and test OpenAPI importer, safe discovery and form revision validation.
- [x] Show schema provenance, nullable fields and import errors in Studio; update examples/docs.
- [x] Run mandatory verification and refresh the local preview.
- [x] Push the OpenAPI follow-up and update draft PR #127 (ff96d57).

User steering: exclude standard Relayna SDK endpoint families and SDK-tagged operations. Focus request-schema import on per-service business operations; SDK status/events remain usable as task lifecycle observers.

Follow-up validation: the complete verification script passed (686 SDK tests, 337 backend tests), along with 119 frontend tests and the production build. Computer Use confirmed imported schema provenance, nullable-to-integer controls and successful plan review in a synthetic preview on port 18994. Common SDK routes are filtered while business routes remain eligible; no live AKS load was generated.

## Release and landing — 15 September 2026

The user authorized a synchronized 1.8.0 version bump, changelog/docs, Codex
review, fixing valid findings and checks, and merging after satisfaction.
Latest released boundary remains v1.7.0; approved additive Studio perimeter
and version manifest updates introduce no SDK contract or existing-data changes.
Backend CI coverage is 97.21% against 98%; add meaningful failure-path coverage.
Deployment is deferred until the user builds and deploys Studio via Azure, then
confirms readiness for Chamber 1.10.0 and backend connection.

- [x] Update release metadata, locks, changelog and rollout documentation.
- [ ] Meet coverage and required verification; request and satisfy Codex review.
- [ ] Merge PR #127 only after checks/review pass; stop PR monitoring.

Release verification: all mandatory checks passed; 686 SDK tests and 119 frontend tests passed, with successful frontend/docs builds. Temporary PostgreSQL/Redis enabled all 398 backend tests with 98.13% total coverage (100% for both new adapter/importer modules). The database migration cycle passed. Codex review requested on fbed292; CI/review pending.

Codex review on fbed292 identified three valid P2 findings. Fix retention to a
stable plan-creation deadline, return terminal snapshots during upstream
outages, and choose non-null enum values when leaving nullable controls.
These amend unreleased behavior directly; no released-state migration is needed.
Add regression coverage and request a fresh Codex review after verification.

All three first-review findings are fixed with regressions. Mandatory verification passed; full PostgreSQL/Redis backend coverage passes at 98.13% with 400 tests, and 120 frontend tests/build pass. Request a second Codex review of the fix commit before landing.
