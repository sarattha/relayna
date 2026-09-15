# Sandbox Studio and Chamber rollout

## Purpose
Deploy Studio and Ampule Chamber in sdbx AKS via vm-machine01. The user finally selected shared operator-token login instead of Entra. Chamber retains its internal operator token.

## Progress
- [x] Inspect live versions: Studio 1.4.28 with Redis; Chamber 1.9.0. Stage ACR Studio 1.8.0 without affecting traffic.
- [x] Upgrade Chamber to 1.10.0 with Helm; preserve workspace, token and observe-only RBAC; readiness passes.
- [x] Implement and verify operator authentication: mandatory stack passes; 433 database-backed tests at 98.12% coverage; 131 frontend tests/build; strict docs build. Computer Use verified the sign-in form and successful entry.
- [x] Landed PR #128 as 8c92eb4; all checks passed and existing CI published Studio 1.8.1 images.
- [x] Backed up 4,123 Studio Redis keys and the fresh PostgreSQL schema; imported 8 services, 1,717 events and 540 task projections with no invalid records. Repeat import returned already_imported with the same checksum.
- [x] Deployed backend/frontend 1.8.1 pinned to verified GHCR digests; both ready. All eight profiles are available. Operator browser login, internal Chamber bearer connection, planning, logs and pod metrics verified. No load traffic started. Removed temporary preflight and migration pods.

- [ ] Release 1.8.2 documentation follow-up: synchronize versions, document reusable deployment configuration, verify, request Codex review once, address findings and land.

## Surprises & Discoveries
The built Studio 1.8.0 requires Entra and PostgreSQL, neither configured in the old sandbox. The user clarified that sandbox uses an operator token instead of Entra (superseding earlier no-login instructions). VM has no Azure CLI credentials; Kubernetes image pulls work. Existing GitHub CI publishes versioned Studio images after a version bump lands.

## Decision Log
2026-09-15: Apply implementation-strategy and production-freeze-guard. Latest release boundary is v1.8.0; strict policy boundary v1.4.30. Release 1.8.1 with opt-in RELAYNA_STUDIO_AUTH_MODE=operator, retaining Entra by default and rejecting unknown modes. The user explicitly authorized the sandbox authentication change. A token sign-in form exchanges the credential for a Redis-backed, HttpOnly session; CSRF protection, rate limits, credential rotation and shared-operator audit identity remain. No SDK or broker wire changes. Update only the authorized route/config/frontend perimeter and release metadata.

## Validation
Run mandatory SDK/backend verification, database-backed backend coverage, frontend tests/build and strict docs. Exercise operator access, cross-site mutation rejection, default Entra requirements, service record migration and actual pod/image readiness. No load traffic is required for deployment verification.

## Recovery
Original Deployment manifests and Helm values are saved mode 0700 on vm-machine01 under /tmp/relayna-studio-1.8.0-rollout. Keep old Studio serving until prerequisites and backfill validation pass. Snapshot Studio Redis keys before stopping old writers and importing; do not delete source keys. Roll back to recorded old image digests if readiness fails. Preserve Chamber PVC.

## Outcomes & Retrospective
Studio 1.8.1 and Chamber 1.10.0 are deployed and ready in sdbx. Studio needed an operator-login addition after the initially built 1.8.0, so this rollout uses the matching GHCR images published by the existing CI rather than the earlier ACR build. Backend digest 14e94defcadc1a5c9bde6d0c2643d5f42aa110ac5e28c28642f5d8e78ea56afc; frontend digest 6540bb967ddb1e43cb0797f0c911257795b3e4cabaa20cab7e6cae3f068b6cb0. Chamber Helm release ampule is revision 5 with retained workspace/token.

Namespace relayna now has ConfigMaps relayna-studio-config and relayna-studio-chamber-profiles, plus Secret relayna-studio-runtime-secrets containing database, Redis, Studio operator and Chamber connection credentials. The dedicated database and role are relayna_studio on postgres.pgsql.svc.cluster.local. Secure browser cookies remain enabled.

Summary imports its supported schema from live OpenAPI; the other seven profiles use constrained snapshots of actual service OpenAPI, excluding unsupported dictionary/nullable multipart fields and pinning existing Chamber upload fixtures. Multipart JSON-string fields remain string fields per those service APIs. Document ingestion requires the operator to supply a test document and its access token. No service load was executed.

Live browser verification used a loopback SSH/Kubernetes tunnel at http://127.0.0.1:18997. The external hostname was not provided, so that access path is not yet verified. A real translation plan (7ff48caf8058420aa0941e2a986c1c47) is ready for manual review. Translation logs returned 200, metrics returned 19 series without warnings, and the pods endpoint returned 15 observations. Existing service health alerts remain visible. Deployment manifests, profiles, migration results and rollback snapshots are retained on vm-machine01 in the protected rollout directory.

## Release follow-up

2026-09-15: The user requested a version bump, changelog/docs update and a PR
with exactly one Codex review request before landing. Use branch
`codex/studio-1.8.2-rollout-handoff`. This is a packaging/documentation patch
relative to 1.8.1: no runtime, API, configuration contract or persisted-format
change. Freeze metadata advances to 1.8.2 with identical public surfaces.
Run the mandatory verification stack, frontend tests/build and strict docs.
The deployed sandbox remains Studio 1.8.1 and Chamber 1.10.0.

Release verification passed: mandatory SDK/backend formatting, lint, types and
tests (SDK 686 passed/9 skipped; backend 417 passed/16 database-dependent skips),
frontend 131 tests and production build, and strict MkDocs build. No runtime
changes were introduced; database-backed coverage for the deployed 1.8.1 runtime
was already verified above. The follow-up PR will receive one Codex review request.
