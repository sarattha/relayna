# Fix Studio PostgreSQL refresh and retention contention

This living ExecPlan follows [repository PLANS.md](https://github.com/sarattha/relayna/blob/main/PLANS.md).

## Purpose / Big Picture

Routine Studio capability/health refreshes must not rewrite or lock all retained
tasks when their service name and environment have not changed. PostgreSQL
retention must delete expired task projections in short, bounded transactions
without reading every live task. A background worker must retain its session
advisory lock without keeping a database transaction open during upstream waits.
A projection is Studio's durable searchable task metadata.

## Progress

- [x] (2026-10-31) Reproduced unchanged refresh rewriting 100,000 task rows and
  naturally blocking a retention DELETE on `Lock / transactionid`. Confirmed
  retention's 1,001 queries over 1,000 live tasks and coordinator transactions.
- [x] (2026-10-31) Read implementation-strategy, production-freeze-guard,
  code-change-verification, pr-draft-summary, and the latest release boundary.
- [x] (2026-10-31) Added 15 real-PostgreSQL regression cases. Against the
  original `HEAD` source, 11 fail and four existing-good controls pass.
- [x] (2026-10-31) Implemented the three internal fixes and documented their
  operational behavior. The existing integration target passes all 61 cases,
  including the 15 new cases, against disposable PostgreSQL and Redis.
- [x] (2026-10-31) Ran the mandatory verification script successfully: SDK
  formatting, lint, type checking, and 704 passed/9 skipped tests; backend
  formatting, lint, type checking, and all 686 tests passed with real services.
- [x] (2026-10-31) Backend coverage gate passed at 98.21% (686 tests), and
  backend wheel build succeeded. Repeated the 100,000-task workload with zero
  task UPDATE statements/tuple replacements, one retention SQL statement, and
  an idle coordinator connection with `xact_start IS NULL`.
- [x] (2026-10-31) Reviewed the final diff, verified no freeze/schema changes,
  prepared the PR handoff, and removed this task's disposable containers.

## Surprises & Discoveries

- `PostgresServiceRegistryStore.update` always invokes a task-wide metadata
  UPDATE, including on capability refresh where only last-seen/status changes.
- Session advisory locks do not require an open database transaction. The
  current coordinator implicitly starts one for its lock SELECT.
- PostgreSQL already has an expiry index on task projections. Retention can use
  it directly instead of applying the Redis-oriented per-document loop.
- Loki TCP failures returned 502 with zero PostgreSQL pool checkouts in the
  real local control. The outbox's multi-minute production I/O wait was not
  reproduced. Neither is a justification for speculative changes here.
- The regressions also exposed a session-lock cleanup issue: an exception after
  acquiring the server lock or before unlocking could return the still-locked
  connection to the pool. Both error paths now invalidate that connection, and
  real PostgreSQL lock inspection confirms cleanup. Cancellation and pool
  isolation restoration are covered too.
- The initial root SDK environment lacked development dependencies. `make sync`
  installed them; the full verification script was then rerun from the start.
  Backend type checking reports two pre-existing redundant-cast warnings and
  exits successfully. No unrelated warnings or source were changed.

## Decision Log

- Compatibility: latest release tag `v1.13.0`, strict freeze perimeter
  `v1.4.30`. Preserve SDK/Studio public exports, function signatures, routes,
  response shapes, existing tables/indexes, expiry meaning, and session lock
  names. No migration or freeze-manifest update. This user-authorized fix changes
  internal execution only. Date/author: 2026-10-31/Codex.
- Serialize registry metadata comparison on the service row already owned by
  that update transaction. Propagate real name/environment edits atomically,
  and skip task SQL entirely for unchanged metadata. Date/author:
  2026-10-31/Codex.
- Add a private PostgreSQL pruning path with bounded `FOR UPDATE SKIP LOCKED`
  batches. Recheck expiry at deletion; defer concurrently locked rows to the
  next pass. Keep Redis pruning unchanged. Date/author: 2026-10-31/Codex.
- Use AUTOCOMMIT for session advisory-lock connections and invalidate the
  connection if acquire/release fails, preventing uncertain session locks from
  returning to the pool. Cover normal contention, errors, and cancellation.
  Date/author: 2026-10-31/Codex.

## Outcomes & Retrospective

The reproduced contention is fixed. Unchanged service metadata avoids task SQL
entirely, while genuine name/environment edits remain atomic and propagate to
task projections. Expiry cleanup uses bounded short transactions and skips
concurrently locked rows; the next pass rechecks expiry and preserves refreshed
or non-expiring tasks. Session advisory locks retain mutual exclusion without
an open PostgreSQL transaction and are cleaned up on cancellation and errors.

Fifteen new integration cases run in the existing PostgreSQL CI job and existing
`integration` target. Eleven fail against original `HEAD` code; all pass with the
fix. The full SDK/backend verification stack, 98% backend coverage gate, and
wheel build pass. The large workload independently confirms zero task writes
for unchanged refresh, one SQL statement over 100,000 live tasks for retention,
and zero checked-out connections after work.

Production-length storage stalls and sustained CPU attribution remain outside
the reproduced evidence. No speculative Loki/outbox changes, public API changes,
schema migrations, or freeze-manifest updates were introduced. Evidence is
retained under ignored `reports/studio-postgres-fix-20261031/`; these experiments
used only this task's disposable databases.

## Context and Orientation

`studio/backend/src/relayna_studio/database.py` owns PostgreSQL registry,
projection, and coordinator operations. `search.py` provides the shared search
service and periodic retention worker. Integration fixtures in
`studio/backend/tests/test_postgres_persistence_integration.py` use disposable
PostgreSQL and Redis URLs from `RELAYNA_STUDIO_TEST_DATABASE_URL` and
`RELAYNA_STUDIO_TEST_REDIS_URL` and truncate only the configured test database.

## Plan of Work

Add regressions to the existing PostgreSQL integration module so the existing
`make -C studio/backend integration` target includes them. Prove unchanged
refresh does not change tuple versions or block on task row locks, actual
metadata edits still propagate, pruning uses bounded SQL and preserves live or
concurrently refreshed tasks, and coordinator sessions are idle without a
transaction and release locks on every exit. Include Loki connection-error
regressions with real PostgreSQL registry sessions.

In `database.py`, compare old service metadata under a row lock, conditionally
propagate changed metadata, implement private batched expiry pruning, and keep
advisory operations outside transactions with safe cleanup. In `search.py`,
dispatch to the PostgreSQL private pruning path and retain the Redis fallback.
Document the operational behavior in `docs/studio-persistence.md`.

## Concrete Steps and Validation

Start separately named disposable PostgreSQL 17 and Redis 7 containers on random
loopback ports. Apply actual Studio migrations to that database. Keep synthetic
test connection settings under ignored `reports/studio-postgres-fix-20261031/`.
From the repository root, run the new tests before and after implementation:

    uv run --project studio/backend pytest studio/backend/tests/test_postgres_persistence_integration.py -k contention
    make -C studio/backend integration
    bash .codex/skills/code-change-verification/scripts/run.sh

Require unchanged task `xmin` on no-op metadata refresh, correct metadata on
renames, no blocking while an unrelated task row is locked, bounded delete
batch sizes with separate commits, no removal of live/non-expiring/refreshed
tasks, and `xact_start IS NULL` while coordinator work remains pending.

## Idempotence and Recovery

Tests use fresh disposable services, never existing developer/production
databases. Re-run migrations and regressions safely on those services. Remove
only this task's containers after verification, including on failures. Preserve
all unrelated user edits; the initial tracked worktree was clean. The user
subsequently requested opening a PR, authorizing the commit and branch push
needed for that handoff. Deployment is outside this task.
