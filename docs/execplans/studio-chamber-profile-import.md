# Import Chamber configurations into Studio service profiles

Maintain this living plan according to `PLANS.md`.

## Purpose / Big Picture

An administrator opens a service's Load testing page, browses Chamber saved plans
and runs, previews a selected operation and its OpenAPI input fields, sets approved
load limits, and saves a persistent profile without a ConfigMap edit or restart.
Existing deployment profiles continue working. Import never starts load traffic.

## Progress

- [x] 2026-09-15: Inspect Studio and Chamber APIs; establish compatibility boundary.
- [x] 2026-09-15: Implement PostgreSQL storage/migration, import preview/save/delete and tests.
- [x] 2026-09-15: Implement admin profile manager; Computer Use verified source selection, OpenAPI preview, 16-user limit, PostgreSQL save, immediate form availability and confirmed removal.
- [x] 2026-09-15: Update documentation and additive freeze perimeter; mandatory stack, frontend tests/build and strict docs pass. Database coverage exceeds 98%; Alembic check finds no pending schema operations.

## Surprises & Discoveries

Chamber 1.10.0 named chamber profiles contain environment budgets, not HTTP request
contracts. Saved plans/runs expose complete ChamberConfig through GET runs/{id};
use this supported source for import. Scenario catalog projections are templates
and do not necessarily carry a complete bound execution target.

## Decision Log

2026-09-15: Apply implementation-strategy and production-freeze-guard with the
user's existing explicit authorization. Latest local release tag v1.8.0; main
contains released 1.8.2 packaging. Preserve deployed profile behavior, SDK/wire
contracts and reviewed plans. Add only Studio routes and one PostgreSQL table.
Bind each imported profile to immutable service ID and exact environment. New
profiles use OpenAPI; never trust browser-supplied execution configuration.
Preview snapshots expire, are service-bound and checked again at save. Store
approved configuration in PostgreSQL; existing ConfigMap profiles remain fallback.
No sandbox mutation or load execution is necessary to implement this change.

## Context and Orientation

Repository: /Users/jobz/Works/relayna. Backend load_testing.py implements the
Chamber adapter, database.py owns PostgreSQL metadata, migrations/versions owns
Alembic revisions. Frontend pages/LoadTestingPage.tsx renders service testing.
Chamber source at /Users/jobz/Works/ampule-chamber is read-only API reference.

## Plan of Work

Extract reusable profile validation. Add a private profile store with an Alembic
migration. Merge imported profiles into existing read/plan paths. Add admin-only
catalog, preview, save and delete routes, with bounded upstream calls and server
snapshots. Build a step-by-step UI with target, operation, files, schema and
load-limit review; support removing imported profiles. Document upgrade/import.

## Validation and Acceptance

Verify read-only rejection, CSRF, environment binding, expired/tampered preview,
invalid schemas/configs, limits, persistence, deletion and existing fallback.
Run mandatory code-change-verification, database-backed tests, frontend tests and
build, strict docs, and Computer Use against a local preview. Import/save must
not call Chamber execution endpoints.

## Idempotence and Recovery

Stable imported IDs make retries idempotent; delete removes only the imported
profile. Previously reviewed plans retain their snapshots. Alembic downgrade
removes only the new table (back up imported profiles before downgrading).

## Outcomes & Retrospective

Implemented on `codex/studio-chamber-profile-import`. Existing ConfigMap profiles
remain supported. Source import is limited to complete Kubernetes attach-mode
Chamber plans/runs and supported OpenAPI schemas; unsupported sources show setup
errors. PostgreSQL migration is required before deploying the backend. No sandbox
configuration or load traffic was changed. Local browser preview uses simulated
Chamber responses and real isolated PostgreSQL.

Verification: SDK 686 passed/9 skipped, backend 442 passed/18 database-dependent
skips in the standard stack, frontend 134 passed/build, strict docs, and the full
database-backed coverage gate (460 passed, 98.12%). The migration was also
downgraded to 0001 and upgraded again successfully on the isolated local database. Screenshot retained at
`/tmp/relayna-chamber-integration/profile-import-review.png`.

PR-ready title: `feat(studio): import Chamber plans as service load profiles`.
This pull request adds administrator import, review and persistent management of
service load-test profiles from Chamber plans/runs. It preserves existing
ConfigMap behavior, adds six authorized Studio routes and a profile manager,
and requires additive Alembic revision `0002_load_profiles`. SDK contracts and
previously reviewed load-test plans remain unchanged.

## Release and landing

2026-09-15: The user requested version bump, changelog/docs, PR and merge.
Release 1.9.0 on the coordinated version line for this additive feature. The
existing one-Codex-review preference applies: request once, fix findings without
another request, and merge only after checks pass. Document the maintenance
window because backend readiness checks enforce the exact Alembic revision.
