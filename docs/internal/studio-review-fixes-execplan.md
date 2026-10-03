# Studio review fixes and encryption-key setup

This living ExecPlan follows `../../PLANS.md` at the repository root.

## Purpose / Big Picture

Address all three Codex findings posted on merged PR 131, provide copyable
encryption-key setup for local and AKS backends, release patch 1.10.1, and land a
new reviewed follow-up PR after checks and review finish.

## Progress

- [x] (2026-10-03) Inspect PR 131 comments 4172913250, 4172913252 and 4172913256;
  create `codex/studio-review-fixes` from current `origin/main` with a clean tree.
- [x] (2026-10-03) Read implementation, freeze, verification, comment-handling,
  security-fix and PR skills. Launch the required read-only security investigation.
- [ ] Fix credential redaction, including retained records; cover equivalent
  field names and legitimate environment/upload references with regressions.
- [ ] Give managed uploads a bounded longer deadline across browser, bridge and
  deployed reverse proxy; preserve normal request limits and cancellation.
- [ ] Recognize all supported terminal task aliases and keep their windows fixed.
- [ ] Add exact key generation, Secret/Deployment wiring, local setup, validation,
  backup and rotation guidance; prepare coordinated patch version 1.10.1.
- [ ] Run focused regressions, security candidate review, full mandatory checks,
  unchanged coverage gates, production/package builds and strict documentation.
- [ ] Push and open a follow-up PR, monitor reviews/CI, address confirmed issues,
  then land the verified head under the user's maintainer authorization.

## Surprises & Discoveries

Codex review completed after PR 131 was merged. All three findings remain present
on main. The deployed frontend proxy also defaults to a 1 MiB request-body cap;
the existing 128 MiB upload promise needs a scoped proxy allowance as well as a
longer browser/backend deadline. Old retained public snapshots require redaction
at read time; fixing only new writes leaves the disclosure reachable.

## Decision Log

The user explicitly authorizes fixes, a new branch/PR, version/docs/changelog
updates and landing after review. Use patch 1.10.1 on the coordinated package
line. Preserve exported signatures and existing schema/configuration; fix
internal redaction and timeout/status behavior. Advance freeze version labels
and their assertions only, with no new SDK exports or route perimeter.

Use the existing recursive redactor and shared secret predicate; preserve
schema-declared environment-variable references and signed upload pathToken.
Apply the redactor to stored public views as well as new writes. Managed upload
requests receive a separate finite budget, while ordinary reads keep 20 seconds.

## Compatibility Boundary

Latest release tag is v1.9.0; current main is prepared as 1.10.0. The historical
strict freeze is v1.4.30. The user-approved bug fixes preserve SDK exports,
wire formats, PostgreSQL schemas and deployment variables. Stored records remain
readable and become safer to expose; no data migration or compatibility shim.

## Plan of Work

Fix `_chamber_connection.py`, the stored public projection in `load_testing.py`
and cached status projection in `_chamber_api.py`. Adapt internal frontend request
deadlines for the existing managed upload path, align bridge/proxy budgets and
body limits, and extend `monitor-state.ts` terminal recognition. Add behavioral
tests in the owning workspaces. Expand `docs/studio-load-testing.md` and release
guidance, synchronize package/lock/manifest version metadata, and validate all
affected workspaces before the new PR.

## Validation and Acceptance

Client-secret variants cannot appear in admin/readonly JSON, retained snapshots
or validation diagnostics. Environment references and signed file descriptors
retain their meaning. A managed upload can succeed after 20 seconds, still
terminates at its longer deadline, and honors external cancellation; ordinary
reads retain the existing deadline. A deployed frontend accepts fixtures above
1 MiB while rejecting oversized upload bodies and keeping other route limits.
All terminal spellings freeze the task window at the last known timestamp.

Run focused tests, then `bash .codex/skills/code-change-verification/scripts/run.sh`
with isolated PostgreSQL/Redis, SDK/backend/frontend coverage, frontend build,
SDK/backend package builds, release metadata validation and strict MkDocs build.
Check every required GitHub job and the final review before merging the exact
verified head; confirm post-merge CI/documentation publication.

## Outcomes & Retrospective

Implementation and verification pending. No live Kubernetes target execution or
user secrets are needed for these fixes.
