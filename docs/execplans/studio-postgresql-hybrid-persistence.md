# Studio PostgreSQL and Redis Hybrid Persistence

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds. This document follows `PLANS.md` at the repository root.

## Purpose / Big Picture

Relayna Studio currently retains its control-plane records in Redis. After this
change, PostgreSQL is the authoritative system of record for durable Studio
data while Redis remains the low-latency, explicitly ephemeral transport for
browser login transactions, sessions, live SSE/pub-sub delivery, caches, and
short-lived coordination. Operators can restart or horizontally scale Studio
without losing service registrations, access membership, settings, retained
events and task projections, health history, notification delivery history, or
the append-only audit trail. The Relayna SDK and service runtime continue to
use Redis only and do not acquire a PostgreSQL dependency.

## Progress

- [x] (2026-08-19 12:05+07:00) Confirmed clean `origin/main` commit `0b44c178c2da11e9a25fdec42f8dd71ef0f3096d` and created `codex/studio-postgres-hybrid` before edits.
- [x] (2026-08-19 12:05+07:00) Read `AGENTS.md`, `PLANS.md`, `production-freeze-guard`, and `implementation-strategy`; recorded the approved compatibility decision.
- [x] (2026-08-19 12:31+07:00) Inventoried all Studio Redis state, API contracts, deployment/version surfaces, and freeze manifests.
- [x] (2026-08-19 15:10+07:00) Implemented async PostgreSQL persistence, initial Alembic schema, repositories, transaction boundaries, outbox relay, advisory-lock worker coordination, readiness, audit logging, and Redis live delivery.
- [x] (2026-08-19 15:10+07:00) Converted durable Studio registry, membership/RBAC, settings, retained events/search/cursors, health, and notification history to PostgreSQL while retaining Redis only for ephemeral responsibilities.
- [x] (2026-08-19 15:42+07:00) Added checksummed idempotent Redis backfill tooling with deleted-service tombstones, validation, and maintenance-window cutover/backup/rollback documentation.
- [x] (2026-08-19 19:15+07:00) Added unit and real-PostgreSQL integration/migration/concurrency/failure-recovery coverage, intentional freeze manifests, synchronized 1.6.0 versions, changelog, Compose topology, and operator documentation.
- [x] (2026-08-19 19:50+07:00) Ran the initial full backend/frontend coverage, migration-cycle, strict-docs, release-metadata, Docker-image, and built-stack Computer Use validation; restart persistence and Redis live delivery passed. A clean final verification pass remains before publication.
- [ ] Commit focused changes, open a draft PR, wait for the first Codex review, address every actionable thread, rerun verification, and resolve addressed threads.

## Surprises & Discoveries

- Observation: `origin/main` is detached at the requested commit and the worktree was clean.
  Evidence: both `git rev-parse HEAD` and `git rev-parse origin/main` returned
  `0b44c178c2da11e9a25fdec42f8dd71ef0f3096d` before branch creation.
- Observation: the newest tag is `v1.4.32`, while repository policy retains
  `v1.4.30` as the strict production-freeze manifest boundary and current main
  already identifies Studio as `1.5.0`.
  Evidence: tag enumeration and `studio/backend/pyproject.toml`.
- Observation: the backend already enforced a stricter 98% coverage threshold,
  and the repository had no PostgreSQL, Alembic, Compose, or database-test
  infrastructure.
  Evidence: `studio/backend/pyproject.toml`, CI, and deployment inventory.
- Observation: pre-1.6.0 Redis event and task history can outlive a deleted
  registry record.
  Evidence: registry deletion removes service keys while event TTLs are
  independent. The importer therefore creates non-visible service tombstones
  to retain those histories and foreign-key integrity.
- Observation: while iterating on the unshipped initial migration, a test
  database that had already recorded revision `0001_studio_postgres` did not
  acquire an index added later to that same revision.
  Evidence: recreating the disposable test database and running upgrade,
  downgrade, and upgrade produced the exact intended schema; `alembic check`
  then reported no pending operations. The revision has never shipped, so no
  production migration lineage was rewritten.
- Observation: the real built UI exercised PostgreSQL-backed services, task
  projections, members, health history, and settings without a frontend API or
  response-shape change.
  Evidence: Gateway Administrator login, `orders-api` registration/refresh,
  live `order-1001` completion delivery, task and tag search, access and failed
  notification settings views, backend restart, and post-restart service/task
  reads all succeeded. PostgreSQL then contained one active service, one
  member, one event, one task projection, one current health row, six health
  history rows, eight audit rows, and zero pending outbox rows; `/readyz`
  returned `{"status":"ready"}`.
- Observation: the first PostgreSQL event-history implementation and its test
  ordered pages by insertion identity, which diverged for out-of-order events
  from the frozen Redis ordering and unknown-cursor behavior.
  Evidence: a direct contract comparison with `RedisStudioEventStore` showed
  that history must sort descending by event time, ingestion time, and dedupe
  key, while an unknown `before` cursor restarts at page one. The PostgreSQL
  query now uses matching keyset pagination and the real-database test asserts
  both cases.

## Decision Log

- Decision: Accept the user-authorized production-freeze break and keep it
  narrowly scoped to Studio persistence, configuration, readiness, deployment,
  and operational tooling. Preserve existing Studio HTTP/frontend response
  shapes wherever possible, and do not change SDK runtime state or dependencies.
  Rationale: the requested durable system-of-record boundary is intentionally
  incompatible with Redis-only Studio deployment, but no SDK behavior needs to
  change.
  Date/Author: 2026-08-19 / Codex.
- Decision: Treat `v1.4.32` as the latest released compatibility comparison and
  `v1.4.30` as the strict freeze manifest boundary. Add PostgreSQL as a required
  Studio backend service and use a short maintenance-window cutover from Redis.
  Rationale: mixed Redis-authoritative and PostgreSQL-authoritative Studio
  versions would permit divergent writes. A maintenance window gives a clear,
  testable ownership transition without a fragile long-lived dual-write mode.
  Date/Author: 2026-08-19 / Codex.
- Decision: Use one PostgreSQL transaction for each durable mutation and write
  an outbox row in the same transaction. A multi-replica-safe relay claims rows
  with row locks and publishes committed live notifications to Redis before
  recording delivery.
  Rationale: this prevents unsafe PostgreSQL/Redis dual writes while retaining
  existing low-latency live behavior.
  Date/Author: 2026-08-19 / Codex.
- Decision: Keep the existing exported `Redis*` store classes and the direct
  `create_studio_app(database_url=None)` helper as compatibility/test tools,
  while requiring PostgreSQL in the production environment settings loader.
  Rationale: this preserves the frozen import surface and existing focused unit
  fixtures without permitting the packaged ASGI production app to start in
  Redis-authoritative mode.
  Date/Author: 2026-08-19 / Codex.
- Decision: Version the synchronized SDK, Studio backend, and Studio frontend
  as 1.6.0 even though SDK code and dependencies are unchanged.
  Rationale: repository policy requires a shared SemVer line, and mandatory
  PostgreSQL plus a maintenance-window cutover is a Studio operator-breaking
  deployment change.
  Date/Author: 2026-08-19 / Codex.

## Outcomes & Retrospective

The implementation and local release validation are complete. PostgreSQL now
owns all requested durable Studio control-plane records; Redis owns only
sessions/login transactions, live publication, and explicitly ephemeral
coordination, while SDK/runtime packages remain Redis-only. The fail-fast
repository verification passed with 678 SDK tests (7 environment-dependent
skips) and 272 Studio backend tests, including 12 real PostgreSQL/Redis
integration tests. SDK coverage is 98%; affected Studio backend coverage is
98.02%. The frontend passed 104 tests and coverage at 98.09% statements,
89.12% branches, 98.46% functions, and 98.01% lines.

Real PostgreSQL passed Alembic upgrade, downgrade-to-base, re-upgrade, and
`alembic check`. Both wheels and Studio images built; the backend image contains
the migration assets and ran `alembic current` at `0001_studio_postgres (head)`.
CI-equivalent dependency, filesystem, Semgrep, and image scans passed. Two
Gitleaks source matches are unchanged origin/main test fixtures; the PR diff
adds no detected source secret.

Computer Use validated the built frontend with the real backend, PostgreSQL,
Redis, development OIDC issuer, and mock service: admin sign-in, registration,
capability/health refresh, SSE delivery, task and tagged-service search, access
and notification settings views, and backend restart persistence all passed.
Publication and the first Codex review remain outstanding; PR/review evidence
and the final residual-risk statement will be appended before handoff.

## Context and Orientation

The Studio backend lives under `studio/backend/src/relayna_studio/`. Its
`app.py` lifespan currently constructs Redis-backed registry, event, health,
search, authentication, and failed-notification stores plus periodic workers.
The React frontend in `apps/studio/` calls the existing `/studio` routes. The
SDK under `src/relayna/` owns service runtime status delivery, leases,
workflows, and dedupe; it is outside this storage change.

PostgreSQL will store durable Studio records and projections. Redis will still
store browser OIDC login transactions and sessions, publish/subscribe channels
for live events, caches, and coordination with bounded lifetime. A
transactional outbox is a PostgreSQL table written atomically with durable
changes and asynchronously relayed after commit, allowing retries without
losing the authoritative mutation.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.32`; strict production-freeze
manifest boundary `v1.4.30`. The user explicitly approved the breaking
production-perimeter change that makes PostgreSQL mandatory for Studio
operators. Existing Studio HTTP responses and frontend contracts will remain
stable wherever possible. New environment variables, readiness dependency,
database schema, migration CLI, and operational procedures are intentional.
The SDK package and its Redis keys, serialized runtime state, RabbitMQ wire
behavior, and public imports remain unchanged.

The relevant backend and frontend freeze manifests will be updated only when
an externally visible surface actually changes, with this note and the final PR
description documenting each intentional addition. No manifest will be changed
merely to silence a test.

## Plan of Work

Add async SQLAlchemy with the async PostgreSQL driver and Alembic to the Studio
backend package. Introduce database lifecycle/session infrastructure and
relational models for services, members, settings, retained control-plane
events, task/service projections, pull cursors, health snapshots/history,
notification delivery/dedupe state, audit entries, and the outbox. Use
relational columns and indexes for service/environment/base URL, task,
correlation, status, stage, timestamps, retention, audit actor/action, and
dedupe queries; reserve JSONB for variable configuration and event payloads.

Refactor existing service-layer protocols so route models and response shapes
remain stable while PostgreSQL repositories replace durable Redis stores.
Authentication login/session transactions stay in Redis, but member reads and
mutations move to PostgreSQL. Event mutations, registry/RBAC/settings changes,
notification state, and other material operator actions append audit/outbox
records in their database transaction. Periodic work uses PostgreSQL advisory
locks or skip-locked claims so multiple replicas do not duplicate work.

Add an Alembic initial migration and CLI/container operations. Add an
idempotent Redis-to-PostgreSQL backfill command that imports only Studio-owned
durable keys, validates counts/checksums/invariants, and never reads or mutates
SDK runtime keys. Document a backup-first maintenance window: stop old Studio
writers, back up Redis and PostgreSQL, migrate schema, run and validate import,
start only the new version, then retain the Redis backup through the rollback
window. Rollback restores PostgreSQL backup and the preserved pre-cutover Redis
snapshot before restarting the old version; mixed old/new replicas are not
supported.

Add focused unit tests and Docker-backed PostgreSQL integration coverage for
migrations, constraints/indexes, repository behavior, concurrent idempotency,
outbox retry/recovery, worker coordination, retention, audit immutability,
backfill reruns/validation, and upgrade/downgrade/upgrade. Keep backend coverage
at or above its configured 98% threshold. Update Studio deployment examples,
Docker configuration, operations/architecture/backup/cutover docs, versions,
release metadata, changelog, and intentional freeze manifests.

Finally start the built local PostgreSQL, Redis, backend, frontend, and safe
development OIDC fixture. Use the Computer Use skill to exercise sign-in,
member/RBAC, registry, search/task and settings flows, verify live updates, then
restart Studio and confirm durable data remains. Capture fresh accessibility
state and screenshots after interactions. Publish a draft PR, request the first
Codex review, address all actionable feedback, and resolve replied threads.

## Concrete Steps

From the repository root:

    make -C studio/backend sync
    make -C studio/backend format
    make -C studio/backend lint
    make -C studio/backend typecheck
    make -C studio/backend test
    make -C studio/backend coverage
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build
    make studio-backend-docker-build
    make studio-frontend-docker-build

Run Alembic against a real local PostgreSQL container:

    uv run --directory studio/backend alembic upgrade head
    uv run --directory studio/backend alembic downgrade base
    uv run --directory studio/backend alembic upgrade head

Exact Docker Compose and backfill/validation commands will be recorded after
the repository's deployment conventions are inventoried.

## Validation and Acceptance

A clean PostgreSQL database upgrades to head, downgrades to base, and upgrades
again. Every required constraint and index is asserted from real PostgreSQL.
Concurrent duplicate event ingestion creates one durable event; concurrent
workers claim work once; a Redis publication failure leaves retryable outbox
state and a later relay publishes it. A committed durable mutation survives
backend and Redis restarts. OIDC login/session data remains Redis-only and
expires. Studio members, settings, registry, retained events/search, cursors,
health history, notification delivery/dedupe, and audit data survive restarts.
No SDK dependency or runtime persistence test changes to PostgreSQL.

The Redis importer is safe to rerun, imports only configured Studio prefixes,
reports validation totals/checksums, and does not alter source keys. Cutover and
rollback procedures are complete and tested where automation permits. Existing
backend API and frontend regression suites pass, affected Studio backend
coverage remains at least 98%, production builds succeed, and the real browser
confirms major flows, live updates, and restart persistence.

## Idempotence and Recovery

Alembic upgrade commands and the Redis importer are retryable. Import rows use
stable natural/dedupe keys and conflict-safe inserts or validated updates.
Outbox delivery uses explicit attempt state and retry scheduling; an interrupted
relay can safely reclaim unfinished rows. Periodic-worker leadership/claims
expire or release on connection loss. Tests use isolated databases and Redis
prefixes. Failed verification commands can be rerun.

Operators must not run old Redis-authoritative Studio replicas beside the new
PostgreSQL-authoritative release. Before cutover, preserve a Redis snapshot and
PostgreSQL backup. Rollback stops all new replicas, restores the pre-cutover
state, and only then starts the old version; new-version writes after cutover
cannot be reconstructed in the old Redis format unless a separately documented
reverse export is used.

## Artifacts and Notes

Verification transcripts, migration outputs, coverage totals, Computer Use
flows/screenshots, PR URL, and review-thread evidence will be added as work
progresses.

## Interfaces and Dependencies

The Studio backend will add a required PostgreSQL DSN environment variable, an
async production PostgreSQL driver, SQLAlchemy async persistence, and Alembic.
Database connection readiness will be distinct from liveness. Redis remains a
required Studio dependency for browser auth/session and live delivery but is no
longer authoritative for durable Studio records. Repository/service protocols
will preserve existing Pydantic route contracts. The SDK's dependency list and
runtime construction will not include PostgreSQL libraries or settings.
