# Release Relayna 1.11.0 with the refreshed Studio interface

This living ExecPlan follows `PLANS.md` and continues the implementation record
in `docs/execplans/studio-design-system.md`.

## Purpose / Big Picture

Publish a coordinated minor SDK/Studio release containing the approved Relayna
identity, Portico typography, light/dark toggle, Gateway-aligned emphasis, and
monitor task-list height fix. Update the changelog and documentation with
current-version screenshots, open a ready-for-review PR, address feedback and
checks, merge it, and publish the release through the existing tag workflow.
The user explicitly authorized PR creation/review, up to two additional Codex
reviews, merging, a minor version bump, and release creation.

## Progress

- [x] (2026-10-25) Inspect release workflows and current remote state.
- [x] (2026-10-25) Start independent Codex review of the committed frontend diff.
- [x] (2026-10-25) Coordinate all package/lock versions at 1.11.0 and update release notes.
- [x] (2026-10-25) Replace nine historical guide screenshots and add desktop
  light/dark and mobile Monitor captures (12 inspected PNGs).
- [x] (2026-10-25) Complete mandatory Python verification, frontend coverage/build,
  release metadata validation and wheel/sdist checks.
- [x] (2026-10-25) Strict docs build passed; the rendered guide loads all 12
  versioned images and the Linen canvas without horizontal overflow.
- [x] (2026-10-25) Second Codex review of immutable head `1a68a40` found no
  actionable issues and confirmed the ANSI fix.
- [x] (2026-10-25) Open and link [PR #135](https://github.com/sarattha/relayna/pull/135)
  ready for review. CI/review monitoring is the next publication gate.
- [ ] Merge once ready, tag the merged commit, and verify the published release
  and its artifacts/images. Stop watching the completed PR.

## Surprises & Discoveries

The latest published release/tag is v1.10.1. Main already contains prepared
1.10.2 security metadata and notes but that version was not released. Consolidate
those pending notes into 1.11.0 instead of documenting a nonexistent release.
The release workflow creates the GitHub Release, SDK artifacts, signed Studio
images, provenance and SBOMs after a `v*` tag is pushed.

## Decision Log

- Decision: Use coordinated version 1.11.0 for SDK, backend, frontend and locks;
  update the backend SDK dependency floor accordingly. Rationale: The user
  requested a minor release and packages share one version line. Includes
  pending security work from main. Date/Author: 2026-10-25 / Codex.
- Decision: Preserve v1.10.1 freeze manifests. Rationale: Typography, theme
  preference, layout and release metadata do not add API/type exports, routes,
  schemas or wire contracts. The user approved these visual changes; there is
  no production perimeter or migration change. Implementation-strategy and
  production-freeze-guard apply; packaging changes require the full
  code-change-verification skill. Date/Author: 2026-10-25 / Codex.
- Decision: Capture the production frontend with synthetic fixture services,
  label the captures as 1.11.0 examples, and retain approved brand assets and
  semantic documentation colors. Rationale: Screenshots must represent the
  final UI without displaying secrets or production data. Date/Author:
  2026-10-25 / Codex.

## Outcomes & Retrospective

Release metadata, screenshots and both independent code reviews are complete.
PR #135 is ready for review; remote checks and publication are pending. Do not claim the release is published until
the tag workflow and release artifacts have been verified.

## Context and Orientation

Versions live in `pyproject.toml`, `uv.lock`, `studio/backend/pyproject.toml`,
`studio/backend/uv.lock`, `apps/studio/package.json` and `package-lock.json`.
`scripts/validate-release-metadata.py` enforces coordinated versions.
`CHANGELOG.md`, `README.md`, and `docs/releases.md` describe installation and
upgrade. `docs/studio-frontend.md` embeds the operator guide screenshots.
`.github/workflows/ci.yml` runs Python/frontend coverage, builds and security
checks; `.github/workflows/docs.yml` deploys docs after merging to main.
`.github/workflows/release.yml` owns artifact/image publication and release
creation. T3 PR watch owns asynchronous check/review notifications.

## Compatibility Boundary

Released baseline: v1.10.1. Maintain SDK/Studio API, route, durable data, and
wire contracts. Only package version/dependency alignment and the approved
frontend presentation/preferences change. No schema migration or freeze
manifest revision. Browsers without a saved theme default to light.

## Plan of Work

Update coordinated metadata and regenerate lock metadata without upgrading
unrelated dependencies. Consolidate unpublished security notes with the Studio
minor-release notes, update current installation links, and document upgrade
and rollback behavior. Capture light/dark, desktop/mobile and operational
screens using the final production frontend; replace old screenshot references.
Run required checks and two read-only Codex reviews as useful, resolve findings,
then open/link the PR ready for review. Use the T3 watcher and resume on events.
Merge normally after readiness is established and publish an annotated v1.11.0
tag on the merged main commit. Verify the release workflow output, SDK assets,
SBOMs, image tags and release notes; never bypass failing required checks.

## Concrete Steps

    make sync
    make -C studio/backend sync
    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio coverage
    make -C apps/studio build
    uv run --frozen --extra dev mkdocs build --strict
    python3 scripts/validate-release-metadata.py v1.11.0
    uv build
    uv run twine check dist/relayna-1.11.0*

Use Node ^24.22.1 for frontend commands. Keep transient logs and fixture scripts
under ignored `reports/`; commit only selected, inspected screenshots under
`docs/assets/images/studio/v1.11.0/`. Use structured PR text or `gh --body-file`.

## Validation and Acceptance

All required checks and relevant coverage gates pass. Current screenshots show
Portico, approved colors and working theme/layout controls with no clipping.
The ready PR is linked to this thread; findings are resolved or explicitly
assessed. The merged main commit carries v1.11.0. Release notes, wheel/sdist,
SBOMs and both Studio images exist, and the release workflow succeeds.

## Idempotence and Recovery

Preserve unrelated work. Do not rewrite existing release tags or force-push
main. Inspect remote state before retrying PR/tag/release writes. Fix failures
on the review branch or a focused follow-up as needed; never publish a second
release just to hide a failed job. No production deployment is requested.

## Artifacts and Notes

Review 1 inspected immutable head `0b55fde54cbca1d6a24c6828ff00e97ca99875d7`
(task client ID `studio-design-system-review-1-0b55fde`). It found P2 dark ANSI
log contrast; fixed with theme-dependent foreground tokens, existing parser
regression assertions, and a rendered audit of all 16 colors. Minimum measured
contrast is 5.03:1 light / 6.34:1 dark. The latest released comparison is v1.10.1;
the review prompt's initial v1.10.2 assumption was corrected in this plan.

Review 2 (`studio-design-system-review-2-1a68a40`) reviewed the complete range
`c7fc37a..1a68a40c15ad94bc88b296f38012b60947f824db`, including coordinated
metadata/docs and unchanged freeze manifests. It returned no actionable findings
and confirmed the first finding resolved. Both user-authorized review rounds
are complete. The subsequent commit records this evidence only.

Verification: SDK format/lint/types and tests passed (688 passed, 9 skipped);
backend format/lint/types and tests passed (513 passed, 21 skipped). Frontend
coverage passed all gates: 267 tests in 19 files; statements 98.01%, branches
89.13%, functions 99.36%, lines 98.65%. Production build, coordinated metadata
validation and SDK wheel/sdist Twine checks passed. Browser task-list geometry
confirmed zero desktop gap, last-task focus visibility, and a bounded mobile
list without page overflow. T3 snapshots failed twice with
`Preview automation snapshot timed out after 15000ms.`; the documented
Playwright fallback captured the production bundle. Selected screenshots and
their fixture limitations are recorded in the versioned image directory.

 User-facing PR/release links belong in the final handoff.

## Interfaces and Dependencies

No new public API or dependency is introduced. Existing GitHub Actions, gh,
uv, Node 24 and the approved design-system snapshot provide the workflow.
