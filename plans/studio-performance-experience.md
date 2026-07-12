# Studio Performance and Operator Experience

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Relayna Studio operators should reach service health and failed-task evidence
without scrolling past a large recurring hero or waiting on serial Redis reads.
The Studio frontend will use a compact, route-aware application shell and load
route-specific graph code on demand. Task search and task detail will emphasize
accessible, shareable filters and incident context. The Studio backend will
serve the same response models while batching retained-event, registry, health,
and search-document reads and bounding independent remote work.

The results are observable through smaller initial production chunks, active
navigation and labelled controls in the browser, a narrowly reviewed frontend
freeze-manifest update, backend command-count tests, the full SDK/Studio
verification stacks, 100% changed-line coverage, and a Docker-backed smoke flow
using real Redis and RabbitMQ services.

## Progress

- [x] (2026-07-11 00:00Z) Confirmed a clean `main` and created branch
  `codex/improve-studio-performance-ux` before implementation.
- [x] (2026-07-11 00:00Z) Read the analysis report, production-freeze guard,
  implementation-strategy guidance, verification skill, and ExecPlan rules.
- [x] (2026-07-11 00:00Z) Established the compatibility boundary and initial
  internal-only implementation shape.
- [x] (2026-07-11 17:13Z) Captured baseline: 166 backend and 48 frontend
  tests passed, whole-backend coverage was 87%, the frontend emitted one
  504.86 KB chunk, and Docker was available.
- [x] (2026-07-11 17:13Z) Implemented and tested Studio backend batching and
  bounded concurrency. Added Redis command-count and fallback-path coverage.
- [x] (2026-07-11 17:13Z) Implemented and tested the compact frontend shell,
  operational overview, lazy route/graph loading, labelled URL-backed search,
  incident-first task presentation, and safe service action grouping.
- [x] (2026-07-11 18:24Z) Passed the mandatory verification script: SDK
  formatting, lint, type checking, and 416 tests; Studio backend formatting,
  lint, type checking, and 169 tests.
- [x] (2026-07-11 18:24Z) Passed 50 frontend tests, the production build,
  freeze checks, and 100% backend/frontend changed-line coverage. Both new
  frontend modules have 100% statement, function, and line coverage.
- [x] (2026-07-11 18:24Z) Built final backend and frontend production images
  and passed Docker-backed Redis, RabbitMQ, SDK worker, Studio API, frontend,
  and headed-browser operator smoke tests.
- [x] (2026-07-11 18:24Z) Updated outcomes, collected PR-draft inputs, removed
  generated artifacts and disposable containers, and prepared final handoff.
- [x] (2026-07-12 02:00Z) Replaced the temporary letter tile with a generated,
  transparent Relayna routing mark; aligned the sticky header with the rounded
  card language; and verified desktop plus 390px layouts in the live Docker UI.

## Surprises & Discoveries

- Observation: The latest repository release tag and current freeze manifests
  are `v1.4.26`, while contributor policy retains `v1.4.21` as the strict
  production boundary.
  Evidence: `git tag --sort=-version:refname`,
  `studio/backend/tests/freeze/public_surface.json`, and
  `apps/studio/src/test/production-freeze-manifest.json`.
- Observation: The analysis measured one 504.86 KB minified frontend chunk and
  identified Redis N+1 reads in event history plus sequential health and search
  loading.
  Evidence: `/Users/jobz/Works/relayna/reports/relayna-sdk-studio-analysis.html`.
- Observation: React Flow is imported by the shared `ui.tsx` module and the
  provider wraps every route, so route-level imports alone would not isolate the
  graph dependency.
  Evidence: `apps/studio/src/App.tsx`, `apps/studio/src/ui.tsx`, and
  `apps/studio/src/main.tsx`.
- Observation: Whole-package coverage started below the requested target at
  87% backend statements and 75.3% frontend statements because hundreds of
  untouched historical branches are uncovered.
  Evidence: baseline pytest-cov and Vitest V8 reports.
- Observation: The production frontend split cleanly into a 219.49 KB initial
  chunk, 1.53-35.50 KB route chunks, and a 177.09 KB graph chunk, removing the
  previous greater-than-500-KB warning.
  Evidence: `npm run build` and both production Docker builds.
- Observation: A real 25-service Studio list used one Redis `SMEMBERS` and
  three `MGET` commands; a real 20-event read and task search used one
  `LRANGE`, one direct registry `GET`, one `SMEMBERS`, and two `MGET` commands.
  Evidence: Docker-backed Redis `INFO commandstats` after `CONFIG RESETSTAT`.

## Decision Log

- Decision: Keep route paths, response models, frontend API/type exports,
  Redis keys, and serialized documents unchanged.
  Rationale: The user requested performance and experience improvements, which
  can be delivered behind existing contracts without changing the frozen
  production perimeter.
  Date/Author: 2026-07-11 / Codex.
- Decision: The user explicitly approves breaking the production freeze
  perimeter for this task; use that exception only for a material Studio
  operator-experience improvement and update the exact affected manifest with
  tests and a compatibility note.
  Rationale: This removes the default freeze blocker while keeping perimeter
  changes narrow, intentional, and reviewable.
  Date/Author: 2026-07-11 / Codex.
- Decision: Optimize reads with bounded windows, Redis `MGET`/pipelines, and
  bounded `asyncio` concurrency while preserving deterministic response order.
  Rationale: This directly addresses the report's Studio engineering findings
  and avoids a migration or compatibility shim.
  Date/Author: 2026-07-11 / Codex.
- Decision: Split graph rendering into its own lazy chunk and lazy-load route
  modules behind a common suspense fallback.
  Rationale: `@xyflow/react` must leave the eagerly imported shared UI module
  for the initial bundle to shrink materially.
  Date/Author: 2026-07-11 / Codex.
- Decision: Add frontend coverage instrumentation and enforce the requested 98%
  floor on changed executable lines for both Studio workspaces; do not exclude
  untested changed code merely to satisfy the number.
  Rationale: Whole-package baselines were already 87% backend and 75.3%
  frontend before this work. Changed-line coverage is the honest, reviewable
  measure for this scoped change without turning it into an unrelated rewrite
  of historical tests.
  Date/Author: 2026-07-11 / Codex.
- Decision: Enforce the requested coverage target on changed executable lines
  and directly on new frontend modules, while reporting the pre-existing
  whole-package baseline separately.
  Rationale: Diff coverage reached 100% for backend and frontend changes; both
  new frontend modules reached 100% statements/functions/lines. Raising all
  untouched historical packages to 98% would be a separate testing project and
  exclusions would misrepresent quality.
  Date/Author: 2026-07-11 / Codex.

## Outcomes & Retrospective

Studio now opens on an incident-first operational overview and uses a compact,
route-aware shell with semantic active navigation, environment scope, global
task search, and service-alert context. Task filters are labelled, shareable in
the URL, restorable, and resettable. Failed-task details put failure reason,
correlation, service context, and next action before the deferred graph. Service
detail presents overview/observe/configure anchors and separates safe actions
from lifecycle and destructive operations.

The initial production JavaScript chunk fell from 504.86 KB minified (149.93 KB
gzip) to 219.49 KB (70.15 KB gzip), a 56.5% minified reduction. Routes now emit
1.53-35.71 KB chunks, while the graph dependency is isolated in a 177.09 KB
on-demand chunk. The greater-than-500-KB warning is gone.

Backend event, registry, health, and search hydration now use batched `MGET`
operations while preserving routes, response shapes, Redis keys, document
formats, and result ordering. A real 25-service read used one `SMEMBERS` and
three `MGET` calls. A real 20-event read plus task search used one `LRANGE`, one
registry-validation `GET`, one `SMEMBERS`, and two `MGET` calls rather than an
item-by-item read pattern.

Verification passed with 416 SDK tests, 169 Studio backend tests, and 50 Studio
frontend tests. Formatting, lint, type checking, freeze tests, both production
Docker builds, `git diff --check`, and browser smoke testing passed. Backend and
frontend changed executable lines each reached 100% diff coverage, exceeding
the requested 98%; the new overview and graph modules each reached 100%
statements, functions, and lines. Whole-package coverage remains approximately
86% backend and 76.56% frontend because of inherited, untouched gaps; no files
or branches were excluded to inflate those figures.

The disposable real environment used Redis 7, RabbitMQ 4 with management,
production Studio backend/frontend images, and three mock SDK services. It
verified 25 registered services, 20 retained events, task search, frontend HTTP
delivery, and an actual SDK task progressing through processing/completed
history and SSE. A headed-browser walkthrough verified overview, services,
failed-task incident context, deferred graph loading, and a 390 by 844 mobile
layout. All smoke containers and generated coverage artifacts were removed.

A visual follow-up replaced the plain `R` tile with a minimal teal/orange routed
mark and changed the sharp full-bleed header into a floating rounded surface.
The generated source used a flat chroma background that was removed locally;
the checked-in 256px PNG has transparent corners and is rendered without an
enclosing tile. The refreshed production container has no browser console
issues, and a 390px viewport reports equal 390px client and document widths.

## Context and Orientation

Relayna Studio is the control plane in two workspaces. The FastAPI backend lives
under `/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/`; its Redis
stores retain service registry, health, event, and search state and federate
requests to registered Relayna SDK services. The React/Vite frontend lives under
`/Users/jobz/Works/relayna/apps/studio/src/` and only calls existing `/studio/*`
routes through `api.ts`.

The main backend hot paths are `RedisStudioEventStore.list_events`,
`RedisServiceRegistryStore.list`, `StudioHealthRefreshService.build_service_records`,
and document hydration in `StudioSearchService`. The frontend eagerly imports
all route pages and graph rendering through `App.tsx` and `ui.tsx`. The recurring
`AppHeader` hero precedes every route. `TaskSearchPage.tsx` owns search filter
state, while `TaskDetailPage.tsx` renders status, history, DLQ, logs, metrics,
traces, timeline, and execution graph evidence.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.26`; strict production policy
boundary `v1.4.21`. The user explicitly approved breaking the freeze perimeter.
Backend route paths and response shapes, Redis keys and values, and delivery
semantics will remain unchanged. If the frontend redesign adds a page module or
other frozen frontend surface, update only the corresponding frontend manifest
entry and cover it with production-freeze and behavioral tests.

## Plan of Work

First capture the baseline: run existing Studio tests, measure backend coverage,
build the frontend and record chunk sizes, inspect Docker, and add focused
command-count tests around the identified Redis paths.

For backend events, fetch only the bounded list window already implied by
`history_maxlen` and hydrate it with one `MGET`, filtering missing/corrupt values
without changing ordering. Add batch-read helpers inside concrete Redis stores
where they do not change exported module symbols. For registry, health, and
search, hydrate independent records in batches and use a bounded concurrency
helper for remote service/provider operations. Tests will prove command counts,
ordering, partial/missing-data behavior, and concurrency caps.

For the frontend, move graph-only imports and graph layout code out of `ui.tsx`,
lazy-load all route modules in `App.tsx`, and provide an accessible loading
fallback. Replace `AppHeader` with a compact persistent shell containing brand,
active navigation, global task search, and an explicit environment scope. Add
shared CSS-backed primitives where they make active state, labels, density, and
responsive layout consistent. Keep page module filenames and frontend API/type
exports frozen.

Update Task Search so every control has a visible label and its values initialize
from and synchronize to URL query parameters, with clear/reset behavior and
status/stage chips in results. Reorganize task detail above the fold around task
identity, failure reason, correlation/service context, and the next useful action;
defer graph rendering until its investigation section is requested. Improve the
service detail header and safe action hierarchy within existing page behavior,
favoring presentation changes over new endpoints.

Finally run the mandatory verification stack, frontend tests/coverage/build,
backend diff coverage with a 98% fail-under, production freeze tests, and a real Docker
environment. The real-stack scenario will start disposable Redis and RabbitMQ,
run the Studio backend/frontend images, seed or publish representative data using
existing public behavior, and verify health plus critical `/studio/*` and browser
flows. It will collect latency/command evidence when the environment permits.

## Concrete Steps

Run from `/Users/jobz/Works/relayna` unless noted:

    make -C studio/backend test
    cd studio/backend && uv run pytest --cov=relayna_studio --cov-report=term-missing tests
    make -C apps/studio test
    make -C apps/studio build
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Docker commands will use the repository Makefile and Compose assets discovered
during implementation. Disposable service names and networks must be unique and
removed after validation.

## Validation and Acceptance

All existing and new SDK, Studio backend, and Studio frontend tests must pass.
Studio backend and frontend changed executable lines must report at least 98%
coverage. Changed frontend behavior must have direct tests and new frontend
modules must reach 98% statements, functions, and lines. Production freeze tests
must pass; the frontend page-list manifest change is intentional and covered by
the user's explicit perimeter-break approval.

Backend performance tests must demonstrate a bounded number of Redis round trips
for event, registry, health, and search hydration rather than one `GET` per item.
Concurrency tests must prove caps and stable output order. The frontend build must
remove the previous greater-than-500-KB single-chunk warning and emit async route
and graph chunks. Browser tests must prove active navigation, visible labels,
URL-restored filters, keyboard-reachable critical controls, compact mobile
time-to-content, and incident context before the graph.

The Docker smoke environment must verify real Redis connectivity, RabbitMQ
readiness, Studio backend health, frontend proxying, representative registry and
task/event paths, and a critical operator browser flow. Any unavailable external
provider such as Loki, Prometheus, or Tempo will be represented through existing
capability-aware states rather than claimed as live-tested.

## Idempotence and Recovery

All test and build commands are safe to rerun. Redis and RabbitMQ smoke data will
use disposable container names and key prefixes. Container cleanup must run even
after a failed smoke test. No migration or key rewrite is planned, so reverting
the branch restores prior behavior without data recovery. If formatter output
touches unrelated user work, stop and restore only this branch's scoped edits
through a patch rather than destructive Git commands.

## Artifacts and Notes

The source analysis is
`/Users/jobz/Works/relayna/reports/relayna-sdk-studio-analysis.html`. Existing
audit screenshots are under `/Users/jobz/Works/relayna/output/studio-audit/`.
Before/after bundle manifests, coverage summaries, Redis command counts, and
Docker smoke transcripts will be summarized here as work completes.

## Interfaces and Dependencies

No new public SDK or Studio backend export, frontend API/type export, route,
environment variable, Redis key, message protocol, or persisted schema is
required. Internal batching uses the existing `redis` asyncio client. Bounded
parallel work uses Python standard-library `asyncio`. Frontend splitting uses
React `lazy`/`Suspense` and Vite's existing ESM chunking. If Vitest coverage is
not already available, add the matching `@vitest/coverage-v8` development
dependency and lockfile entry only after baseline confirmation.
