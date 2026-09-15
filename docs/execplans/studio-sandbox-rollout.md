# Sandbox Studio and Chamber rollout

## Purpose
Deploy Studio and Ampule Chamber in sdbx AKS via vm-machine01. The user finally selected shared operator-token login instead of Entra or operator access. Chamber retains its internal operator token.

## Progress
- [x] Inspect live versions: Studio 1.4.28 with Redis; Chamber 1.9.0. Stage ACR Studio 1.8.0 without affecting traffic.
- [x] Upgrade Chamber to 1.10.0 with Helm; preserve workspace, token and observe-only RBAC; readiness passes.
- [x] Implement and verify operator authentication: mandatory stack passes; 433 database-backed tests at 98.12% coverage; 131 frontend tests/build; strict docs build. Computer Use verified the sign-in form and successful entry.
- [ ] Land 1.8.1 and wait for existing CI to publish matching Studio images.
- [ ] Back up and migrate existing Studio Redis records into PostgreSQL, deploy Studio, configure Chamber profiles and verify access.

## Surprises & Discoveries
The built Studio 1.8.0 requires Entra and PostgreSQL, neither configured in the old sandbox. The user clarified that sandbox uses an operator token instead of Entra (superseding earlier no-login instructions). VM has no Azure CLI credentials; Kubernetes image pulls work. Existing GitHub CI publishes versioned Studio images after a version bump lands.

## Decision Log
2026-09-15: Apply implementation-strategy and production-freeze-guard. Latest release boundary is v1.8.0; strict policy boundary v1.4.30. Release 1.8.1 with opt-in RELAYNA_STUDIO_AUTH_MODE=operator, retaining Entra by default and rejecting unknown modes. The user explicitly authorized the sandbox authentication change. A token sign-in form exchanges the credential for a Redis-backed, HttpOnly session; CSRF protection, rate limits, credential rotation and shared-operator audit identity remain. No SDK or broker wire changes. Update only the authorized route/config/frontend perimeter and release metadata.

## Validation
Run mandatory SDK/backend verification, database-backed backend coverage, frontend tests/build and strict docs. Exercise operator access, cross-site mutation rejection, default Entra requirements, service record migration and actual pod/image readiness. No load traffic is required for deployment verification.

## Recovery
Original Deployment manifests and Helm values are saved mode 0700 on vm-machine01 under /tmp/relayna-studio-1.8.0-rollout. Keep old Studio serving until prerequisites and backfill validation pass. Snapshot Studio Redis keys before stopping old writers and importing; do not delete source keys. Roll back to recorded old image digests if readiness fails. Preserve Chamber PVC.

## Outcomes & Retrospective
Chamber 1.10.0 is healthy. Studio rollout is in progress.
