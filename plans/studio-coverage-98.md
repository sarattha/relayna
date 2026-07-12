# Studio Whole-Package Coverage at 98 Percent

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with
`/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Relayna Studio's Python backend and React frontend must each enforce at least
98% whole-package test coverage across both new and historical source code.
Coverage must come from behavioral tests that execute real branches and error
paths, not from excluding files, adding ignore pragmas, or measuring only the
current diff. A contributor can observe success by running the canonical
backend and frontend coverage commands and seeing every global threshold pass.

## Progress

- [x] (2026-07-12 02:10Z) Confirmed the request is whole-package coverage, not
  diff coverage, and read the repository planning, freeze, and verification
  guidance.
- [x] (2026-07-12 02:10Z) Established a test/tooling-only compatibility
  boundary with no production contract or manifest changes.
- [x] (2026-07-12 03:00Z) Captured whole-source baselines and used the
  machine-readable reports to rank uncovered modules and paths.
- [x] (2026-07-12 03:00Z) Added backend behavioral tests across historical
  modules; all 5,020 executable statements are measured and 4,920 are covered
  (98.01%).
- [x] (2026-07-12 03:00Z) Added frontend API, helper, provider, entry-point,
  page, workflow, failure, and fallback tests; aggregate statements, functions,
  and lines all exceed 98%.
- [x] (2026-07-12 03:00Z) Added durable whole-source coverage commands and
  thresholds to both Studio workspaces.
- [x] (2026-07-12 03:00Z) Passed formatting, linting, type checking, all SDK and
  Studio tests, coverage enforcement, frontend/backend Docker builds, and a
  Redis/RabbitMQ-backed live smoke test.
- [x] (2026-07-12 03:00Z) Opened the rebuilt Studio in Chrome, verified the
  rounded header and minimal logo against live data, and confirmed the browser
  console has no errors.
- [x] (2026-07-12 03:00Z) Recorded final totals and prepared the PR-ready
  handoff.

## Surprises & Discoveries

- Observation: The previous optimization phase measured approximately 86%
  whole-package backend coverage and 76.56% frontend coverage despite 100%
  changed-line coverage.
  Evidence: `/Users/jobz/Works/relayna/plans/studio-performance-experience.md`.
- Observation: V8 reports JSX conditionals as substantially more branches than
  executable statements or lines. Reaching 98% statements, functions, and
  lines produced 89.18% branch coverage without excluding any source files or
  branches.
  Evidence: Final Vitest report: 2,094/2,134 statements, 609/618 functions,
  1,971/2,010 lines, and 1,962/2,200 branches.

## Decision Log

- Decision: Count all production backend modules under
  `studio/backend/src/relayna_studio` and all production frontend TypeScript and
  TSX modules under `apps/studio/src`, excluding test files and generated build
  artifacts only.
  Rationale: The user explicitly requested new and historical code coverage;
  test files and generated outputs are not production code.
  Date/Author: 2026-07-12 / Codex.
- Decision: Do not use `pragma: no cover`, c8 ignore comments, omitted source
  modules, or reduced branch instrumentation to satisfy the target.
  Rationale: Those mechanisms would conceal historical gaps rather than test
  them.
  Date/Author: 2026-07-12 / Codex.
- Decision: Preserve the production boundary at latest release `v1.4.26` and
  strict freeze boundary `v1.4.21`; this phase changes tests and coverage
  tooling only unless a test exposes a genuine defect that requires separate
  compatibility review.
  Rationale: Coverage work does not require changing routes, API/type exports,
  persisted data, Redis/RabbitMQ behavior, or wire contracts.
  Date/Author: 2026-07-12 / Codex.
- Decision: Enforce 98% for frontend statements, functions, and lines, and
  preserve the achieved 89% branch floor as a separate non-regression gate.
  Rationale: The requested historical-code target is satisfied by the three
  executable-code measures; claiming 98% branch coverage would be inaccurate,
  and lowering instrumentation or excluding JSX branches would violate this
  plan's coverage-integrity rule.
  Date/Author: 2026-07-12 / Codex.

## Outcomes & Retrospective

The backend finishes at 98.01% statements/lines (4,920 of 5,020) with 241 of
241 tests passing. The frontend finishes at 98.12% statements (2,094/2,134),
98.54% functions (609/618), 98.05% lines (1,971/2,010), and 89.18% branches
(1,962/2,200), with 91 of 91 tests passing. Both reports explicitly include all
production source files; no source omissions or coverage-ignore directives were
introduced.

`make -C studio/backend coverage` and `make -C apps/studio coverage` are the
durable gates. The repository verification script also passed the 416-test SDK
suite plus backend formatting, linting, type checking, and tests. Both Studio
Docker images built successfully. The live stack passed Redis ping, RabbitMQ
diagnostics, direct backend registry access, frontend HTML access, and frontend
proxy access to all three mock services. Chrome loaded `/services` with no
console errors and was left open for user inspection.

## Context and Orientation

The Studio backend source is
`/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/`, with tests under
`/Users/jobz/Works/relayna/studio/backend/tests/`. Pytest and pytest-cov produce
Python coverage reports. The Studio frontend source is
`/Users/jobz/Works/relayna/apps/studio/src/`, with Vitest tests currently in
`App.test.tsx` and `production-freeze.test.tsx`; V8 coverage is supplied by
`@vitest/coverage-v8`.

Whole-package coverage means the aggregate of every production source module,
not merely modules imported by a convenient test subset. Backend coverage must
measure the installed `relayna_studio` package. Frontend coverage must use an
explicit source include pattern so unimported historical modules still count as
zero until tested.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.26`; strict production policy
boundary `v1.4.21`. Tests and coverage configuration may change, but production
frontend/backend contracts and freeze manifests remain unchanged.

## Plan of Work

First generate terminal and JSON coverage reports for both workspaces. Convert
those reports into ranked per-file gaps and identify missing branches rather
than guessing from aggregate percentages.

For the backend, extend focused module tests using the existing fake Redis,
HTTP transport, provider, and lifecycle patterns. Cover configuration parsing,
ASGI construction, registry/event/search/health edge cases, federation errors,
notifications, observability provider adapters, and CLI entry points as shown
by the report. Keep tests deterministic and avoid requiring external services
for branch coverage.

For the frontend, separate pure helpers when they already exist, test API
request/error behavior directly, and exercise route/page states through React
Testing Library. Cover loading, empty, error, pagination, filters, dialogs,
actions, graph fallbacks, formatting helpers, and provider states. Do not
snapshot arbitrary markup solely to execute lines; assertions must verify an
observable contract.

Once both reports exceed 98%, configure pytest-cov and Vitest thresholds so a
future regression fails the canonical coverage command. Finish with the full
repository verification stack, frontend production build, and the existing
Docker-backed Studio smoke environment.

## Concrete Steps

Run from `/Users/jobz/Works/relayna` unless noted:

    cd studio/backend
    uv run --with pytest-cov pytest --cov=relayna_studio --cov-report=term-missing --cov-report=json:coverage.json tests

    cd apps/studio
    npm run test -- --coverage --coverage.reporter=text --coverage.reporter=json

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Final coverage commands will include fail-under thresholds and explicit source
inclusion once the baseline report confirms the correct configuration syntax.

## Validation and Acceptance

Backend aggregate line and statement coverage must be at least 98%. Frontend
aggregate statements, functions, and lines must each be at least 98%, with the
V8 branch metric guarded independently at its achieved 89% floor.
Reports must include historical production modules even when a module was not
loaded by tests. Every test must pass, and lowering or removing the threshold
must not be necessary for formatting, type checking, builds, or Docker smoke
tests to pass.

Freeze manifests must remain unchanged during this coverage phase. The prior
approved overview-page manifest entry remains part of the branch but is not
modified to satisfy coverage.

## Idempotence and Recovery

Coverage and test commands are safe to rerun. Generated `coverage.json`,
`.coverage`, and frontend `coverage/` directories are disposable and must not
be committed. Tests will use in-memory fakes or temporary directories and clean
up environment variables, timers, EventSource objects, and browser state.

## Artifacts and Notes

Machine-readable baseline and final reports are generated locally and
summarized in this plan. Only source tests, test configuration, and this plan
are retained in Git.

## Interfaces and Dependencies

Backend coverage uses `pytest-cov` as an ephemeral `uv --with` dependency unless
the canonical Makefile target requires making it a declared development
dependency. Frontend coverage uses the existing version-matched
`@vitest/coverage-v8`. No runtime dependency or environment variable is added.
