# Studio Recovery Center

This living ExecPlan follows `PLANS.md`. Maintain Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective as work proceeds.

## Purpose / Big Picture

An administrator can pin up to 200 failure snapshots from one service and
environment, preview eligibility and payload/queue overrides, dispatch a chosen
small batch, inspect observed outcomes, and release remaining items at a bounded
rate. Pause/resume and stop affect only new dispatch. The durable report links
original and retried tasks and separates requested, accepted, observed success,
observed failure, pending observation, and unknown acceptance/outcome.

## Progress

- [x] (2026-10-25) Fetch origin/main; confirm clean feat/studio-recovery-center
  starts exactly at origin/main, 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
- [x] (2026-10-25) Read implementation-strategy and production-freeze-guard;
  inspect retry, authentication, audit, persistence, and frontend entry points.
- [x] (2026-10-25) User explicitly approved: "You can break production freeze".
- [x] (2026-10-25) Implement ledger migration, execution/reconciliation, API, and backend tests.
- [x] (2026-10-25) Implement UI, failure selection handoff, and frontend tests.
- [x] (2026-10-25) Update operational documentation and intentional freeze manifests.
- [x] (2026-10-25) Run code-change-verification, frontend checks, real PostgreSQL
  concurrency/restart tests, migration cycle, and desktop/mobile visual checks.
- [x] (2026-10-25) Backend coverage: 555 tests, 98.13%; frontend coverage:
  284 tests, all configured gates pass (98.03% statements, 89.14% branches,
  99.39% functions, 98.70% lines). Wheel, frontend, and strict docs builds pass.
- [x] (2026-10-25) Final code-change-verification passes after the last regression
  tests: SDK 688 passed/9 skipped; backend 555 passed. Frontend coverage 284
  passed, final frontend build and strict documentation build pass.
- [ ] Commit/push, open and register the PR, request at most one Codex review,
  notify coordination, and enable T3 watching.

## Surprises & Discoveries

- T3 preview snapshot timed out twice with "Preview automation snapshot timed
  out after 15000ms." Playwright CLI completed the desktop/mobile live checks.
- CI runs strict coverage gates in addition to ordinary tests. Added meaningful
  editor, transport, control, pagination, and stale-scope tests until all gates
  passed; did not lower thresholds.

- The latest release is v1.11.0, identical to the starting checkout. Instructions
  retain v1.4.30 as the policy freeze boundary; current test manifests identify
  v1.10.1. Preserve those existing labels and record the approved additions.
- The existing SDK retry can reuse the original task ID, has no idempotency
  key, and publishes before persisting its retry receipt. A timeout/crash can
  therefore never justify an automatic resend. An absent receipt does not prove
  nonacceptance. Old terminal status evidence must never count as recovery.
- PostgreSQL is optional in local Studio. Recovery execution requires it and
  must return an explicit unavailable response in Redis-only mode.

## Decision Log

- Decision: retryable preflight reads and a revoked executing actor pause with
  the item still pending, because no mutation has begun; an administrator can
  resume after repair. Eligibility/snapshot changes block the item. This is
  distinct from committed dispatch intent, which can never return to pending.
  Date/Author: 2026-10-25, Codex.
- Decision: record only timestamp baselines and compact observed event evidence
  in the ledger; full task evidence remains accessible by stable task links.
  Rationale: avoid multiplying retained task payloads across the recovery ledger.
  Date/Author: 2026-10-25, Codex.

- Decision: keep all orchestration in Studio; preserve existing SDK contracts,
  upstream retry behavior, and current individual retry endpoint.
  Rationale: independent feature; no dependency on Incident Inbox or SDK changes.
  Date/Author: 2026-10-25, Codex.
- Decision: additive PostgreSQL plan documents with per-failure reservations,
  short row-lock transactions, and a committed dispatch intent before network I/O.
  A dispatch intent is never returned to pending after restart. Concurrent
  instances share the same claims and rate deadline; overlapping plans cannot
  claim the same failure snapshot. Control and result writes reload under lock.
  Rationale: prevent silent duplicate sends without claiming upstream exactly-once.
  Date/Author: 2026-10-25, Codex.
- Decision: reconcile ambiguous sends through an exact unique retry-note marker
  in the retained failure receipt. Preserve unknown when the marker cannot be
  found; never infer acceptance from somebody else's retry. Observe replacement
  task status only with evidence newer than the retry receipt and baseline.
  Rationale: acceptance is distinct from business completion, including reused IDs.
  Date/Author: 2026-10-25, Codex.
- Decision: allow the source queue and, for explicit queue overrides, current
  workflow stage queues advertised by the same service; revalidate at dispatch.
  Require existing administrator authorization, active service, supported retry
  capability, pinned environment/base URL, current failure eligibility/payload,
  and unchanged snapshot/previous retry receipt. No new operator roles.
  Date/Author: 2026-10-25, Codex.
- Decision: production-perimeter changes approved in this thread by the user.
  Narrow additions: recovery routes, frontend page/types/API, ledger tables and
  migration, worker lifecycle, and matching freeze manifest entries. Existing
  response shapes and SDK/Redis/RabbitMQ formats remain compatible.
  Date/Author: 2026-10-25, Codex and user.

## Outcomes & Retrospective

Recovery Center is implemented as an additive Studio feature with durable
PostgreSQL execution and explicit unknown outcomes. Real database tests prove
concurrency, restart, ambiguous acceptance, authorization, pinning, and stale
control behavior. The live six-item demo paused at five sends and distinguished
four observed successes from one observed failure. Desktop and 390px mobile
inspection showed readable layouts and no horizontal overflow. Backend and
frontend coverage gates pass without changing thresholds.

The user subsequently authorized committing, pushing, opening a PR against main,
one additional Codex review, and event-driven monitoring. No merge is authorized.
The originating coordination thread owns final restacking in this order:
main <- 01 Incident Inbox <- 02 Recovery Center <- 03 Queue & Worker Cockpit <-
05 Reliability Goals & Alert Routing. Keep 0003_recovery intact until that
integration. PR URL/head and review/check progress remain to be recorded.

## Context and Orientation

Relayna's SDK (`src/relayna/`) runs service-side tasks and retry publication.
Studio is the separate control plane: `studio/backend/src/relayna_studio/app.py`
owns runtime lifecycle, `federation.py` proxies service reads/mutations,
`database.py` owns SQLAlchemy metadata and audit records. Alembic migrations
live in `studio/backend/src/relayna_studio/migrations/versions/`.
`apps/studio/src/pages/FailedTasksPage.tsx` provides failure selection and
`TaskDetailPage.tsx` provides stable task investigation links. Recovery Center
adds a page and routes through `App.tsx`, `api.ts`, `types.ts`, and `ui.tsx`.

## Compatibility Boundary

Policy freeze boundary: v1.4.30. Released behavior comparison: v1.11.0.
This is an explicitly approved additive Studio feature and PostgreSQL schema
migration. Keep existing route response shapes, public SDK exports/signatures,
service retry requests, and persisted upstream data unchanged. Existing plans
and audit logs are durable; migration downgrade intentionally deletes recovery
history and must occur only after workers are stopped and history is exported.

## Plan of Work

Add private recovery contracts, a PostgreSQL repository using the shared metadata,
and a lifecycle worker in Studio. Keep an immutable pinned cohort/options and
durable item progress. Store dispatch intent and audit atomically; send once
through federation; reconcile receipts and terminal evidence in later ticks.
Use row locks to serialize controls and updates. Record batch allowance and a
persisted next-dispatch deadline so replicas cannot bypass limits.

Add preview/create/list/detail/control routes under `/studio/recovery`, protected
by existing auth middleware and audited through the ledger. The frontend offers
exact selection, queue/JSON overrides, explicit preview and batch confirmation,
progress, controls, and a linked report. Add failure-selection handoff without
depending on future incident groups. Document limitations, operational rollout,
unknown reconciliation, and cohort recovery timing/repeated-failure measures.

## Concrete Steps

From this worktree run focused backend/frontend tests during implementation.
Finish with:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Use isolated disposable PostgreSQL/Redis instances for recovery integration tests
and Alembic upgrade/downgrade/upgrade, leaving other worktrees' services untouched.

## Validation and Acceptance

Tests prove exact pinning/eligibility/queue validation, authorization and audit,
bounded canary and remaining release, pause/resume/stop races, overlapping cohort
rejection, multi-instance claims, committed-intent restart, timeout reconciliation,
unknown preservation, no automatic resend, old-status rejection, replacement
links, observed recovery time and replay failure counts. Frontend tests exercise
preview, controls, readonly mode, failures, and accepted-versus-observed labels.
Browser inspection checks desktop/mobile usability and empty/partial states.

## Idempotence and Recovery

Alembic upgrade is rerunnable. Execution controls are serialized by row locks;
duplicate control requests cannot increase allowance while already running.
Creation uses a client request identifier so response loss can be retried without
creating a second cohort. Reserved failure IDs cannot silently enter another
plan. Pending items survive restart; interrupted intents become unknown and are
reconciled, never resent. Pause/stop do not undo upstream side effects or claimed
requests already in flight. Schema rollback is destructive to recovery history.

## Artifacts and Notes

The initial git fetch verified HEAD == origin/main ==
89954699d2e43aebfee54e4ec64c06edb0caf2ce. Implementation stays in this checkout.

## Interfaces and Dependencies

Reuse FastAPI/Pydantic, SQLAlchemy asyncpg, Alembic, existing federation and
administrator authentication/audit facilities, React Router, and Studio UI
components. No SDK changes, new external service, or new credential required.
Recovery requires the existing `RELAYNA_STUDIO_DATABASE_URL` and current schema.
