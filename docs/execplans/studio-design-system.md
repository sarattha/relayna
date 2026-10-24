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

- [x] (2026-10-25) Read design-system guidance and inspect Studio styling.
- [x] (2026-10-25) Apply production-freeze-guard and implementation-strategy.
- [x] (2026-10-25) Copy portable assets/tokens with provenance and SHA-256 manifests.
- [x] (2026-10-25) Replace Studio colors, Round typography, logos, and Chamber overrides.
- [x] (2026-10-25) Update README and documentation branding.
- [x] (2026-10-25) All 258 frontend tests, production build, strict docs build,
  asset hashes, and responsive light/dark visual checks pass.
- [ ] Commit and push the review branch; provide the PR draft summary.

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

- Decision: Adopt existing semantic `--rn-*` tokens through Studio's existing
  `--studio-*` variables, including system light/dark colors. Retain operational
  status meanings, monospace logs, existing routes and interactions.
  Rationale: The user authorized a visual identity change; no SDK, API, durable
  state, or wire behavior changes are needed. Date/Author: 2026-10-25 / Codex.
- Decision: Vendor only needed generated token/font/logo/banner exports with
  source attribution and usage-rights notice, rather than an absolute local npm
  dependency. Rationale: Builds must work on GitHub without the owner's local
  directory. Preserve exported asset geometry and exact anchors/taglines.
  Date/Author: 2026-10-25 / Codex.

## Outcomes & Retrospective

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
explicitly identified as the earlier visual theme. Commit/push delivery is the
remaining step.

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
font and renders Mulberry/Linen light colors and Ink/Blossom dark colors. Logos
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
network font service, public API, or theme preference setting is added. Keep
the default system appearance behavior of the supplied semantic tokens.
