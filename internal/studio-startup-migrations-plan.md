# Automatic Studio database migrations

This ExecPlan follows `PLANS.md` and is a living document.

## Purpose / Big Picture

Deploying a new Studio backend should apply its bundled PostgreSQL migrations
before serving traffic, like `relayna-gateway`'s `PostgresStore::connect`.
Operators should not need a separate migration job for normal upgrades.

## Progress

- [x] (2026-10-27) Inspect gateway startup migrations, Studio lifespan, deployment assets, and freeze manifests; read implementation-strategy and production-freeze-guard.
- [x] (2026-10-27) Add automatic migrations, shared PostgreSQL serialization, and startup failure coverage; update Compose and deployment docs.
- [x] (2026-10-27) Verify empty/existing databases, concurrent starts, rollback on failure, CLI migration cycle, and installed-wheel migrations outside the checkout.
- [x] (2026-10-27) Run code-change-verification: SDK 704 passed/9 optional skips; backend 671 passed with PostgreSQL and Redis enabled. Prepare PR draft for `t3/studio-auto-db-migrations`.
- [x] (2026-10-27) Coordinate 1.13.0 versions, changelog, release notes, and locks. Repeat verification: SDK 704 passed/9 optional skips, backend 671 passed with PostgreSQL/Redis, frontend 370 passed and build passed on Node 24.22.1, docs build passed, wheel/sdist builds and Twine checks passed, installed 1.13.0 wheel migrations passed outside checkout.
- [x] (2026-10-27) Obtain one independent read-only Codex review: no actionable findings. Reviewer independently ran focused tests (9 passed, 4 PostgreSQL skips); parent ran real-service integration tests.
- [ ] Commit, push, and open PR.
- [ ] Monitor PR checks/reviews, merge when ready, and publish v1.13.0 with the existing release workflow.

## Surprises & Discoveries

Studio already bundles Alembic scripts in its backend image and wheel, but only
Compose invokes them automatically through a separate service. The application
currently rejects a missing or outdated schema. Alembic's environment calls
`asyncio.run`, so startup must supply the existing SQLAlchemy connection rather
than nest another event loop. Alembic uses process-global context proxies, so
concurrent startup tasks must acquire the database lock before invoking Alembic.

## Decision Log

- (2026-10-27, Codex) Treat the user's request as authorization for the narrow
  change from manual to automatic application of existing migrations. Keep the
  v1.4.30 freeze perimeter intact: no public exports, signatures, route shapes,
  environment variables, or new schema revisions; no manifest updates.
- (2026-10-27, user/Codex) The user explicitly approved breaking the production
  freeze for this task. The implementation still needs no public export,
  contract, or manifest changes; authorization covers the startup behavior.
- (2026-10-27, user/Codex) The user authorized versioning, one additional Codex
  review, PR publication/monitoring, merge, and release using the previous
  template. Select minor 1.13.0 for the new deployment behavior and coordinate
  all package versions and backend SDK floor. Keep v1.12.0 contract manifests
  unchanged because no contract entries change.
- (2026-10-27, Codex) Compare released behavior with latest tag v1.12.0. Add a
  private migration runner invoked by the shared application lifespan. Resolve
  scripts relative to the installed package, without depending on a source
  checkout, working directory, or alembic.ini. Share a transaction-level advisory
  lock with manual Alembic commands; commit the upgrade atomically and release
  the lock on failure. Preserve explicit migration commands and Redis backfill.
- (2026-10-27, Codex) Remove the redundant Compose migration service, making
  startup the default across deployment methods. Document DDL privileges,
  startup probe budgets, backup, and coordinated rollback.

## Outcomes & Retrospective

Studio now applies existing bundled migrations before schema validation,
authentication, or workers. Compose uses this shared startup path without a
separate migration container. CLI migrations and startup share a transaction
advisory lock. No schema revisions, freeze manifests, dependencies, or public
contracts changed. Database DDL privileges and startup time are deployment
requirements, and legacy Redis import remains explicit.

Validation completed: all eight commands in code-change-verification passed,
including 704 SDK tests (9 optional skips) and 671 backend tests with real
disposable PostgreSQL/Redis. The focused migration/deployment suite passed 13
tests. The CLI upgrade/downgrade-base/re-upgrade cycle passed on disposable
PostgreSQL. Both wheels built; an isolated installed wheel outside the checkout
successfully migrated a fresh database, checked the exact schema head, and
repeated startup. Compose configuration and `git diff --check` passed.

Release preparation and local verification are complete for 1.13.0. The one
additional Codex review reported no actionable findings. No application
environment is being deployed; PR checks and merge/tag publication will use
the existing GitHub workflows.

## Context and Orientation

`studio/backend/src/relayna_studio/app.py` owns the FastAPI lifespan that checks
PostgreSQL, Redis, and the schema before starting workers. `database.py` owns the
SQLAlchemy engine and exact expected schema revision. The `migrations/` directory
contains the existing Alembic revision chain and `env.py`. `studio/compose.yaml`
currently gates backend startup on a separate migration container. SDK code under
`src/relayna/` and the frontend under `apps/studio/` need no runtime changes.

## Compatibility Boundary

The production freeze boundary is v1.4.30; latest release comparison is v1.12.0.
The approved behavior change is startup upgrading an existing database to the
bundled head. Existing persisted layouts and revision history remain unchanged.
Database credentials now need DDL privileges during normal startup. Manual
pre-deployment upgrades remain supported. Redis import remains explicit.

## Plan of Work

Add `_migrations.py` to run Alembic against an existing async engine under a
PostgreSQL transaction advisory lock. Teach `migrations/env.py` to accept the
supplied sync connection and serialize CLI operations under the same lock.
Call the runner before `check_schema` in the lifespan. Remove Compose's
redundant migration service and update persistence/deployment guidance. Add
tests for lifecycle ordering/failure, actual migration concurrency, idempotence,
data-preserving upgrades, and transactional failure recovery.

## Concrete Steps

From the repository root, use focused backend pytest commands during iteration.
Run disposable PostgreSQL/Redis containers for integration checks; no production
database or deployment is touched. Finish with:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C studio/backend build

Inspect the built wheel and run migrations using an isolated installation from
outside the checkout to verify packaged script resolution.

## Validation and Acceptance

An empty database reaches the application head; a database at an earlier
revision preserves its existing records; repeat and concurrent runs succeed.
A failed upgrade rolls back and permits retry. Lifespan tests demonstrate that
migrations precede schema checks, Redis, authentication, and workers, and failure
prevents serving while disposing resources. CLI upgrade/downgrade/re-upgrade
continues to work. All mandatory format/lint/type/test steps pass.

## Idempotence and Recovery

Alembic records applied revisions and repeat startup is a no-op. PostgreSQL
releases transaction locks on commit, rollback, or disconnect. Failed migrations
abort startup and roll back transactional DDL; operators fix the cause and retry.
Unknown/newer revisions fail rather than downgrade. Back up before upgrades,
budget startup time for locks/DDL, and stop new replicas before coordinated
schema rollback or restoring the matching backup and image. Startup never
downgrades or imports legacy Redis records.

## Artifacts and Notes

Reference: `/Users/jobz/Works/relayna-gateway/crates/gateway-store/src/postgres.rs`
calls `MIGRATOR.run(&pool).await?` in `PostgresStore::connect` before returning.

## Interfaces and Dependencies

Use the existing Alembic, SQLAlchemy asyncio, and asyncpg dependencies. Migration
helpers stay private. Existing database settings and public Studio factories
retain their signatures. No freeze manifests change.
