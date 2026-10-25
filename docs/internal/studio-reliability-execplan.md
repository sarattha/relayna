# Studio Reliability Goals & Alert Routing

This living ExecPlan follows `PLANS.md`.

## Purpose / Big Picture

Operators configure service/environment/owner goals, inspect honest evidence states,
export Prometheus rules and Alertmanager routing, and follow alert deliveries into
Studio investigations. Existing Prometheus evaluates rules; Alertmanager owns grouping,
silences, inhibition and routing. Studio reuses the failed-task email HTTP transport.

## Progress

- [x] (2026-10-25) Fetched origin/main; clean feature branch and original merge-base verified as 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
- [x] Read implementation-strategy, production-freeze-guard, PLANS.md and current integrations.
- [x] (2026-10-25) Implement persisted goal management, evidence preview, rule export and authenticated notification bridge.
- [x] (2026-10-25) Implement responsive UI and behavioral tests; deliberately extend freeze manifests.
- [x] (2026-10-25) Document instrumentation and operations; run verification and browser checks.
- [x] (2026-10-25) Committed/pushed implementation and created/linked PR #139 against main; coordination notified of intended fourth layer.
- [ ] Address review/CI events through T3 watch; coordination owns final restacking and migration ordering.

## Surprises & Discoveries

- Integration suites truncate their disposable database. Verification and coverage must run sequentially against a shared test database; the final sequential runs pass.

- Latest release is v1.11.0; strict policy boundary remains v1.4.30. Current manifests are labeled v1.10.1 after prior authorized changes.
- Events and task projections default to 86400 seconds retention; they cannot establish long-window business outcomes.
- SDK task duration is handler duration, and task counters count attempts. Neither proves accepted-order success or end-to-end latency.
- SDK supports retry counters, worker heartbeat and pressure signals, but no queue-age metric. Pressure gauges have no inherent observation freshness.
- Disposable integration setup initially lacked alembic_version after metadata-only creation; created a separate disposable database, migrated to 0002_load_profiles, and all 21 existing integration tests passed.
- Existing PostgreSQL operator_settings JSON storage can hold bounded goal settings and last-delivery diagnostics without a migration.

## Decision Log

- (2026-10-25, Codex) Read shared email settings before the receipt transaction and lock registry scope in that same transaction. A one-connection PostgreSQL concurrency test guards against nested-session pool exhaustion and duplicate sends.

- (2026-10-25, Codex) User explicitly authorized this feature's freeze exception. Preserve existing SDK, routes, notification settings and persisted formats; add Studio-only endpoints, page, settings keys and optional webhook credential. No sibling branch dependency.
- (2026-10-25, Codex) Export rules/config rather than provisioning or replacing external alert infrastructure. Preview is a point-in-time evidence read, never a claim that rules are installed.
- (2026-10-25, Codex) Business objectives require externally maintained, mature accepted-work cohorts with good/eligible counts, exact window bounds and evidence freshness. SDK attempts and retained events are not substituted.
- (2026-10-25, Codex) Use existing operator_settings table with versioned namespaced keys; PostgreSQL required for goal writes and bridge diagnostics. No Alembic revision, Redis schema change, or SDK change.

## Outcomes & Retrospective

Implemented immutable scoped goals, previews and guarded exports for six kinds,
authenticated Alertmanager callbacks, shared email transport, receipts, UI and docs.
SDK verification: 688 passed / 9 skipped. Final backend verification: 564 passed with PostgreSQL/Redis enabled, including
30 reliability tests. Backend coverage: 98.19%.
Existing PostgreSQL/Redis integration suite: 21 passed. Frontend: 277 passed, build passed; coverage 98.00% statements, 89.06% branches.
Prometheus promtool accepted all 12 exported rules and passed five evaluation cases
(breached/met objective, zero denominator, stale and duplicate aggregate evidence).
Desktop 1440px and mobile 390px fit without document overflow; both screenshots
inspected. Local screenshots archived under /tmp/relayna-reliability-ui/. T3 browser snapshots timed out twice, so Playwright skill was used.
PR: https://github.com/sarattha/relayna/pull/139. One additional Codex review requested
via `@codex review` (allowance used, do not request another). Local checks are ready;
GitHub CI/review pending. T3 event-driven watch handles follow-up; never merge or
enable auto-merge in this task. Coordination thread owns final stack assembly.

## Context and Orientation

Studio backend is `studio/backend/src/relayna_studio`; metrics.py already queries
registered Prometheus and failed_task_notifications.py provides the email client.
app.py composes providers; auth.py and _operator_auth.py enforce roles/CSRF.
The React app lives in apps/studio/src with shared navigation, service scope and UI.
Operator settings and mutation audit are in database.py. Goals and delivery receipts
will be stored as new reliability:v1 settings documents, without changing old rows.

## Compatibility Boundary

Strict perimeter v1.4.30, released behavior compared with v1.11.0. Intentional,
authorized additive Studio API/config/page changes; affected route, public-surface
and frontend freeze manifests receive reviewed additions plus behavioral coverage.
Existing failed-task notifications retain their behavior. Business instrumentation
is a new opt-in external contract, documented explicitly. No database migration.

## Plan of Work

Add private reliability module with validated goal types, PostgreSQL settings store,
Prometheus preview and rule export. Install an exact-path bearer-authenticated
Alertmanager callback (independent from browser sessions), reuse configured email
transport and enabled setting, serialize receipts for idempotence and diagnostics.
Add /reliability page with scoped goal creation/deletion, previews, export and links.
Add backend/frontend tests for evidence boundaries, routing, auth, duplicates and
outbound failures. Update docs and freeze entries deliberately.

## Concrete Steps

From repository root run focused pytest/vitest while iterating, then:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Run desktop and mobile browser checks. Commit/push feature branch and open a PR
against main; coordination thread 18e948a2-67d9-49b6-91ad-58ee29029145 owns restacking.

## Validation and Acceptance

No evidence, stale, unsupported and unavailable data must never become healthy or
zero. Preview and exports must use identical expressions and service identity.
Business ratios require a positive explicit denominator, mature unfinished work in
that denominator, exact cohort boundaries and fresh aggregate evidence. Callback
must require its own secret, resolve recipients only from stored goals, reject stale
rule revisions/environment drift, deduplicate deliveries and persist failure details.
Read-only members may inspect but cannot mutate. No external rule is installed by
saving a goal. Layout must fit 390px and desktop widths.

## Idempotence and Recovery

Goal writes and receipt updates use row locks/transactions. Existing schema and
settings remain valid. Export can be repeated; operators deploy/reload externally.
Disable or delete a goal and remove its external rules to retire it. HTTP email is
at-least-once across a crash after send but before receipt commit; document that
boundary rather than claiming exactly-once delivery.

## Artifacts and Notes

Original main base: 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
Final stack: main <- 01 Incident Inbox <- 02 Recovery Center <- 03 Cockpit <- 05 Reliability.
No rebase or retarget of sibling PRs in this conversation.

## Interfaces and Dependencies

Use FastAPI/Pydantic/httpx/SQLAlchemy and current React components only. No new
runtime dependency. Optional RELAYNA_STUDIO_RELIABILITY_WEBHOOK_TOKEN authorizes
Alertmanager's callback; RELAYNA_STUDIO_RELIABILITY_PUBLIC_URL supplies optional clickable links; never return it in responses or exports. Prometheus egress
uses the same outbound policy as existing providers. Alertmanager export uses a
credential-file placeholder, not a secret. Email bridge uses the existing email
service and global enabled setting, with per-goal owner/environment recipients.
