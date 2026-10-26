# Integration Doctor and Studio Health

This is a living ExecPlan governed by the repository
[ExecPlan rules](https://github.com/sarattha/relayna/blob/main/PLANS.md).

## Purpose / Big Picture

Operators can explain empty and stale Studio views with scoped, timestamped
evidence. A Studio Health page separates control-plane readiness from upstream
reachability, event ingestion, provider configuration, and notification delivery.
A per-service Doctor checks a newly registered service and suggests next checks.
Administrators can browse the existing audit endpoint without changing roles.

## Progress

- [x] (2026-10-26) Fetch origin/main and confirm clean feat/studio-integration-doctor at 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
- [x] (2026-10-26) Read implementation-strategy and production-freeze-guard; record authorized additive perimeter changes.
- [x] (2026-10-26) Implement bounded backend diagnostics and regression tests; 22 real persistence tests pass.
- [x] (2026-10-26) Implement responsive Health/Doctor and audit UI with missing-data coverage.
- [x] (2026-10-26) Update operational docs and intentional freeze manifests.
- [x] (2026-10-26) First mandatory verification, frontend tests/build, strict docs and desktop/mobile inspection pass; backend first pass 98.10%, frontend statements 98.00%/branches 89.20%.
- [x] (2026-10-26) Final verification after review fixes passes: SDK 688 passed/9 skipped; backend 531 passed/22 persistence skips without service variables; full real-service backend coverage 553 passed, 98.29%; frontend 282 tests, build and coverage pass; strict docs pass.
- [x] (2026-10-26) Commit/push and open/link [PR #141](https://github.com/sarattha/relayna/pull/141) against main; one extra review completed and both findings fixed.
- [x] (2026-10-26) Current-head CI and automatic review passed; clean idle ownership handoff completed and individual watcher stopped.
- [x] (2026-10-26) Coordinator rebased this fifth layer onto Reliability #139, combining routes, UI/contracts, tests and approved manifests while preserving the first four heads.
- [x] (2026-10-26) Combined verification passed: 704 SDK tests/9 environment skips, 662 real-service backend tests, 357 frontend tests, all coverage gates, production build, strict docs and Alembic drift check.
- [ ] Publish the five-layer native stack and monitor CI on the rewritten head.

## Surprises & Discoveries

The latest release tag is v1.11.0. Policy retains v1.4.30 as the strict freeze
boundary, while checked-in manifest metadata currently says v1.10.1. Preserve that
metadata and deliberately add only feature 07 entries. Event and task retention
default to 86400 seconds, and service event history is additionally capped at
5000 entries. Readiness checks Redis and PostgreSQL schema/connectivity only.
Worker heartbeat age reflects task activity, not periodic liveness.

The shell defaults to Node 26; frontend checks require Node 24.22.1 and run via
`npm exec --yes --package=node@24.22.1 -- <command>`. Backend coverage requires
real PostgreSQL/Redis integration variables; without them existing integration
coverage is skipped. Isolated feature07 containers use ports 55447 and 56397.
T3 preview snapshots timed out twice; documented Playwright CLI fallback was
used. Desktop 1440px and mobile 390px evidence cards have no horizontal overflow.

## Decision Log

- 2026-10-26, Codex: Extra review identified zero/nonpositive history caps and
  future timestamp pairs. Clamp sample admission independently to 1–20 records,
  matching existing minimum-window semantics, and require source <= index <= a
  single sample observation time. Both fixes have regression tests.

- 2026-10-26, Codex: User has explicitly approved feature 07 production perimeter
  changes. Add new diagnostics routes and frontend contracts/pages; preserve
  existing route shapes, SDK behavior, outbound allowlists and role/CSRF rules.
- 2026-10-26, Codex: Reuse bounded existing read models, with expiring Redis
  refresh/delivery observations; no SQL schema change or migration. Observations
  are best effort and never fail ingestion or notification operations.
- 2026-10-26, Codex: GET reads existing evidence; administrator POST performs
  explicit bounded live checks. Missing timestamps, unconfigured providers,
  unsupported capabilities, unavailable queries and successful empty queries
  remain distinct. Metrics/trace configuration is not a successful provider probe.
- 2026-10-26, Codex: Coordinator owns stack #140 extension and final restacking.
  Open against main independently; never edit sibling worktrees/migrations.
- 2026-10-26, Coordinator: Inherit `0003_recovery` through the existing
  Incident Inbox → Recovery migration chain. Integration Doctor adds no
  migration and preserves all earlier feature contracts and diagnostic safeguards.
- 2026-10-26, Coordinator: Keep the full approved route set and metadata;
  order Doctor declarations before Incident declarations to match source-file
  ordering in the freeze guard. No route was added or removed by this correction.

## Outcomes & Retrospective

Implementation, documentation and review fixes are complete, and standalone
validation and hosted CI passed for [PR #141](https://github.com/sarattha/relayna/pull/141).
The coordinator has taken ownership and rebased this fifth layer after
Reliability #139. Combined verification passes, including all 662 backend tests
against isolated PostgreSQL/Redis and 357 frontend tests. Native stack #140
publication and rewritten-head CI monitoring are the remaining delivery steps.
Extra review allowance: used by one bounded delegated Codex review, task
`feature07-review-01-20261026`, gpt-6.1-sol, xhigh/priority. Review complete: two P2 findings (zero-cap sample admission and future timestamp
lag) fixed with regressions. No additional review rounds or @codex request will
be made.
Browser evidence: 1440×1000 and 390×844 layouts, empty/unknown/unconfigured
service states, failed live check and absent PostgreSQL audit storage verified.
No new migration. The standalone main baseline was `0002_load_profiles`;
the combined stack inherits schema head `0003_recovery` after
`0003_incident_inbox`.

Original main merge-base: 89954699d2e43aebfee54e4ec64c06edb0caf2ce.

## Context and Orientation

Studio is the control plane in studio/backend/src/relayna_studio and the React
frontend in apps/studio/src. The SDK under src/relayna emits task events; Studio
indexes retained events for investigation. An outbox is PostgreSQL's queued
publication to Redis streams; its age measures delivery backlog, not task lag.
Audit records live in PostgreSQL and are admin-only with Entra enabled.

## Compatibility Boundary

Compare released behavior against v1.11.0; strict policy perimeter is v1.4.30.
The user authorizes additive feature 07 routes, contracts and pages. No existing
response, public SDK API, durable event schema, auth requirement or migration
changes. New expiring diagnostic observations are optional auxiliary state;
absence is unknown. Deliberately update backend routes/feature manifests and
frontend API/type/page manifest entries, not existing perimeter metadata.

## Plan of Work

Add private backend modules for diagnostics models, bounded reads and recording.
Wire through app.py; record event-pull and notification delivery outcomes using
existing service paths. Use current health refresh, logs queries and configured
outbound policy for explicit probes. Reuse database tables for outbox samples.
Build a HealthPage and API/types, primary navigation, service selection and
admin-only audit browser. Document scope and limitations in studio backend,
frontend and persistence docs. Add regression tests before full verification.

## Concrete Steps

From repository root, run focused backend pytest and frontend vitest while
iterating. Finish with bash .codex/skills/code-change-verification/scripts/run.sh,
make -C apps/studio test, make -C apps/studio build and the strict docs target.
Run local frontend/backend fixtures for desktop/mobile browser inspection.
Create a single committed PR against main with the repository PR template and
register its URL using link_pull_request. Use T3 event-driven monitoring.

## Validation and Acceptance

A reachable empty service reports no retained evidence rather than healthy
ingestion. Unsupported feeds do not trigger network calls. Configured but
unprobed providers remain unknown. Missing/naive timestamps produce unknown age.
A failed source preserves identity and does not blank successful checks. Reads
are bounded, probes time out, URLs obey current policy, non-admins cannot POST
or read audit. Health/audit failure and disabled/unconfigured states are usable
at desktop and phone widths. Full required verification passes before pushing.

## Idempotence and Recovery

GET diagnostics does not mutate existing state. Live checks use existing health
refresh behavior and optional expiring evidence only; retries are safe. No
migration applies. Revert the feature commit to remove routes/UI; auxiliary
Redis keys expire. Leave the checkout committed and clean for coordinator.
