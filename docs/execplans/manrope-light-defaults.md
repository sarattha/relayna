# Adopt Manrope branding and consistent light entry surfaces

This ExecPlan follows `PLANS.md`. Keep the living sections current.

## Purpose / Big Picture

Studio, documentation and repository branding will use the owner's current
Manrope design system. Sign-in and session gates always use light mode without
changing the authenticated workspace's saved preference. Documentation starts
light for first-time visitors, including dark-system visitors, and retains an
explicit light/dark toggle and saved preference. Logos, banners and typography
come from exact current exports rather than redesigned consumer assets.

The resumed release instruction also prepares coordinated v1.12.0 metadata,
changelog and operational docs for the entire six-layer delivery. The coordinator
owns native stack append, readiness audit, ordered merge and tag/release publication.

## Progress

- [x] (2026-10-26) Confirm prepared branch and stable original parent c4a4d084adfeccbdbc3b44c718d0624324701afd.
- [x] (2026-10-26) Read contributor rules and apply implementation-strategy and production-freeze-guard.
- [x] (2026-10-26) Honor pause; interrupt extra review and stop QA without reverting files.
- [x] (2026-10-26) Receive explicit resume with superseding Manrope direction; inspect current design-system 0.4.0.
- [x] (2026-10-26) Replace obsolete work with exact Manrope font, CSS, tokens, logos, SDK banners, favicon, provenance and OFL.
- [x] (2026-10-26) Implement auth light isolation and docs light default/manual toggle with focused regression coverage.
- [x] (2026-10-26) Pass current frontend tests/coverage/build, strict docs build, asset checks and responsive visual QA for Manrope.
- [x] (2026-10-26) Commit/push and open/register PR #142 against feat/studio-integration-doctor.
- [x] (2026-10-26) Prepare coordinated v1.12.0 metadata/locks, changelog, migration/rollback and release docs for all six layers.
- [x] (2026-10-26) Fix verified public-checksum Gitleaks false positives; pinned history scan and five positive/negative canaries pass.
- [x] (2026-10-27) Pass full code-change-verification, SDK/backend coverage, package/tag/lock metadata, frontend coverage/build, strict docs and rollback checks.
- [x] (2026-10-27) Correct Getting Started's two current install links to v1.12.0 after coordinator audit; retain historical references.
- [ ] Handle automatic CI/review on the final head and hand off release readiness.
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
- Release verification uses isolated task-owned PostgreSQL/Redis. The empty
  database correctly failed the exact-schema readiness fixture until migrated;
  install backend dev tools in the selected Python 3.13 environment and apply
  `upgrade head` before the final full-stack rerun. No schema checks were weakened.
- Backend coverage exposed two untested failure paths near the existing 98%
  gate. Add regressions proving a deleted service is rejected before its former
  provider is queried and invalid metric timestamps do not erase valid independent
  queue/worker evidence. Runtime behavior and coverage thresholds stay unchanged.

## Decision Log

- Decision: Prepare coordinated minor v1.12.0 using the published v1.11.0 notes
  and release workflow as structure. Update package/dependency/lock versions and
  freeze labels only, retaining every previously approved contract entry. Runtime
  SDK version is derived from package metadata; no runtime version constant needs
  a new API. Preserve historical notes, screenshot versions and external Chamber
  version requirements. Full verification is now mandatory because packaging and
  freeze tests are touched. Date/Author: 2026-10-26 / Codex.
- Decision: The combined release requires PostgreSQL migrations
  `0002_load_profiles -> 0003_incident_inbox -> 0003_recovery`. A Recovery-only
  downgrade to `0003_incident_inbox` removes only Recovery data and requires a
  backend built for that head; it is not a rollback to published v1.11.0, which
  expects `0002_load_profiles`. Document full rollback data loss explicitly.
  Date/Author: 2026-10-26 / Codex.
- Decision: CI's two Gitleaks findings are exact public token-CSS digests, verified
  against source bytes. Extend the existing inherited generic-rule AND allowlist
  for only the new digest on the two manifest paths; retain real-token detection.
  Validate pinned 8.24.3 history scanning and positive/negative canaries.
  Date/Author: 2026-10-26 / Codex.

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

Automatic review completed on `cefb50edd92cf2186f5c4831191ded411888b856` with no
findings. The subsequent Getting Started correction and this living record are
documentation only; runtime code and validated package metadata are unchanged.
Strict docs and built-page anchor/install-link checks pass for the correction;
wait for hosted CI on the new exact head. Do not represent the earlier automatic review as review
of a later commit or request a replacement review.

Coordinated v1.12.0 release preparation is complete. All package/runtime versions,
backend SDK floor and locks align; seven freeze labels advance with identical
approved contents. Full verification passes with 704 SDK tests (nine existing
environment skips) and 664 backend tests on task-owned PostgreSQL/Redis. SDK
coverage passes at the reported 98%, backend reaches 98.04%, and current frontend
checks retain 370 passing tests and the metrics below. Two focused failure-path
regressions improved the combined backend gate without changing runtime behavior.

SDK wheel/sdist and backend wheel metadata/builds and Twine pass (backend retains
its existing optional long-description warnings). The positional v1.12.0 tag
validator, both lock checks, runtime package versions, dependency audits, pinned
Gitleaks 8.24.3 history scan and five canaries pass. Strict docs and rendered
anchor checks pass. Disposable migration upgrade/downgrade/base/upgrade and both
documented rollback targets passed: Recovery-only preserves Incident/load
profiles; published 1.11.0 rollback also removes Incident; re-upgrade returns
`0003_recovery`. The final PR head remains subject to hosted CI/automatic review
before the coordinator appends the native stack, merges and publishes.

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
supported palette preference. The typography layer needs no shims or migrations; combined release requirements
are recorded below.
Release preparation intentionally advances freeze labels to v1.12.0 without
changing approved entries. The five inherited features already contain their
authorized additive SDK/Studio contracts and Incident/Recovery migrations.
The combined release therefore requires the documented database upgrade. Run
the complete code-change-verification skill and rebuild both Python packages.

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

For release preparation also run:

    bash .codex/skills/code-change-verification/scripts/run.sh
    uv run python scripts/validate-release-metadata.py v1.12.0
    uv build
    make -C studio/backend build
    uv run twine check dist/* studio/backend/dist/*

The metadata script accepts a positional tag, not `--tag`. Use isolated task-owned
PostgreSQL/Redis for backend integration and migration-cycle checks; never use a
sibling's database. Compare freeze JSON after removing only `freeze_version` to
the parent/first-head manifests to prove approved entries remain identical.

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
package files. Fix failed checks and rerun relevant gates. Typography alone needs
no server migration; the coordinated release requires the schema chain documented
above. Do not restack siblings or edit shared stack metadata. PR must target
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
