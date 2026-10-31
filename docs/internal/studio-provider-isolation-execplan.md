# Isolate unavailable Studio telemetry providers

This living ExecPlan follows [repository PLANS.md](https://github.com/sarattha/relayna/blob/main/PLANS.md).

## Purpose / Big Picture

A Loki, Prometheus, or Tempo outage must pause only dependent telemetry work.
Task details, events, registry operations, and healthy providers remain usable.
The first bounded real provider operation is the availability probe; successful
empty responses count as reachable. Cache failures briefly, permit one recovery
probe, bound concurrent work, and show paused telemetry with recovery in Studio.
Checking a separate readiness URL before every operation would add requests and
could reject healthy deployments whose proxy exposes query endpoints only.

## Progress

- [x] (2026-11-01) Inspected existing integration doctor, query services,
  HTTP-client lifecycle, monitor readers, and open PR #144.
- [x] (2026-11-01) Applied implementation-strategy and production-freeze-guard:
  latest release v1.13.0, strict perimeter v1.4.30; preserve public contracts.
- [x] (2026-11-01) Implemented bounded per-configuration gates, separate HTTP
  pools, two active operations, sixteen probe waiters, and 128 cached states.
- [x] (2026-11-01) Added backend regressions for failure suppression, recovery, cancellation,
  configuration separation, empty responses, concurrency, and lifecycle.
- [x] (2026-11-01) Paused dependent monitor reads, added jittered recovery,
  preserved core reads, and tested configuration/data ownership changes.
- [x] (2026-11-01) Exposed cached evidence through existing doctor fields and
  documented per-process budgets and shared Azure limitations. Updated version,
  dependency floor, locks, changelog, README, and upgrade docs for 1.13.1.
- [x] (2026-11-01) Final verification passed: 704 SDK tests/9 optional skips,
  703 backend tests with real services, 98.24% backend coverage, 376 frontend
  tests and all frontend coverage gates, frontend build, strict docs, package
  builds, and release metadata. Probe admission and cancellation cases passed.
- [ ] Push updated PR #144, request fresh review, and monitor checks/reviews.
- [ ] Land the PR when required checks/reviews are ready under user approval.

## Surprises & Discoveries

- All telemetry providers currently share the HTTP client used by federation,
  authentication, event ingestion, and health requests.
- The doctor reports configured providers as unknown; configuration alone does
  not establish connectivity. Its live Loki sample already uses a bounded query.
- Monitor reads are independently rendered but failed telemetry can be retried
  by auto-refresh repeatedly. A provider pause must persist across time-window
  key changes and reset when its configuration or service changes.

## Decision Log

- Keep all existing exports, constructor signatures, route shapes, status codes,
  and configuration fields. Use private availability state and existing 502
  errors/doctor states. No migration or freeze-manifest changes. The user asked
  for provider failure isolation; this is within that authorized behavior change.
  Date/author: 2026-11-01/Codex.
- Treat the first bounded actual operation as a query-capability check, then
  cache success/failure. Do not add a mandatory `/ready` endpoint dependency or
  create duplicate preflight requests for every query. Date/author:
  2026-11-01/Codex.
- Use independent HTTP clients for Loki, Prometheus, and Tempo through the
  existing client factory; retain the core client for core operations. Manage
  private clients with application shutdown. Date/author: 2026-11-01/Codex.
- Scope availability to service plus full provider configuration, with bounded
  state, ten-second operation deadlines, two concurrent operations per provider
  service, four default HTTP connections, and a single recovery probe. The user
  emphasized protecting Azure services shared with other applications, so failed
  recovery probes back off exponentially with jitter up to five minutes.
  Cancellation does not count as provider failure. Date/author:
  2026-11-01/Codex.
- The user requested version preparation, changelog/docs, review readiness,
  monitoring, and approved landing PR #144 when checks/reviews are ready. Prepare
  patch version 1.13.1 across all three packages and locks; do not publish a tag
  or deploy as part of the merge. Date/author: 2026-11-01/Codex.

## Outcomes & Retrospective

Provider isolation and version preparation are implemented. The first bounded
query acts as the availability check without extra health traffic. Outages pause
only dependent operations, with single recovery probes, capped queues/cache,
conservative HTTP pools, and exponential backoff with jitter. Prometheus sibling
queries are cancelled after a failed batch. A real socket regression confirms
stalled Loki traffic does not block the core client.

Backend/SDK checks have passed (704 SDK passed/9 skipped; 703 backend passed with
real PostgreSQL/Redis). Frontend tests passed all 376 cases and coverage meets
all gates (98.08% statements, 98.85% lines, 99.07% functions, 89.82% branches).
Backend coverage passed at 98.24%; strict docs, release metadata, frontend build,
SDK wheel/sdist, and backend wheel passed. Final probe-admission refinement is
verified. Public contracts and schemas remain unchanged. The 32 new backend
cases across this PR include its prior PostgreSQL regressions; six frontend
cases cover availability pause and recovery.

These defenses complement PR #144's database fixes; they do not establish
production CPU attribution or impose a global quota on other Azure applications.
The user approved landing the PR when checks/reviews are ready; tag publication
and deployment are outside the requested merge.

## Context and Orientation

Backend query services are in `studio/backend/src/relayna_studio/logs.py`,
`metrics.py`, and `traces.py`; `app.py` constructs and closes their clients.
`_doctor.py` exposes existing diagnostic check shapes. `apps/studio/src/monitor-data.ts`
and `monitor-workspace.tsx` own independent monitor reads and display. Existing
SDK and Studio public exports and response models remain unchanged.

## Plan of Work

Add a private reusable async availability gate. Apply it around provider work,
after registry sessions have closed. Keep optional trace evidence degradable.
Create separate telemetry HTTP clients and close them with the runtime. Surface
cached availability in existing doctor checks. Add a provider-only monitor
cooldown/recovery path keyed to configuration; retain existing reads for core
features and useful stale data. Add focused tests and operational docs.

## Validation and Acceptance

Failing providers must receive no repeated work during cooldown; a single
post-cooldown probe can recover and release waiting work. Different services,
configuration revisions, and provider families remain independent. Cancellation
must release concurrency slots and probe locks. Bound state and total deadlines.
Verify HTTP clients are distinct and all close. In UI tests, confirm core reads
remain usable, provider retries pause across window changes, and recovery and
configuration changes enable work again.

From the repository root:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C studio/backend coverage
    make -C apps/studio test
    make -C apps/studio build

Use disposable PostgreSQL and Redis for integration cases. Keep diagnostics in
ignored `reports/studio-provider-isolation-20261101/`; clean up only this task's
containers. Preserve prior PR changes and unrelated work. Do not add routes,
schema fields, or update freeze manifests to satisfy tests.
