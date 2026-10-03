# Studio as the complete Ampule Chamber API workspace

This living ExecPlan follows `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Operators use Relayna Studio to configure, run and investigate all Ampule Chamber assessments when Chamber has only an internal Kubernetes Service and port-forward access. Administrators can override deployment connection settings in Studio, verify connectivity, and manage reusable targets and profiles. Studio renders native configuration, monitoring, result/evidence, comparison and recovery views through server-to-server APIs.

## Progress

- [x] (2026-10-03) Audit both sources and current UI; read contributor, freeze, implementation, design, palette and Computer Use instructions.
- [x] (2026-10-03) Select the current Studio interface as visual baseline; compose the forest/neutral palette with Color Designer.
- [x] (2026-10-03) Implement encrypted connection settings, diagnostics and bounded authenticated API bridge.
- [x] (2026-10-03) Extend Chamber API coverage and versioned integration capabilities for UI-only operations, caller provenance and uploads.
- [x] (2026-10-03) Implement complete native assessment builder and result/evidence/history/recovery experience, preserving approved-operation flow.
- [x] (2026-10-03) Integrate existing monitor layouts, fix target/duration review, profile density and telemetry freshness.
- [x] (2026-10-03) Add behavioral and compatibility coverage; intentionally record approved Studio route perimeter additions.
- [x] (2026-10-03) Run mandatory verification in both repositories and validate core UI functions with Computer Use.
- [x] (2026-10-03) Document configuration/deployment, compatibility and acceptance evidence; commit and open draft PRs.
- [x] (2026-10-03) Add 67 further Studio behavior regressions and satisfy unchanged frontend gates: 233 tests, 98.07% statements locally (98.04% under CI Node 20) / 89.02% branches / 99.36% functions / 98.67% lines. Recheck SDK (97.94%) and backend (495 tests / 98.13%). Chamber adds 78 tests, reaches 394 tests / 97.79% global branch-and-statement coverage, and enforces a 96% floor.
- [ ] (2026-10-03) Enforce SDK and frontend coverage in CI, run final verification, consolidate into user-selected PR 131, retire superseded PR 132, and refresh PR descriptions/checks.

## Surprises & Discoveries

- Whole-source frontend coverage revealed untested advanced builder, result-management and legacy controls. Initial frontend coverage is 86.88% statements / 79.23% branches / 80.76% functions / 93.63% lines. Existing gates are 98/89/98/98; the backend already measures 98.13%, and SDK whole-package coverage is 97.94% (98% rounded). Chamber is raising global branch-inclusive coverage from 90% to an enforced 96% floor.

- Chamber 1.10.0 has comprehensive plan, execution, scenario, discovery, evidence and comparison APIs, but archive, tags, managed uploads and cleanup verification require API additions.
- Studio's existing backend uses one deployment token and strips plan/job identity from records. Imported operation profiles intentionally reject suites and experiments; the full assessment builder must be a separate path preserving their complete configurations.
- Current Studio 30-day Redis references and durable PostgreSQL profiles must remain readable. PostgreSQL already has an operator-settings table, so connection storage needs no schema migration.
- Chamber's Phase 05 plans were removed historically; its active Phase 03 plan explicitly owns integrated Studio preparation and hardening. Update that plan with this extension.

- Full scenario roundtrips initially failed because Studio redacted environment variable names. Preserve schema-declared secretEnv and headersFromEnv references while masking actual values; regression and real API smoke passed.
- Archive/tag mutation responses initially returned unbounded task details. Added compatible opt-in summary responses and requested them in Studio.
- Computer Use exposed run-window initialization races, stale execution labels, unreadable truncation metadata and excessive mobile history density. Corrected these and added regression coverage.

## Decision Log

- Decision: on the user's follow-up, consolidate Studio PR 132 into PR 131 by a fast-forward of its branch; retain the existing Chamber draft PR 43. Preserve coverage scopes and existing stronger gates. Add behavior tests for uncovered failures and workflows rather than exclude code. Date/Author: 2026-10-03 Codex.

- Decision: user explicitly authorizes all audited findings and the Studio connection/API/storage perimeter additions; keep SDK contracts untouched. Date/Author: 2026-10-03 Codex.
- Decision: preserve v1.9.0 released behavior and existing deployment variables; strict historical boundary is v1.4.30. Add reviewed routes and backwards reads rather than replace existing operation endpoints. Freeze-manifest additions are intentional review items. Date/Author: 2026-10-03 Codex.
- Decision: retain secret-only deployment fallback; encrypt UI tokens with a separate deployment-provided Fernet key and never return plaintext credentials. Pin the chosen connection to saved plans so changing the active connection cannot silently retarget an existing execution. Date/Author: 2026-10-03 Codex.
- Decision: use native Studio views and authenticated server APIs, not links that require opening Chamber in the browser. Preserve complete advanced configs with an expert editor while providing typed controls for common operations, suites, faults, agents and targets. Date/Author: 2026-10-03 Codex.
- Decision: branch from the existing monitor work and make the Relayna draft PR depend on PR 131; use a separate Chamber branch/PR for its API coverage. Date/Author: 2026-10-03 Codex.

## Outcomes & Retrospective

Delivered native Studio connection, full assessment, monitoring, results and recovery workflows, plus Chamber 1.11 API completion. Mandatory SDK/backend checks pass; PostgreSQL integration 21 tests; Studio backend coverage 495 tests / 98.13%; frontend 233 tests / 98.07% statements / 89.02% branches / 99.36% functions / 98.67% lines / production build; SDK 97.94% whole-package coverage; Chamber 394 tests / 97.79% global branch-and-statement coverage / enforced 96% floor / complete make check. Coverage scopes and exclusions remain unchanged. Actual ASGI integration passed 23 operations with 305 exact tasks. Computer Use validated connection save/test, multi-journey capacity review and explicit start/cancel, exact task pagination/search, reusable target creation and three monitor layouts, including 390-pixel responsive checks. The execution supervisor was stubbed to prevent process/Kubernetes traffic; live target execution remains deployment acceptance. User-selected draft PRs: https://github.com/sarattha/relayna/pull/131 and https://github.com/sarattha/ampule-chamber/pull/43. PR 132 is superseded by consolidating its additive Studio workspace into PR 131. GitHub check status is tracked on the PRs.

## Context and Orientation

Relayna Studio backend is `/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/`; frontend is `/Users/jobz/Works/relayna/apps/studio/src/`. Existing load testing is `load_testing.py`, `_profile_import.py`, `pages/LoadTestingPage.tsx` and `pages/LoadProfileManager.tsx`. Existing monitor components are `monitor-workspace.tsx`. Ampule Chamber lives at `/Users/jobz/Works/ampule-chamber`, with FastAPI routes in `chamber/control_plane/server.py` and durable run/job stores. Preserve the unrelated untracked Chamber audit folder.

## Compatibility Boundary

Latest released tag: Relayna v1.9.0; historical strict freeze boundary: v1.4.30. New Studio routes and component contracts are explicitly approved by the current user request. Preserve old route behavior, profile records, deployment settings and run references. Only intentionally amend route/page manifests if the delivered surface changes. Chamber additive APIs ship as a minor version; its released run/evidence formats remain readable.

## Plan of Work

Add a connection settings store and bounded transport in the Studio backend, reusing PostgreSQL operator settings. Extend the service-bound adapter with immutable target/connection snapshots and complete result projection. Add an allowlisted Chamber workspace API with read-only versus administrator authorization, credentials redaction, bounded payloads/downloads and mutation auditing. Add only missing Chamber API operations using existing stores and validators. Build native Studio Connection, Profiles, Configure, Monitor and Results views against these APIs. Keep the approved operation form, add a full assessment builder/catalog/expert editor, and render complete Chamber outcomes without directing operators to its private hostname.

## Concrete Steps

From `/Users/jobz/Works/relayna` run focused backend/frontend tests while iterating, then:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio coverage
    make -C apps/studio build

From `/Users/jobz/Works/ampule-chamber` run:

    make check

Validate connection save/test/fallback, advanced plan review, explicit starts, result tabs, profile management, discovery, comparison and cleanup confirmation through Computer Use against isolated local services. Capture narrow and desktop states and preserve evidence outside source fixtures.

## Validation and Acceptance

Tokens are encrypted in storage, omitted from responses/logs, and never forwarded to a changed host implicitly. Non-admins cannot configure connections or mutate Chamber. A deployment-only connection remains usable. Connection statuses distinguish unset/auth/transport/incompatibility/verified API, without claiming cluster readiness from installed tools. Advanced plans preserve suites, experiments, journeys, multipart and agents; planning sends no load and starting requires explicit reviewed target/fault confirmation. Run references retain their original connection/environment. Evidence downloads remain bounded attachments. Outcomes, missing evidence, cleanup, task pagination and telemetry staleness are explicit. All requested UI actions have working backend routes and meaningful tests; all required check commands pass before draft PRs.

## Idempotence and Recovery

Use stable plan-based start keys. Connection updates preserve old encrypted snapshots and allow reverting to deployment configuration. New settings occupy an existing operator-setting key; no schema upgrade needed. Never reset user work. Stop only local preview processes created for validation. Retain fixtures and verification evidence independently of runtime deployments.

## Artifacts and Notes

Implementation acceptance: `/Users/jobz/.codex/visualizations/2026/10/02/01a0fcee-5428-7012-a85d-703f18db5f39/ampule-studio-validation/validation.html`.

Prior audit: `/Users/jobz/.codex/visualizations/2026/10/02/01a0fcee-5428-7012-a85d-703f18db5f39/ampule-connection-audit/audit.html`.

## Interfaces and Dependencies

Internal Chamber HTTP API is proxied through Studio with fixed operation paths and Studio role/CSRF protection. UI connection encryption uses `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY`; deployment URL/token variables continue to apply when selected. No browser-accessible Chamber hostname is required. Explicitly support Chamber 1.10 capabilities and gate new 1.11 API operations from negotiated feature metadata.
