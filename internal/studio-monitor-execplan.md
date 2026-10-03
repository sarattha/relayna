# Studio monitoring workspace

This living ExecPlan follows `PLANS.md` at the repository root.

## Purpose / Big Picture

Keep the existing service and task Overview screens accessible and add a Monitor
workspace with Task explorer, Logs focus, and Investigation buttons. Operators
can read full-width logs, select a task without leaving its service, and inspect
events and metrics in a shared time window. Fix the confirmed permission-refresh
loop, cleared-search race, and topology route race in the same focused frontend PR.

## Progress

- [x] (2026-10-03) CI repair: inspected latest security job 111084037114; upgraded vulnerable lock entries only. SDK/backend audits and frontend audit pass; mandatory Python verification passes (686 SDK and 442 backend tests). Replacement CI is tracked on PR #131 after pushing.
- [x] (2026-10-03) Both security jobs pass on 53cbccb. Corrected duplicate disclosure toggles in the task-log test helper; native events settle before returning, one pending log read is asserted. Frontend: 151 tests/build passed, failing case passed five focused runs. Final replacement CI is tracked on PR #131.
- [x] (2026-10-03) Follow-up: added Pod memory beside CPU using existing `memory_usage` group; shared-window markers, independent empty states, desktop/mobile Computer Use, 151 frontend tests, and production build passed.
- [x] (2026-10-02) Created `codex/studio-monitor-workspace`; inspected skills, freeze tests, and selected visual targets.
- [x] (2026-10-03) Fixed all three findings; regression tests pass.
- [x] (2026-10-03) Integrated shared responsive Monitor with retained Overview and Configure.
- [x] (2026-10-03) Applied Color Designer palette; initial 148 frontend tests/build and full SDK/backend verification pass. The initial coverage gap was resolved in the consolidated follow-up below.
- [x] (2026-10-03) Computer Use verified 1440×1000 and 390×844; final combined comparisons pass in `design-qa.md`.
- [x] (2026-10-03) Updated operator documentation and saved normalized UI evidence.
- [x] (2026-10-03) Committed and pushed `codex/studio-monitor-workspace`; opened and attached draft PR #131.
- [x] (2026-10-03) Follow-up: restored service-control spacing and selector/button alignment; Computer Use desktop and 390×844 checks, 148 frontend tests, and production build passed.
- [x] (2026-10-03) Consolidated native Ampule workflows into PR #131 and added 67 Studio regression tests. All original frontend coverage gates pass; SDK/frontend coverage is now enforced in CI. Companion Chamber PR #43 adds 78 regression tests and a 96% coverage floor. Both drafts have green CI.

## Surprises & Discoveries

The repository guide names v1.4.30 as the original strict freeze boundary, while
the current frontend manifest and latest release are v1.9.0. Existing frontend
coverage gates initially failed on main (92.12% statements, 84.72% branches, 90.15% functions,
95.15% lines). The follow-up closes that gap without lowering thresholds or changing coverage scope.
Computer Use and Color Designer's palette-picker are now available in this session. Mobile CPU labels overlapped
until tick density followed available chart width. Browser extension messages
were observed; no application render failure was seen. Synthetic fixtures do not
verify real provider latency or retention.

The Overview/Configure wrapper removed SectionCard's direct-child spacing from
the action row, observation controls, and configuration disclosure. Restore the
existing 18px stack gap inside that wrapper. Bottom-align the observation button
with the selector and use a full-width field below 600px; no API or type changes.

## Decision Log

CI security repair: GitHub SDK audit reports two AnyIO findings fixed in 4.14.2
and three urllib3 findings fixed in 2.8.0. Upgrade only those packages in the SDK
lock and AnyIO in the backend lock (currently 4.13.0). The reproduced backend
audit additionally found 13 PyJWT 2.13.0 advisories; upgrade its lock to 2.15.0,
after which the backend audit reports no known vulnerabilities. Preserve dependency ranges,
API contracts, freeze manifests and audit enforcement; no migration is needed.
The user explicitly authorized this CI repair and commit/push. Verify both Python
audits, frontend audit, and the mandatory Python stack after syncing both locks.

Replacement run 37082163496 passed security hardening, but frontend job
111084841448 failed its task-log loading test. The shared test helper both sets
`details.open` (queuing a native toggle) and dispatches a synthetic toggle. While
the first log read is deferred, the duplicate read can return an error and end
the loading state. Use one summary click, settle its native event in `act`, and
assert one request while pending;
no runtime behavior or timeout threshold changes.

Retain React and existing CSS rather than migrate frameworks. Use shared internal
components outside `pages/` and additive query parameters on existing URLs;
preserve API/types exports and page perimeter. The user explicitly approved the
layout additions and the three bug fixes on 2026-10-02. Use existing supplied
logo and icons; no new raster assets are needed. All monitoring data comes from
existing Studio APIs, with bounded task pages and log results. Automatic polling
waits for pending reads; a slow provider cannot starve the displayed result.
Desktop long-error inspection bounds the reader to keep the stack visible;
narrow CPU charts show three ticks. These decisions followed Computer Use QA.

Pod memory uses the existing released `memory_usage` request/response contract
(bytes), converted to MiB only for display. Reuse the branch-local resource chart
with an internal metric selector, request CPU and memory together, and retain
separate empty states plus shared provider warnings. Stack the two charts below
800px. No API/type exports, freeze manifests, or backend behavior change.

## Outcomes & Retrospective

Security CI repair changes only the SDK/backend locks: AnyIO 4.14.2 in both,
urllib3 2.8.0 in SDK, and PyJWT 2.15.0 in backend. SDK/backend dependency audits
and frontend security audit pass. Mandatory format/lint/typecheck/test sequence
passes: SDK 686 passed/9 skipped; backend 442 passed/19 skipped. No audit bypass,
public contract change, or unrelated package upgrade was introduced.

Pod memory follow-up is complete: memory bytes display in MiB beside CPU cores,
with identical time bounds and selected-event markers. Charts stack below 800px.
The initial expanded frontend suite had 151 passing tests and the build passed. The
existing provider API and production-freeze manifests are unchanged.

All three layouts, bug fixes, and native Ampule workflows are implemented in draft PR https://github.com/sarattha/relayna/pull/131. Final verification: SDK 686 passed/9 skipped with 97.94% coverage; backend 495 passed with 98.13% coverage, including real PostgreSQL integration tests; frontend 233 passed and production build passed. Whole-source frontend coverage under CI's Node 20 is 98.04% statements, 89.02% branches, 99.36% functions, and 98.67% lines, passing all unchanged gates. SDK and frontend coverage are now enforced in CI. Companion Chamber draft PR https://github.com/sarattha/ampule-chamber/pull/43 has 394 tests and 97.80% CI coverage with a 96% floor. Both drafts have green CI. PR #132 was consolidated into PR #131's branch; nothing was merged to main.

Desktop/mobile Computer Use comparisons passed after correcting header density, mobile sticky space, error-panel bounds, CPU tick overlap, and the native Ampule flows. Synthetic-provider UI evidence verifies layout and interaction; staging provider performance and real target execution remain operational acceptance work. See [the Ampule ExecPlan](../docs/internal/ampule-studio-api-execplan.md) for the additive backend contracts, deployment requirements, and saved validation evidence.

## Context and Orientation

`apps/studio/src/App.tsx` owns routes; `auth-context.tsx` owns sessions;
`services-context.tsx` owns the registry. `pages/ServiceDetailPage.tsx` and
`pages/TaskDetailPage.tsx` contain existing summaries and telemetry. Add shared
monitoring UI under `apps/studio/src/monitor-workspace.tsx`, and use `studio.css`
for responsive geometry and palette. SDK/backend Python code is outside scope.

## Compatibility Boundary

Original freeze boundary v1.4.30; latest released comparison v1.9.0. Preserve
backend routes/responses, `api.ts` exports, canonical types, existing route paths,
and persisted Redis/RabbitMQ data. Keep the v1.9.0 frontend manifest unchanged.
Use optional `view`, `layout`, and monitor-specific query parameters; old links
still open Overview. Local browser preferences are optional and tolerate disabled
storage. No migration or compatibility shim is needed.

## Plan of Work

Guard Access fetches by permission; refresh sessions without unmounting active
children. Invalidate service search on Clear and topology on route changes.
Implement one monitoring component whose mode changes do not reset filters,
task selection, or scroll. Use existing search/log/event/metric APIs, request
version guards, bounded pages, explicit freshness and pause state. Integrate
Monitor into service and task tabs and add full-workspace links beside existing log controls. Preserve overview diagnostics and configuration. Validate with
focused tests then full frontend tests/build, and run the mandatory verification
skill if any Python area or build/test behavior changes.

## Concrete Steps

From `/Users/jobz/Works/relayna` run focused Vitest while iterating, then:

    make -C apps/studio test
    make -C apps/studio build
    make -C apps/studio coverage
    bash .codex/skills/code-change-verification/scripts/run.sh

Use a loopback-only synthetic API fixture for UI validation if the real backend
is unavailable. Do not bundle fixture data into production. Open the Vite app
through Computer Use in Chrome and exercise desktop and narrow viewport states.
Create a draft PR with `gh pr create --draft --body-file <file>` after checks.

## Validation and Acceptance

Regression tests prove no requests on readonly Access, no workspace remount on
403 refresh, Clear cannot restore an old search, and late topology cannot replace
the current service. Monitor tests cover layout/scope/task selection, retained
filters, pagination, stale requests, pause/refresh, event selection and time
window, unavailable providers, and malformed preferences. UI evidence includes
all layouts, service/task entry, long logs, many tasks, keyboard interaction,
and narrow/mobile layout without page overflow. `design-qa.md` records normalized
source/implementation comparisons and must say `final result: passed` before
handoff. Report any existing coverage deficit transparently.

## Idempotence and Recovery

Work only on this branch and preserve unrelated work. Revert this PR to restore
the previous UI; no external data changes occur. Preview servers use dedicated
loopback ports and synthetic records. Stop only processes created for this task.

## Artifacts and Notes

Visual targets in `/Users/jobz/.codex/generated_images/01a0fcee-5428-7012-a85d-703f18db5f39/`:
`exec-0a935613-e1fa-4e38-b38c-3959dde8dbcf.png` (Explorer),
`exec-37658ce2-ffc3-4cbd-9c3d-ab29a72924fd.png` (Logs focus),
`exec-ae94a09b-fba9-40ac-b896-c9f2775ed4a8.png` (Investigation).

## Interfaces and Dependencies

No new runtime dependency or backend contract is planned. React, router query
parameters, existing API helpers, and the supplied Relayna brand asset suffice.
Palette roles: ivory canvas/surfaces, dark text, teal selection, burnt-orange
actions, semantic red/amber/green feedback. All statuses include text labels.
