# Reusable Studio observability profiles

This ExecPlan is a living document. Maintain Progress, Surprises & Discoveries,
Decision Log, and Outcomes & Retrospective in accordance with `PLANS.md`.
Paths below are relative to `/Users/jobz/.t3/worktrees/relayna/t3-6b2d1770`.

## Purpose / Big Picture

An administrator configures a named Loki, Prometheus, or Tempo connection once,
including its credentials, and selects it while registering or editing a
service. For example, an administrator saves `Production Loki`, then selects it
for both Payments and Orders without entering its URL or password again. Each
service retains its own log selectors, Prometheus namespace and selectors, and
other service-specific query settings. Endpoint and credential changes on a
profile apply to its linked services.

The first version supports the three providers Studio already queries: Loki
for logs, Prometheus for metrics, and Tempo for traces. Supporting additional
providers is outside this change. Existing manual configurations remain usable.

## Progress

- [x] (2026-11-01) Reproduce and fix both PR review findings with regression tests.
- [x] (2026-11-01) Mandatory verification, frontend build, and coverage pass after the review fixes: 704 SDK tests, 695 backend tests, 380 frontend tests; backend coverage 98.25%, frontend lines 98.86%, new UI lines 100% and branches 98.07%.
- [x] (2026-11-01) Publish both verified review fixes in PR #145 and continue monitoring without merging; mark their review threads resolved after the push.

- [x] (2026-11-01) Inspect service registration, provider clients, PostgreSQL
  persistence, authentication, existing encrypted settings, and freeze tests.
- [x] (2026-11-01) Apply `implementation-strategy` and
  `production-freeze-guard`; prepare this scoped implementation proposal.
- [x] (2026-11-01) Obtain explicit approval for the Studio production-perimeter exception
  described in Compatibility Boundary before changing runtime code or manifests (approved 2026-11-01).
- [x] (2026-11-01) Implement durable profiles, safe credential handling, and profile API.
- [x] (2026-11-01) Implement service bindings and authenticated provider request resolution.
- [x] (2026-11-01) Add profile management and selection to the service registration editor.
- [x] (2026-11-01) Add regression, persistence, authorization, redaction, and race tests; update documentation and narrowly scoped freeze entries. Add a per-file frontend coverage gate above 95%.
- [x] (2026-11-01) Run mandatory SDK/backend verification and frontend build and coverage. Complete shared-browser desktop/mobile UI checks.
- [x] (2026-11-01) Final coverage measurements pass: backend 98.24% overall and new profile module 99.60%; frontend 98.86% lines overall and new profile UI 100% lines / 98% branches.
- [x] (2026-11-01) Open PR #145: https://github.com/sarattha/relayna/pull/145. Activate T3 check/review monitoring at handoff; merging remains prohibited until the user signals.
- [ ] Native Computer Use remains blocked: browser host unavailable, Chrome timeoutReached, Safari noWindowsAvailable. Shared preview browser used as an explicitly reported fallback.

## Surprises & Discoveries

- PR review found that Loki `source_label` defaults updated a redundant draft
  field while the editor and serializer read `log_app_label_key`. Regression tests
  reproduced blank, stale, and explicit-null source defaults before the fix.
- PR review found that Tempo profile HTTP/policy failures bypassed the trace-path
  enrichment fallback. Real resolver regressions reproduced missing/wrong keys,
  missing profiles, and blocked endpoints suppressing graph/event/DLQ diagnostics.

- The latest local release tag is `v1.13.0`; `HEAD` is `7dfd2b1`.
  `registry.py` and `ServicesPage.tsx` have no differences against that tag.
  Their current registration behavior is released behavior.
- The guide and guard skill name `v1.4.30` as the strict freeze boundary, while
  current frontend and backend surface tests use manifests labeled `v1.12.0`.
  Both prohibit silently widening the production perimeter. This feature does
  not need a broad freeze-version rewrite.
- Production Studio uses PostgreSQL as its durable authority. An existing
  `studio_operator_settings` JSON table supports versioned setting documents.
  The programmatic app factory also supports Redis-only test/development use.
- `_chamber_connection.py` already encrypts credentials with Fernet using
  `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY`, omits secrets from public responses,
  and audits mutations in the same PostgreSQL transaction.
- Current provider configurations contain URLs, tenant information, and query
  options, but do not have bearer/basic credential fields. Credential support
  must reach the actual Loki, Prometheus, and Tempo HTTP calls, including both
  Prometheus instant and range queries.
- Service configuration mixes shared connection settings with selectors that
  identify one service. Reusing a complete configuration without separating
  these concerns could query another service's telemetry.

## Decision Log

- Decision: Correct both review findings within the existing approved perimeter;
  keep the Loki editor/serializer fields synchronized and translate Tempo profile
  failures into sanitized provider-domain errors. Preserve the released trace-path
  diagnostic fallback and its response schema. No new manifest or migration changes.
  Compatibility boundary remains v1.4.30 policy / v1.13.0 released behavior;
  profile binding fixes affect only this PR's unreleased additions.
  Date/Author: 2026-11-01, Codex.

- Decision: Propose one independently selectable profile per provider, with an
  explicit environment scope. Keep service selectors and namespaces editable
  on the service. Do not automatically select a profile based on its name.
  Rationale: Different services can share a telemetry endpoint while requiring
  distinct query scopes; production and staging credentials should not be
  accidentally interchanged.
  Date/Author: 2026-11-01, Codex; user approved the proposal on 2026-11-01.
- Decision: Keep profile endpoint and credentials linked, and resolve them on
  the backend for each query. Copy non-secret query defaults into the service
  editor when a profile is selected; these become service-owned values.
  Rationale: Credential rotation applies centrally while later default changes
  do not unexpectedly replace service-specific selectors or query settings.
  Date/Author: 2026-11-01, Codex; user approved the proposal on 2026-11-01.
- Decision: Store versioned profile documents in `studio_operator_settings`;
  add optional `profile_id` references to the existing provider-configuration
  JSON documents. Reuse the existing settings encryption key and audit pattern.
  Rationale: No new SQL tables or Alembic revision are necessary. These are
  nevertheless new durable document fields and an intentional API extension.
  Date/Author: 2026-11-01, Codex; user approved the proposal on 2026-11-01.
- Decision: Profile storage requires PostgreSQL; saving authenticated profiles
  also requires the configured encryption key. Existing inline configurations
  continue working in Redis-only programmatic deployments. Profile operations
  return an actionable unavailable response when storage is absent.
  Rationale: Durable Studio configuration belongs in PostgreSQL, consistently
  with existing operator settings.
  Date/Author: 2026-11-01, Codex; user approved the proposal on 2026-11-01.
- Decision: The user explicitly approved the scoped production-perimeter exception and requested implementation, a PR, and monitoring without merging.
  Rationale: A feature request alone does not explicitly approve changing the
  production perimeter required by the contributor guide and guard skill.
  Date/Author: 2026-11-01, Codex.

## Outcomes & Retrospective

The feature is implemented under the user-approved freeze exception. Admins can
manage named profiles, use them in service registration, rotate credentials,
and test saved connections. Existing manual configurations remain supported.
Shared-browser UI validation exercised real backend/PostgreSQL storage with
synthetic credentials and a local provider fixture. It covered creating/testing
all three providers, bearer/basic authentication, blank credential preservation,
renaming, registration and persisted profile selections, environment changes,
referenced-profile deletion protection, cancel/confirm deletion, JSON validation,
failed reload and recovery, and a 390px mobile layout. Native Computer Use itself
could not run because its host could not connect to browser windows; this is a
remaining verification limitation, not a passed check.

PR #145 is open at https://github.com/sarattha/relayna/pull/145. Local checks and coverage gates passed. T3 monitoring is activated at handoff to handle checks and reviews; merging requires a separate user signal.

## Context and Orientation

Studio is Relayna's separate administrative control plane; the SDK is the
service-side Python library. This task changes Studio only.

`apps/studio/src/pages/ServicesPage.tsx` contains the registration and editing
form. `services-context.tsx`, `api.ts`, and `types.ts` translate its draft into
backend requests. `studio/backend/src/relayna_studio/registry.py` defines
`LokiLogConfig`, `PrometheusMetricsConfig`, `TempoTraceConfig`, service records,
and registration routes. `database.py` persists the three configuration objects
as JSON and provides transactionally written operator audit records.

`logs.py`, `metrics.py`, and `traces.py` issue provider HTTP requests.
`StudioOutboundUrlPolicy` in `registry.py` restricts their destinations.
`app.py` constructs the runtime and installs routers. `auth.py` limits
`/studio/admin/` to administrators and applies CSRF checks to browser mutations;
`_operator_auth.py` supplies the supported operator-auth deployment mode.
`_chamber_connection.py` is a reference for encrypted settings, not a dependency
to import private Chamber-specific transport behavior from.

## Compatibility Boundary

Policy boundary: `v1.4.30` strict production freeze. Released implementation
comparison: latest local release tag `v1.13.0`. Current manifests labeled
`v1.12.0` remain guards; their version labels are not changed just for this feature.

The user approved these additions only: profile CRUD routes under
`/studio/admin/observability-profiles`, a saved-profile connection-test route,
optional per-provider service `profile_id` fields, versioned profile documents
in `studio_operator_settings`, authenticated provider requests, and the frontend
API/types/UI required to manage and select these profiles. Update the backend
route/feature manifests and frontend API/type manifest only for this scope.
No SDK export or SDK contract changes are proposed.

Existing inline configurations and registration payloads remain valid, with
unchanged behavior when no `profile_id` is provided. Existing required URL fields
remain accepted; the editor can fill them from non-secret profile metadata.
When a reference is present the backend validates it and uses the stored profile
as the authority for connection URL, tenant and credentials, regardless of a
submitted URL. Public service responses show the effective non-secret connection
settings. Credentials and ciphertext never enter service response objects.

Old stored service documents without profile references remain readable without
backfill. Profile-bound services require the updated backend; rollback requires
detaching profiles into explicit inline configuration and arranging any needed
upstream authentication before restoring a backend without profile support.
Document that downgrade limitation rather than silently ignoring the references.

## Plan of Work

First implement a private observability-profile module under
`studio/backend/src/relayna_studio/`. A profile has a stable ID, unique name
within environment/provider scope, environment, immutable provider kind,
endpoint, optional tenant, provider-specific non-secret defaults, authentication
mode, encrypted credentials, revision, and timestamps. Initial authentication
modes are none, bearer token, and basic username/password. Secrets are write-only;
responses report authentication mode and whether a credential is configured.
Omitting credentials on update preserves them; clearing them requires an
explicit authentication-mode change. Validate URLs without embedded credentials,
secret query parameters, or fragments; preserve legitimate provider base paths.
Use strict typed request models and errors that exclude submitted secrets.

Expose list/create/read/update/delete operations and a saved-profile connection
test through the admin route namespace. Use the existing auth modes and CSRF
mechanisms. Store each versioned document under a reserved settings key. Serialize
profile edits, service binding changes, and deletion checks consistently in
PostgreSQL transactions; prevent deletion while a non-deleted service references
the profile. Audit profile changes without credentials, ciphertext, or raw
request bodies. Reject stale edits with a revision conflict.

Extend the three registry configuration models with optional profile references.
Validate provider kind and exact environment compatibility on service create
and update, including service-environment changes. Preserve unbound behavior.
Resolve effective public configuration when reading service records and resolve
secrets immediately before provider requests. Missing profiles or decryption
failures produce explicit unavailable errors, never unauthenticated fallback.
Apply endpoint changes and credential rotation without process restarts. Keep
credential resolution centralized so normal views, task views, load-testing
views, and reliability queries use the same behavior.

Apply the existing outbound policy at save, test, and request time. Keep request
headers local to each request so shared HTTP clients cannot leak credentials
between services. Do not follow redirects with credentials or attach secrets to
Tempo public trace links. Sanitize upstream failures before they reach errors
or logs. Connection tests must exercise the selected provider's read API with a
bounded request and report authentication/network/provider errors distinctly.

Add a profile manager component to the existing Services page rather than a new
top-level navigation page. Admins can create, edit, test, and delete profiles and
see which services use them. Reuse existing form and dialog components. In each
registration configuration section offer Disabled, Manual, or a saved profile
filtered by provider and the service's environment. Show inherited connection
settings and retain editable service-specific query fields. Explicitly clear
incompatible selections when environment changes, and block saving until the
user resolves required settings. Preserve entered service fields when profile
fetches fail; show loading, empty, error, and retry states. Profile edits explain
that endpoint and credential changes affect every linked service.

Add backend tests alongside registry/provider tests, frontend profile/form tests,
and PostgreSQL persistence/concurrency tests. Update `docs/studio-persistence.md`
and add focused administrator documentation for profile setup, linking,
credential rotation, key backup, deletion, and rollback. Include links in the
existing Studio documentation navigation where appropriate.

## Concrete Steps

From the repository root, after the scoped exception is approved, implement the
backend and frontend milestones above and run focused tests while iterating.
Finish using the code-change-verification skill and frontend targets:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

The verification script runs SDK formatting, lint, typecheck, and tests, followed
by the same Studio backend stack in fail-fast order. Fix failures and rerun the
required stack. Run PostgreSQL-backed feature tests against a disposable real
database using the existing integration-test fixture conventions; do not count
skipped persistence tests as proof of persistence or concurrency behavior.

## Validation and Acceptance

An admin can save one profile for each supported provider and select it on two
new services with different service selectors. Both registrations work without
entering connection URLs or credentials again. Editing and reloading a service
preserves its binding and selectors; backend restarts preserve profiles.

Actual Loki, Prometheus instant/range, and Tempo requests receive only the
selected profile's credentials and tenant. Rotating credentials or changing an
endpoint affects subsequent requests for both linked services. Service selectors
do not change when another service or the profile defaults are edited. Changing
endpoint with existing credentials requires explicit confirmation/re-entry to
avoid inadvertently sending an old secret to a different destination.

Read-only users cannot manage profiles. Wrong-provider, wrong-environment,
missing, deleted, or undecryptable bindings fail explicitly. A referenced profile
cannot be deleted, including concurrent bind/delete attempts. Concurrent edits
do not silently overwrite credential changes.

Responses, validation errors, provider failures, service/Gateway exports, audit
records, and logs contain neither plaintext secrets nor ciphertext. Stored
authenticated profiles contain encrypted credentials only. Profile reads never
return a reusable password or token. Existing inline registration and provider
tests pass without requiring profiles or an encryption key.

All mandatory verification commands pass, approved freeze changes are narrowly
reviewable, and the final handoff includes the required PR draft.

## Idempotence and Recovery

Profile updates use stable IDs and revision checks; creating another profile
with the same environment/provider/name returns a conflict. Transactions roll
back failed binding or settings writes together with their audit records.
Retain the settings encryption key with existing deployment secrets and backup
procedures. A missing/wrong key blocks authenticated queries clearly until the
correct key is restored or an admin replaces the credential. Unrelated inline
services continue to operate. No existing Alembic revision is edited. Damaged or unavailable bindings remain visible and editable in registry reads, while provider requests and new bindings resolve strictly.

## Verification evidence

The mandatory verification script passed SDK formatting/lint/types and 704 SDK
tests (9 existing skips), then backend formatting/lint/types and 691 backend tests,
including real PostgreSQL/Redis integration tests. The frontend build passed.
The new UI module's focused coverage gate passed at 100% statements/functions/lines
and 98% branches; `apps/studio/vite.config.ts` now enforces per-file minimums of
98% statements/functions/lines and 96% branches. Whole-source frontend line
coverage is 98.86%; its existing whole-source branch threshold remains unchanged.
Final backend coverage passed at 98.24% overall and 99.60% for the profile module, measured after all runtime edits. All 691 backend tests ran with disposable PostgreSQL and Redis. The frontend suite passed all 377 tests.

## PR review regressions

The Loki source-default regression failed for all three cases before the fix:
blank source label, stale source label, and explicit null. All now serialize the
selected value through the editor's actual `log_app_label_key` field. Tempo
regressions failed before the fix for missing/wrong encryption keys, a missing
profile, and a disallowed endpoint. They now return HTTP 200 trace-path diagnostics
with graph nodes, events, DLQ records, trace identifiers, and an enrichment warning;
direct trace requests return a provider error without attempting an HTTP request
or exposing credentials. No route/schema/manifest changes were needed.
