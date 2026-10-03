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
- [x] Fix credential redaction, including retained records; cover equivalent
  field names and legitimate environment/upload references with regressions.
- [x] Give managed uploads a bounded longer deadline across browser, bridge and
  deployed reverse proxy; preserve normal request limits and cancellation.
- [x] Recognize all supported terminal task aliases and keep their windows fixed.
- [x] Add exact key generation, Secret/Deployment wiring, local setup, validation,
  backup and rotation guidance; prepare coordinated patch version 1.10.1.
- [x] Run focused regressions, security candidate review, full mandatory checks,
  unchanged coverage gates, production/package builds and strict documentation.
- [x] Push and open follow-up PR 133; initial CI and Codex review complete.
- [x] Address all additional confirmed review findings and rerun mandatory checks,
  backend coverage/build and strict docs. Backend 530 passed at 98.12%.
- [ ] Monitor the updated head and land it after final CI/review under the user's
  maintainer authorization.

## Surprises & Discoveries

The next PR review confirmed a RecursionError for hundreds of raw nested form
assignments. A focused API reproduction failed before correction. Bound public
inspection to 32 container/encoding levels and expanded strings to 2 MiB; use an
explicit masked section when a budget or JSON parser depth is exceeded. Omit
upstream diagnostics with an explicit inspection-limit reason if their payload
cannot be safely inspected. Preserve original upstream submissions. Regressions
cover deep forms, parser-deep JSON, expansion-heavy forms and combined URL size.

The second Codex review of PR 133 confirmed credential-bearing URLs and
structured content nested inside form values. All five added source-to-sink
cases reproduced before correction. Pair values now recurse through the shared
redactor; diagnostics collect nested credentials and encoded representations.
Unchanged nonsecret forms retain their original bytes and upstream submissions
remain untouched. Include five additional regressions and rerun required checks.

PR 133 Codex review on d569bc7 confirmed two further cases: uppercase/padded
URLs bypass userinfo masking, and HTTPX inactivity budgets do not establish a
total upload deadline. Four URL cases reproduced before correction. Normalize
only the parsing input (preserving original nonsecret strings), cover retained
and readonly views plus diagnostics, and wrap upload request/response streaming
in `asyncio.timeout(240)` while leaving ordinary requests without this budget.
Seven additional regressions cover these cases, including stream cleanup while
response chunks keep arriving. Re-run mandatory checks and coverage before push.

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

The independent security candidate review identified encoded raw form bodies and
URL query/fragment parameters as additional paths through the same redactor.
Handle these supported encodings recursively and cover them through the API,
including retained records, diagnostics and nonsecret preservation controls.

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

All three review findings are fixed with 35 backend and 20 frontend regression
cases. The mandatory verification stack passes: SDK 686 passed with 9 optional
skips, Studio backend 530 passed, and frontend 253 passed. Unchanged coverage
gates pass at SDK 97.94%, backend 98.12%, and frontend statements 98.07% (Node 26).
Production frontend and Python package builds, release metadata validation and
strict MkDocs compilation pass. A real Nginx deployment accepted a 2 MiB managed
upload and rejected oversized managed uploads and ordinary requests.

A fresh read-only investigator established the credential boundary; the fresh
candidate reviewer exposed encoding bypasses, now covered by passing regressions.
Environment references, pathToken and original upstream requests are preserved.
No live Kubernetes target execution or user secrets were needed. PR 133 is open. Initial CI passed and all subsequent Codex findings are fixed;
updated-head CI/review monitoring and landing remain pending.
