# Adopt Manrope branding and consistent light entry surfaces

This ExecPlan follows `PLANS.md`. Keep the living sections current.

## Purpose / Big Picture

Studio, documentation and repository branding will use the owner's current
Manrope design system. Sign-in and session gates always use light mode without
changing the authenticated workspace's saved preference. Documentation starts
light for first-time visitors, including dark-system visitors, and retains an
explicit light/dark toggle and saved preference. Logos, banners and typography
come from exact current exports rather than redesigned consumer assets.

## Progress

- [x] (2026-10-26) Confirm prepared branch and stable original parent c4a4d084adfeccbdbc3b44c718d0624324701afd.
- [x] (2026-10-26) Read contributor rules and apply implementation-strategy and production-freeze-guard.
- [x] (2026-10-26) Honor pause; interrupt extra review and stop QA without reverting files.
- [x] (2026-10-26) Receive explicit resume with superseding Manrope direction; inspect current design-system 0.4.0.
- [x] (2026-10-26) Replace obsolete work with exact Manrope font, CSS, tokens, logos, SDK banners, favicon, provenance and OFL.
- [x] (2026-10-26) Implement auth light isolation and docs light default/manual toggle with focused regression coverage.
- [x] (2026-10-26) Pass current frontend tests/coverage/build, strict docs build, asset checks and responsive visual QA for Manrope.
- [ ] Commit/push, open/register PR against feat/studio-integration-doctor and handle automatic CI/review.
- [ ] Hand clean stable branch to coordinator for native stack append.

## Surprises & Discoveries

- Initial repository fonts were Portico/Round. The user's resumed instruction
  supersedes the earlier Signal attempt with the current owner source at
  `/Users/jobz/Works/relayna-design-system`, version 0.4.0. This exports official
  Manrope 4.505, variable weight 200–800, upright only, SIL OFL 1.1.
- Current design-system typography assigns Manrope to both sans and legacy
  code-role tokens. It is proportional; code whitespace remains preserved and
  tables retain structural columns. No native italic face is supplied.
- Approved tagline strings are unchanged; the source has rebuilt their artwork
  in Manrope. Refresh logos and SDK banners at their existing consumer paths.
- Docs imported tokens followed the system independently of Material's light
  palette. Align shared tokens before styles load and on manual palette changes.
- Previous T3 screenshots timed out twice with `Preview automation snapshot
  timed out after 15000ms.` Current visual QA must try T3 first and may use the
  documented Playwright fallback if the same failure persists.

## Decision Log

- Decision: Use exact current 0.4.0 exports and pinned font provenance. Copy only
  consumed artwork and the WOFF2 face, preserve generated CSS/artwork, and ship
  OFL in both snapshots and Studio's production public directory. Source package
  remains read-only. Prior Signal validation does not validate this revision.
  Date/Author: 2026-10-26 / Codex.
- Decision: Follow the current source's sole-Manrope typography, including code
  roles, while retaining whitespace, semantic columns, tabular numerals, normal
  tracking and unsynthesized styles. Replace remaining hardcoded font stacks.
  Date/Author: 2026-10-26 / Codex.
- Decision: Keep preference separate from rendered auth theme. One stable theme
  provider is owned by the auth boundary. Loading, signed-out, error, pending
  and blocked gates force light; active workspaces restore the saved choice.
  Auth surfaces expose no theme toggle and never write that saved preference.
  Date/Author: 2026-10-26 / Codex.
- Decision: Reuse Material's supported two-palette toggle and scoped storage.
  Set shared theme before CSS loads and synchronize subsequent palette changes.
  First palette is explicitly light without system-media selection. Logo/banner
  colorways follow that explicit choice rather than the OS setting.
  Date/Author: 2026-10-26 / Codex.
- Decision: Count the interrupted extra Codex review as the single used allowance.
  Do not replace, restart or manually request another review. Handle ordinary
  automatic PR review and CI. Coordinator owns native stack append after handoff.
  Date/Author: 2026-10-26 / Codex.

## Outcomes & Retrospective

The Manrope revision passes all 370 frontend tests, all seven Node tooling
tests, the production build and strict MkDocs build. Coverage is 98.06%
statements, 89.83% branches, 99.06% functions and 98.84% lines, meeting all current
gates. Both snapshots match the current upstream manifest byte for byte;
production includes the exact OFL. No superseded font is shipped.

Production-browser QA inspected login, overview and docs in light/dark at
1440/390/320px, including refreshed artwork. Login with saved/system dark stayed
light; active login/reload restored dark and logout preserved it. Docs first
render frames stayed light under system dark; manual choices survived reload,
instant navigation and OS changes. Code uses the loaded Manrope variable face.
Blocked font requests retained readable, fitting mobile login and docs. Source
text, muted text and primary actions meet 4.5:1 contrast in both themes. These
operational checks used only local fixtures, with no live backend or shipped
bypass. Screenshots/logs are in ignored `reports/manrope/`.

T3 snapshots again timed out twice with the exact error recorded above, so the
documented Playwright CLI fallback supplied the visual evidence. No extra review
was requested after resume. Local and remote parent both remained
c4a4d084adfeccbdbc3b44c718d0624324701afd before PR preparation. No sibling branches
or shared gh-stack metadata have changed.

## Context and Orientation

`apps/studio/src/studio-theme.tsx` owns browser-local appearance and logos.
`auth-context.tsx` gates operational pages on the Studio session. `App.tsx`
contains authenticated routes. `studio.css`, `ui.tsx` and code editors use shared
font tokens. `src/assets/brand` and `docs/assets/brand` contain portable brand
snapshots. README consumes the docs SDK banner. `mkdocs.yml`,
`docs/assets/stylesheets/extra.css`, `docs/assets/theme-init.js` and Material
partials control the docs theme, logo and banner. Source exports are inputs.

## Compatibility Boundary

Latest release comparison: v1.11.0; strict freeze baseline: v1.4.30. The user
explicitly authorizes typography, branding and theme changes to presentation.
SDK exports, frontend API/types, routes, backend behavior, server persistence
and wire formats remain intact. Preserve `relayna.studio.theme` and Material's
supported palette preference. No freeze manifest updates, shims or migrations.
Python code-change-verification is not triggered unless SDK/backend runtime,
tests or their build/test tooling are touched; this change is frontend/docs.

## Plan of Work

Refresh only consumed brand exports and hashes, replace obsolete font imports,
and update current guidance while keeping historical releases/screenshots
accurate. Preserve the auth/theme implementation and regression tests. Verify
prepaint docs behavior, explicit preference and theme-aware artwork. Run all
frontend tests/coverage/build, strict docs build and snapshot hashes. Inspect
actual production pages under both OS appearances and both explicit themes on
desktop/mobile before committing and opening the focused PR.

## Concrete Steps

Run from the prepared worktree with Node ^24.22.1 and Python 3.13:

    make -C apps/studio test
    make -C apps/studio coverage
    make -C apps/studio build
    uv run --extra dev mkdocs build --strict

Use T3 preview first, then the documented fallback when necessary. QA fixtures
may intercept session/service responses, but never ship bypasses or demo data.
Keep local reports and screenshots in ignored `reports/`.

## Validation and Acceptance

All current frontend tests, freeze tests and coverage gates pass. Production
frontend and strict docs builds pass. Fonts/CSS/artwork exactly match the source
manifest; bundled font license and provenance hashes match. Text and code roles
render Manrope with a readable fallback, and no superseded font is shipped.
Under saved/system dark, login/loading remain light, sign-in restores dark, and
logout returns light without overwriting the choice. Docs first paint is light
without a preference; explicit dark survives navigation/reload and can return
to light. Inspect login, operational pages, docs overview/code at 1440, 390 and
320px, including fonts, contrast, artwork, clipping and overflow.

## Idempotence and Recovery

Re-copying verified exports is safe. Preserve unrelated user work and source
package files. Fix failed checks and rerun relevant gates. There is no server
migration. Do not restack siblings or edit shared stack metadata. PR must target
feat/studio-integration-doctor and remain unmerged, without auto-merge.

## Artifacts and Notes

Coordinator: thread 18e948a2-67d9-49b6-91ad-58ee29029145. Send source/workspace,
PR/push/validation milestones and final clean IDLE HANDOFF with exact head,
original parent, path and review accounting.

The only extra review was interrupted during the pause, taskId
`node:delegated-task:command%3Amcp%3A8cb8c0ae-d603-4c20-8c2e-1aab4d550306%3Adelegate-task%3Asignal-single-codex-review-20261026`.
Its final status is interrupted with no pending child runs; allowance 1/1 used.

## Interfaces and Dependencies

Reuse React state, browser storage, Material palette controls and existing
build dependencies. No runtime font service or package dependency. Keep font
and private brand licenses distinct. Unsupported scripts use system fallbacks;
upstream font evidence and local browser QA bound rendering claims.
