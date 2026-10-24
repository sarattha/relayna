# Fill the monitoring task sidebar

This living ExecPlan follows `PLANS.md`.

## Purpose / Big Picture

The Task explorer in Studio's monitoring workspace should use the sidebar's
remaining height for task rows. Long pages must scroll to their last task
without leaving an unused band below a separately capped list.

## Progress

- [x] (2026-10-25) Inspect the supplied screenshot and released CSS; reproduce
  a 150px task list above a sidebar bottom over 200px lower.
- [x] (2026-10-25) Reproduce the 118px gap with a 50-task page at 1440×720.
- [x] (2026-10-25) Fill the desktop/expanded sidebar using flex sizing; keep
  stacked/mobile lists bounded at 240px.
- [x] (2026-10-25) Pass 35 browser geometry checks, all 267 frontend tests,
  production build, strict docs build, and diff checks; prepare branch delivery.

## Surprises & Discoveries

`apps/studio/src/studio.css` sets the task list to
`max-height: min(440px, calc(100dvh - 490px))` independently of its flex sidebar.
The list can stop hundreds of pixels before the sidebar's bottom. The log
panel and grid establish a taller workspace. A fix must also prevent a long
list's intrinsic content from making the entire workspace thousands of pixels
tall. Screens at or below 1000px stack tasks above logs and should remain bounded.

## Decision Log

- Decision: Use CSS sizing within the existing task sidebar; preserve task
  fetching, 50-item pagination, selection, filters, and all public contracts.
  Rationale: The requested correction concerns layout only. Compare against
  latest release v1.10.1 and the strict v1.4.30 baseline; the same visual bug
  exists in released CSS. This user-authorized fix requires no compatibility
  shim, migration, or freeze manifest update. Implementation-strategy and
  production-freeze-guard were applied. Date/Author: 2026-10-25 / Codex.

## Outcomes & Retrospective

The independent viewport cap is replaced by a flexing list with a zero base
height and a 150px desktop minimum. This prevents task count from expanding the
grid while allowing the list to consume the sidebar's available height. Expanded
Task explorer also fills the dialog's remaining height. Other monitoring layouts
keep their existing display rules; mobile retains a 240px task-list cap.

Thirty-five browser geometry checks passed with 1/10/50 tasks and empty or
200-entry log readers, at 1440/1024/1000/728/390/320px widths. The original
50-task list was 230px tall with a 118px gap; it now uses 348px with zero gap.
A long log reader gives tasks 608px, and a tall expanded view gives 776px or
more. Every case could scroll/focus the last task inside the list. Mobile stays
bounded, and no tested page overflows horizontally. The live local instance
also changed from a 150px list with a 212px gap to a 362px list with zero gap.

All 267 frontend tests, the production build, strict MkDocs build, and
`git diff --check` pass. No JavaScript, API, pagination, or backend change was
needed. Browser geometry supplies meaningful CSS regression evidence; no
jsdom test was added to mirror stylesheet declarations.

## Context and Orientation

`apps/studio/src/monitor-workspace.tsx` renders a grid with a task sidebar and
log reader. The sidebar contains search, status and pagination controls above
`.monitor-task-list`. `.monitor-canvas` and `.monitor-log-panel` establish the
shared row height. Expanded mode fixes the workspace within the browser.
`apps/studio/src/studio.css` controls all these sizes. The monitor is shared by
service, task, and Chamber views, so preserve stacked/mobile behavior.

## Compatibility Boundary

This is a visual correction to released v1.10.1 behavior. SDK exports, Studio
API/type exports, routes, pagination, stored preferences, and backend behavior
remain unchanged. No frozen surface or manifest change is needed.

## Plan of Work

Reproduce the issue with 50 fixture tasks at short and tall desktop sizes.
Change the list's flex sizing so its contents scroll within the remaining
sidebar height, with a small minimum and no desktop viewport-derived cap.
Keep the existing stacked list limit on mobile. Verify expanded Task explorer
uses its available height without changing Logs focus or Investigation.
Document this layout rule in `docs/studio-frontend.md`.

## Concrete Steps

From the repository root, with Node ^24.22.1 on PATH:

    make -C apps/studio test
    make -C apps/studio build
    uv run --frozen --extra dev mkdocs build --strict
    git diff --check

Use T3 preview for the existing local app at http://localhost:5173. Its resize
step previously retained the wrong measured width; use the already established
Playwright CLI fallback for reliable responsive geometry and screenshots.
Keep fixtures and screenshots in ignored `reports/studio-task-list-height/`.

## Validation and Acceptance

With 1, 10, and 50 tasks, the desktop list bottom aligns with the sidebar
bottom, and scroll height exceeds client height when needed. Scroll and focus
the final task and confirm it is visible within the list. The workspace must
remain bounded with a long task page. Repeat for expanded mode and 1440px,
1024px, 1000px, 728px, 390px, and 320px widths, with no page overflow. Stacked
lists remain at most 240px high and logs remain reachable below them. Existing
frontend tests and production build must pass. Browser geometry, rather than
jsdom style assertions, provides regression evidence for this CSS-only change.

## Idempotence and Recovery

The change adds no data writes or migrations. Fixture API interception stays
outside shipped code. Preserve unrelated edits, and revert the CSS commit if
review finds a regression. Reuse the existing branch and local preview.

## Artifacts and Notes

`reports/studio-task-list-height/` contains the before/after fixture scripts,
geometry results, screenshots, and CLI logs. These local artifacts are ignored;
no fixture interceptors or mock data are included in shipped application code.

The supplied screenshot is the original reproduction reference. The initial
local measurement showed list height 150px and a 212px unused gap below it.

## Interfaces and Dependencies

Use existing grid/flex CSS and React markup. No dependency or public interface
is added. Backend/Python verification is unnecessary for CSS and docs edits.
