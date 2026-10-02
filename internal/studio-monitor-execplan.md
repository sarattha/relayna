# Studio monitoring workspace

This living ExecPlan follows `PLANS.md` at the repository root.

## Purpose / Big Picture

Keep the existing service and task Overview screens accessible and add a Monitor
workspace with Task explorer, Logs focus, and Investigation buttons. Operators
can read full-width logs, select a task without leaving its service, and inspect
events and metrics in a shared time window. Fix the confirmed permission-refresh
loop, cleared-search race, and topology route race in the same focused frontend PR.

## Progress

- [x] (2026-10-02) Created `codex/studio-monitor-workspace`; inspected skills, freeze tests, and selected visual targets.
- [x] (2026-10-03) Fixed all three findings; regression tests pass.
- [x] (2026-10-03) Integrated shared responsive Monitor with retained Overview and Configure.
- [x] (2026-10-03) Applied Color Designer palette; 148 frontend tests/build and full SDK/backend verification pass. Coverage remains below existing gates.
- [x] (2026-10-03) Computer Use verified 1440×1000 and 390×844; final combined comparisons pass in `design-qa.md`.
- [x] (2026-10-03) Updated operator documentation and saved normalized UI evidence.
- [ ] Commit, push, open and attach the draft PR.

## Surprises & Discoveries

The repository guide names v1.4.30 as the original strict freeze boundary, while
the current frontend manifest and latest release are v1.9.0. Existing frontend
coverage gates fail on main (92.12% statements, 84.72% branches, 90.15% functions,
95.15% lines). Do not lower thresholds or change freeze manifests to pass checks.
Computer Use and Color Designer's palette-picker are now available in this session. Mobile CPU labels overlapped
until tick density followed available chart width. Browser extension messages
were observed; no application render failure was seen. Synthetic fixtures do not
verify real provider latency or retention.

## Decision Log

Retain React and existing CSS rather than migrate frameworks. Use shared internal
components outside `pages/` and additive query parameters on existing URLs;
preserve API/types exports and page perimeter. The user explicitly approved the
layout additions and the three bug fixes on 2026-10-02. Use existing supplied
logo and icons; no new raster assets are needed. All monitoring data comes from
existing Studio APIs, with bounded task pages and log results. Automatic polling
waits for pending reads; a slow provider cannot starve the displayed result.
Desktop long-error inspection bounds the reader to keep the stack visible;
narrow CPU charts show three ticks. These decisions followed Computer Use QA.

## Outcomes & Retrospective

All three layouts and bug fixes are implemented. SDK: 686 passed, 9 skipped; backend: 442 passed, 19 skipped; frontend: 148 passed, production build passed. Final UI comparisons passed after fixing header density, mobile sticky space, error-panel bounds, and CPU tick overlap. Frontend coverage remains below its pre-existing gates: head 92.04% statements, 85.01% branches, 90.20% functions, 94.78% lines. Draft PR publication is the remaining handoff step.

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
