# Studio Incident Inbox

This ExecPlan is a living document maintained according to [PLANS.md](../../PLANS.md).

## Purpose / Big Picture

An operator can identify the largest cohort of terminal failures without reading
every task, inspect representative evidence, and open the existing task
investigation workspace. Studio owns acknowledgement, owner, resolution, and
manual grouping corrections independently of worker retry/investigation state.
Grouping is a triage aid and does not establish a shared root cause.

## Progress

- [x] (2026-10-25) Read repository policies, implementation-strategy, and production-freeze-guard.
- [x] (2026-10-25) Fetched origin/main and tags; clean feat/studio-incident-inbox HEAD equals origin/main at 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
- [x] (2026-10-25) Inspect failure federation, PostgreSQL storage, auth, existing pages, and freeze tests; prepare perimeter proposal.
- [x] (2026-10-25) Received explicit user approval: “Approve these Studio-only additions” and “You can break production freeze.”
- [x] (2026-10-25) Implement grouping, bounded coverage, durable lifecycle, split/merge, auth checks and PostgreSQL behavior tests.
- [x] (2026-10-25) Implement responsive Incident Inbox, investigation links, and frontend behavior coverage.
- [x] (2026-10-25) Document operator semantics and migration; update only approved Studio freeze entries.
- [x] (2026-10-25) Run code-change-verification (688 SDK passed/9 skipped, 546 backend passed), frontend test/build (289 passed), coverage gates, PostgreSQL migration cycle/integration, and desktop/mobile browser QA.
- [x] (2026-10-25) Review final diff and prepare the PR summary after green verification.
- [ ] Commit/push, open/link the feature PR, notify coordination, and enable event-driven monitoring. User authorized this follow-up on 2026-10-25; remote state is recorded in the thread.

## Surprises & Discoveries

- The latest fetched release is v1.11.0. The policy declares v1.4.30 as the
  strict boundary while the current manifest headers say v1.10.1 and include
  approved later feature additions. Preserve those headers and existing entries.
- CI enforces whole-source coverage (backend 98%; frontend 98% statements and
  89% branches), in addition to mandatory test/build checks. Added retained-state,
  pagination, history, and stale navigation behavior coverage to meet those gates.
- Failure federation already provides per-service cursor pagination, normalized
  task references, exception type/message, queue, and failure timestamps.
  Environment comes from the Studio registry. No SDK change is needed.
- PostgreSQL is authoritative in production; schema readiness requires the exact
  Alembic head. Durable incident state must not use Redis-only storage.

## Decision Log

- Decision: follow the later user authorization to commit/push and open a PR
  against main after verification; request at most one additional Codex review,
  report to coordination thread 18e948a2-67d9-49b6-91ad-58ee29029145, and use
  T3 PR watching. Final stack order is main <- 01 <- 02 <- 03 <- 05; feature 05
  is the fourth layer, and no feature 04 is requested. Stack assembly stays with
  coordination; do not merge.
  Date/author: 2026-10-25, user/Codex.
- Decision: preserve current URL filters when an in-flight mutation completes,
  while ignoring responses after selection/environment changes. Added navigation
  regressions, including filter changes during a save. Date/author: 2026-10-25, Codex.
- Decision: serialize discovery snapshots with correction writes so PostgreSQL
  READ COMMITTED cannot pair old incident rows with newly split overrides.
  Added a real concurrent reader/writer regression test. Date/author: 2026-10-25, Codex.

- Decision: proceed with the five Studio routes, new frontend page/API/types,
  additive PostgreSQL tables/migration, and matching manifest entries. User
  explicitly approved these additions and permitted breaking the production
  freeze. Keep changes limited to this feature. Date/author: 2026-10-25, user/Codex.

- Decision: compare compatibility against fetched v1.11.0 and record v1.4.30 as
  the strict policy boundary. Add new Studio contracts; preserve existing routes
  and SDK/runtime contracts. Date/author: 2026-10-25, Codex.
- Decision: compute counts from the current bounded upstream scan, not cumulative
  observations. Persist lifecycle, group identity, and manual corrections only.
  This avoids presenting expired/retried evidence as a current failure count.
  Date/author: 2026-10-25, Codex.
- Decision: deterministic grouping uses environment, service ID, queue, stage
  when supplied, exception type, and a versioned normalized error signature.
  Normalize UUIDs, hexadecimal IDs, standalone numbers, and whitespace; keep
  raw representative messages available. Missing messages fall back to a
  failure-specific key so unrelated unknown errors do not collapse.
  Date/author: 2026-10-25, Codex.
- Decision: support manual split of selected observed failure IDs and merge
  within one environment/service. A split affects those exact records; merge
  redirects future matches of the source group. Target lifecycle is preserved,
  source history remains accessible, and corrections are audited. Require
  revision checks to reject stale concurrent edits. Date/author: 2026-10-25, Codex.

## Outcomes & Retrospective

The user approved the Studio-only perimeter additions on 2026-10-25. Backend,
persistence, and interface implementation are complete. SDK contracts remain unchanged.
Coverage gates pass (backend 98.23%; frontend statements 98.01%, branches
89.45%, functions 99.24%, lines 98.71%). PostgreSQL upgrade/downgrade/upgrade
and Alembic metadata drift checks pass. Desktop 1440px and mobile 390px show no
horizontal page overflow, including dark mode. Browser QA saved an owner and
acknowledgement, preserved them on reload, exposed three representative links,
and switched environment without retaining the previous detail. Implementation is ready for review. The later user authorization adds PR
creation, one extra Codex review request at most, coordination notifications,
and event-driven monitoring. Remote CI/review state is tracked by this thread.

## Context and Orientation

The SDK under src/relayna runs inside user services and owns task processing,
RabbitMQ queues, Redis status, and runtime commands. Studio is a separate
control plane: studio/backend/src/relayna_studio/federation.py reads registered
services, database.py owns SQLAlchemy metadata and audit transactions, and
app.py wires routers and existing admin/readonly plus CSRF enforcement.
apps/studio/src/pages/FailedTasksPage.tsx shows individual terminal failures;
OverviewPage.tsx shows service health signals. The new inbox will complement
both and reuse task routes, health context, frontend theme, and scoped links.

## Compatibility Boundary

Strict policy boundary: v1.4.30. Latest release comparison: v1.11.0.
Requested approval is limited to additive Studio changes:

1. GET /studio/incidents for bounded grouped evidence and coverage; GET
   /studio/incidents/{incident_id} for evidence and durable history; POST
   /studio/incidents/{incident_id} for owner and lifecycle changes; POST
   /studio/incidents/{incident_id}/split and /merge for manual corrections.
2. A new /incidents frontend page, navigation/overview entry, and the associated
   frontend API functions and TypeScript types.
3. Additive PostgreSQL tables for incident identity/state, per-failure overrides,
   and append-only lifecycle/correction history, plus a new Alembic revision.
   Existing audit rows record incident operations in the same transaction.
4. Only corresponding backend route/feature and frontend symbol/page freeze
   manifest entries. No SDK manifest, package-root exports, existing response
   shape, runtime command, queue, Redis wire format, or configuration addition.

Existing installations need alembic upgrade head before deploying this backend.
Downgrade removes only incident data; backup/export it before downgrading.
No compatibility shim is needed for brand-new Studio-owned contracts.

## Plan of Work

Implement internal incident modules for conservative normalization, per-service
scan budgets/timeouts with bounded concurrency, grouping, and PostgreSQL state.
Use upsert-on-discovery without changing lifecycle on refresh. Persist no task
payload, traceback, or historical failure counts. Keep stored incidents visible
when current evidence is absent, and distinguish absent evidence from an
unavailable service. First/last seen and trend refer to the scanned evidence,
with missing timestamps reported explicitly. Return coverage for every scoped
non-disabled service: complete, truncated, or unavailable, errors and observed
record counts. The default scan budget is 20 pages of 50 records per service;
counts are exact only within that scan, and unavailable/truncated sources always
make the overall query incomplete. Dedupe failure IDs per service.

Add durable revision-controlled lifecycle mutations (open, acknowledged,
resolved, explicit reopen), owner text, optional note, authenticated actor, and
history. Resolution does not retry tasks, clear failures, or automatically
reopen on refresh. Serialize grouping corrections transactionally, validate
observed split membership and merge scope, and reject stale revisions with 409.

Add apps/studio/src/pages/IncidentInboxPage.tsx with largest-cohort ordering,
environment/status filters, coverage notices, trend/time/affected-task summary,
representative evidence, history, lifecycle/owner controls, split/merge actions,
and task investigation links. Readonly members can inspect; only admins can
mutate. Handle loading, empty, stale read errors, partial and unavailable states,
and prevent older responses from overwriting a new environment or selection.

Update docs/studio-frontend.md, docs/studio-backend.md, and
docs/studio-persistence.md with semantics and operational requirements. Add
meaningful focused backend, real PostgreSQL, and frontend behavior tests.

## Concrete Steps

Run from /Users/jobz/.t3/worktrees/relayna/feat-studio-incident-inbox:

    make sync
    make -C studio/backend sync
    make -C apps/studio sync
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Use disposable PostgreSQL/Redis for integration. Set only task-local database
URLs; run Alembic upgrade, downgrade one new revision, then upgrade again.
Run make -C studio/backend integration with the disposable test URLs. Exercise
the page using synthetic operational evidence. T3 preview snapshot timed out
twice, so browser QA used the playwright skill/CLI fallback. Preview and test
databases were separate; no user service was called.

## Validation and Acceptance

Hundreds of failures with different numeric/UUID tokens collapse into a stable
cohort; different environments, services, queues/stages and exception types
remain distinct. All scanned pages contribute, duplicate IDs do not inflate
counts, and incomplete/failed/looping sources cannot appear complete. Operators
can open representative task investigation directly. Trend uses explicit recent
and preceding time buckets; missing timestamps are excluded and disclosed.

Owner, acknowledgement, resolution, reopening, split/merge and history survive
store reconstruction. Refresh never overwrites lifecycle. Stale revisions fail
with 409; concurrent corrections cannot make cycles or lose membership. Existing
admin/CSRF policy protects every mutation. PostgreSQL migration is additive and
upgrade/downgrade/upgrade succeeds. Existing tests and approved freeze guards
pass; UI is usable on desktop and mobile.

## Idempotence and Recovery

Discovery upserts stable identities without resetting operator state. Reads and
scans can be retried. Failed mutation transactions roll back state, mappings,
history and audit together. Browser mutation failures retain inputs and offer
refresh/retry. Repeating a stale mutation returns a conflict rather than applying
it twice. Use only disposable databases for migration-cycle validation; do not
run destructive initial downgrade on a user database. Git changes remain local
until verified; the user subsequently authorized a commit, push, and feature PR.
Do not merge or restack sibling branches in this thread.

## Artifacts and Notes

Verification log: /tmp/relayna-incident-verification.log. Coverage summaries:
/tmp/relayna-incident-frontend-coverage.log and
/tmp/relayna-incident-backend-coverage.log. Local screenshots:
/tmp/relayna-incident-desktop.png, /tmp/relayna-incident-mobile-top.png, and
/tmp/relayna-incident-mobile-dark.png. These use synthetic data and are local QA
artifacts, not deployment assets.

Prepared base confirmation: HEAD, origin/main, and their merge base were all
89954699d2e43aebfee54e4ec64c06edb0caf2ce after git fetch origin main --tags.

## Interfaces and Dependencies

Use existing FastAPI/Pydantic, SQLAlchemy/asyncpg/Alembic, React/TypeScript and
Vitest dependencies. Do not add runtime dependencies. Studio PostgreSQL and
registered failure-feed services are needed for durable operation. Redis auth
and existing investigation reads remain unchanged. New internal implementation
symbols need not be exported from relayna_studio.__init__.
