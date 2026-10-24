# Adopt the Relayna identity in Studio and repository branding

This ExecPlan follows `PLANS.md`. Keep Progress, Surprises & Discoveries,
Decision Log, and Outcomes & Retrospective current.

## Purpose / Big Picture

Studio, Relayna's operational control plane, will use the approved Mulberry &
Linen colors, Slim Leaf logo, and Relayna Round typeface from the owner's
`/Users/jobz/Works/relayna-design-system` package version 0.2.1. The repository
README and hosted documentation will present the same approved SDK identity.
The finished source will be committed and pushed on `t3/studio-design-system`
for review. GitHub repository settings are outside the requested scope.

## Progress

- [x] (2026-10-25) Add the sun/moon toggle, saved light/dark preference,
  matching logos, keyboard access, cross-tab synchronization, and tests.
- [x] (2026-10-25) All 267 frontend tests, production build, strict docs build,
  reload persistence, keyboard focus/activation, and 320–1440px browser checks
  pass. Prepare the update for the existing review branch.

- [x] (2026-10-25) Verify the explicit light default under light and dark
  system appearances; all 258 frontend tests, production build, and strict
  documentation build pass.

- [x] (2026-10-25) Read design-system guidance and inspect Studio styling.
- [x] (2026-10-25) Apply production-freeze-guard and implementation-strategy.
- [x] (2026-10-25) Copy portable assets/tokens with provenance and SHA-256 manifests.
- [x] (2026-10-25) Replace Studio colors, Round typography, logos, and Chamber overrides.
- [x] (2026-10-25) Update README and documentation branding.
- [x] (2026-10-25) All 258 frontend tests, production build, strict docs build,
  asset hashes, and responsive light/dark visual checks pass.
- [x] (2026-10-25) Commit and push `t3/studio-design-system` to origin;
  prepare the required PR draft handoff.

## Surprises & Discoveries

- Studio uses both orange/teal global styles and a separate forest-green
  Chamber workspace override. Both need semantic token mappings.
- The latest local release tag is v1.10.1; frontend freeze tests already target
  that version. Contributor skill prose still names v1.4.30. This styling change
  preserves both perimeters and requires no manifest edits.
- T3 browser snapshots timed out twice (`Preview automation snapshot timed out
  after 15000ms.`), so the documented Playwright CLI fallback supplied visual QA.
- Browser checks caught a separate Georgia heading override; all interface
  headings now use Round. Logs and terminal ANSI mappings remain monospace.
- The available default Node version is 26; Studio requires ^24.22.1. Use the
  pinned Node 24 executable for installation and verification.

## Decision Log

- Decision: Add an internal React theme provider and a shared sun/moon button
  to the header and auth surfaces. Default to light when no valid preference is
  saved; store explicit light/dark choices in `relayna.studio.theme`. Apply the
  document scope and browser chrome in a layout effect before React paint, and
  select the logo from the same state. Synchronize storage changes across tabs;
  retain an in-memory fallback when storage is blocked. Rationale: The owner
  explicitly requested a light/dark control after the light-default follow-up.
  This adds only frontend presentation and a new browser-local preference;
  no released API/data contract or freeze manifest changes are required.
  Date/Author: 2026-10-25 / Codex.

- Decision: Default Studio to light mode using `data-rn-theme="light"` on the
  initial HTML document. Keep light logo sources and the Linen browser theme
  color independent of system appearance. Rationale: Owner requested light as
  the default after local testing; a theme toggle or persisted preference was not yet requested
  (superseded by the toggle decision above). This directly replaces unreleased branch behavior and leaves all
  frozen contracts intact. Date/Author: 2026-10-25 / Codex.

- Decision: Adopt existing semantic `--rn-*` tokens through Studio's existing
  `--studio-*` variables, initially including system light/dark colors (superseded by the explicit
  light default above). Retain operational
  status meanings, monospace logs, existing routes and interactions.
  Rationale: The user authorized a visual identity change; no SDK, API, durable
  state, or wire behavior changes are needed. Date/Author: 2026-10-25 / Codex.
- Decision: Vendor only needed generated token/font/logo/banner exports with
  source attribution and usage-rights notice, rather than an absolute local npm
  dependency. Rationale: Builds must work on GitHub without the owner's local
  directory. Preserve exported asset geometry and exact anchors/taglines.
  Date/Author: 2026-10-25 / Codex.

## Outcomes & Retrospective

The requested sun/moon control is available in the signed-in header and all
sign-in/access surfaces. Light remains the default without a saved preference.
The selected theme persists across reloads and navigation, synchronizes between
tabs, and keeps logos/browser chrome aligned. Storage-blocked browsers can
still switch for the current page. All 267 tests and both builds pass. Real
browser checks confirmed keyboard switching/focus and usable 44px targets at
1440/728/390/320px in both themes without page overflow. T3 verified the real
local login/header path; Playwright used test fixtures for responsive screenshots
because T3 resize requests repeatedly left the measured viewport at 1440px.
Screenshot artifacts are under ignored `reports/studio-theme-toggle/`.

Follow-up: the owner requested a light default after testing locally. The
initial HTML now explicitly resolves light tokens before first paint, with
matching light logos and browser chrome. The original system-appearance checks
below document the initial adoption; the follow-up is verified under both
system appearances to confirm the default stays light (`#F7F0E8` canvas,
`color-scheme: light`, light logo). All 258 tests and both builds pass again.

Studio now uses the supplied Round font, outlined Studio lockups, and semantic
Mulberry & Linen light/dark tokens. Chamber, shared controls, status badges,
charts, and graph surfaces follow the same system. README and docs use the
approved SDK banner; hosted docs use local Round and Slim Leaf assets. All
258 frontend tests pass, the production bundle builds, and MkDocs builds in
strict mode. Asset hashes match the source exports. Overview was inspected at
1440/728/390/320 px in both themes; auth, Chamber connection, and execution
graph were checked at desktop/mobile sizes. Documentation was checked at
1440/320 px. All checked pages fit their viewport. Operational browser checks
used test fixtures intercepted by Playwright, without a live backend; no mock
or authentication bypass is shipped. Historical operator-tour screenshots are
explicitly identified as the earlier visual theme. The implementation is committed as `e6aee85` and pushed to
`origin/t3/studio-design-system`; this completion record is a documentation-only
follow-up. The built production bundle also loads Round and the correct themed
logo from hashed local assets. No deployment or GitHub settings changes were
requested.

## Context and Orientation

`apps/studio/src/studio.css` owns global theme and Chamber workspace styles.
`ui.tsx`, `auth-context.tsx`, `graph-surface.tsx`, and detail pages contain logos
or inline colors. `README.md` is the GitHub landing page. `mkdocs.yml`,
`docs/index.md`, and `docs/assets/stylesheets/extra.css` control hosted docs.
The supplied design-system package is a source input and will not be modified.

## Compatibility Boundary

Strict production baseline: v1.4.30; latest released comparison: v1.10.1.
Only the explicitly requested visual presentation changes. Preserve SDK exports,
frontend API/type exports, route pages, backend responses, task/health semantics,
configuration, and persisted/wire formats. No freeze manifest changes or data
migrations are required. Relayna Round remains a development font (0.100).

## Plan of Work

Copy semantic tokens, the Round variable WOFF2 face, approved Slim Leaf assets,
and selected SDK banners into consumer asset directories. Keep the upstream
generated CSS unchanged and map consumer variables in authored styles. Replace
hardcoded decorative palettes and Chamber overrides, and map status badges to
the supplied functional success/warning/error/info/neutral colors. Update the
header and auth identity with supplied outlined Studio lockups. Add responsive
light/dark SDK artwork to README/docs and document asset provenance and reuse.

## Concrete Steps

Run from repository root with Node ^24.22.1 on PATH:

    make -C apps/studio sync
    make -C apps/studio test
    make -C apps/studio build

Run the local frontend and inspect it through T3 preview. Use test-only mock
responses for representative operational screens if no backend is running;
do not add demo data or bypass authentication in shipped source. Inspect
1440, 728, 390, and 320 pixel widths in both appearances, including auth,
service cards, graphs, and Chamber where possible. Build docs with MkDocs.

## Validation and Acceptance

Frontend tests and production build pass. The browser loads the bundled Round
font and defaults to Mulberry/Linen under both system appearances, then switches
to Ink/Blossom on request and restores that choice after reload. Logos
retain aspect ratio and the approved outlines. Status text and distinct
functional tones remain available. No new page/API/type exports appear and
freeze tests pass. README assets resolve from repository-relative paths.
The review branch is pushed to origin with a clean working tree.

## Idempotence and Recovery

Copying the same upstream files is safe. Never modify the source design-system
directory or unrelated local changes. Fix failed checks and rerun affected
targets. Roll back individual edits through a review commit if needed; no data
migration or external deployment is part of this work.

## Artifacts and Notes

Local screenshots and diagnostics are retained under ignored
`reports/studio-branding/`; visual checks use fixture data.

Checks: `make -C apps/studio test` (18 files, 258 tests),
`make -C apps/studio build`, `uv run --frozen --extra dev mkdocs build --strict`,
and `git diff --check` passed. Python runtime/build/test tooling was not changed;
the Python code-change-verification stack is outside this frontend-only scope.


Source package: @relayna/design-system 0.2.1, Natural Connection / Mulberry &
Linen, Slim Leaf, Open Signal campaign, Round 0.100. Approved Studio tagline:
“Make distributed work visible.” Approved SDK tagline: “Your logic. Connected
execution.” Existing operational screenshots may document older releases;
clearly identify their historical appearance if retained.

## Interfaces and Dependencies

Use existing React, Vite, and CSS facilities. No runtime package dependency,
network font service, or public API is added. The internal React provider in
`apps/studio/src/studio-theme.tsx` owns the browser-local `relayna.studio.theme`
preference and explicit `data-rn-theme` document scope. Auth and header controls
share this provider; the initial HTML remains light before the app mounts.
