# Issues 72-73 Studio Gateway Export And GHCR Release Images

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `/Users/jobz/Works/relayna/PLANS.md`.

## Purpose / Big Picture

Operators should be able to use Relayna Studio as the source catalog for Relayna
Gateway service imports, and tag releases should publish deployable Studio
backend and frontend images to GitHub Container Registry. The observable result
is a new Studio backend read endpoint that returns Gateway-safe service metadata,
frontend and docs guidance that makes the integration discoverable, and a
release workflow that pushes both Studio images on `v*` tags.

## Progress

- [x] (2026-05-13 16:34+07:00) Read GitHub issues #72 and #73 plus Gateway
  release workflow reference.
- [x] (2026-05-13 16:40+07:00) Recorded compatibility and implementation
  strategy before editing.
- [x] (2026-05-13 21:45+07:00) Add Studio Gateway export models,
  endpoint, and backend tests.
- [x] (2026-05-13 21:49+07:00) Add Studio frontend integration affordance
  and type/API support.
- [x] (2026-05-13 21:52+07:00) Update Studio docs with the import contract
  and GHCR release images.
- [x] (2026-05-13 21:52+07:00) Update release workflow to publish backend
  and frontend GHCR images.
- [x] (2026-05-13 21:59+07:00) Run required formatting, tests, type checks,
  code-change verification, frontend build/test, and local Docker image builds.

## Surprises & Discoveries

- Observation: Issue #72 is paired with Gateway issue #17, but this repo only
  needs the Studio export contract and discoverability path.
  Evidence: `gh issue view 72 --repo sarattha/relayna --json ...`.
- Observation: The existing release workflow builds Python artifacts only and
  has `contents: write` permission, while the Gateway reference adds GHCR login,
  Docker metadata, and build-push actions.
  Evidence: `.github/workflows/release.yml` and Gateway release workflow.
- Observation: The first local Docker build attempt hung in Docker credential
  helper processes while loading image metadata. Retrying with a temporary
  `DOCKER_CONFIG` bypassed the local helper and both images built successfully.
  Evidence: `docker build -f studio/backend/Dockerfile ...` and
  `docker build -f apps/studio/Dockerfile ...` with isolated Docker configs.

## Decision Log

- Decision: Add a dedicated `GET /studio/gateway/services` endpoint rather than
  treating the existing `/studio/services` payload as the Gateway import
  contract.
  Rationale: The existing registry payload includes Studio operational configs
  such as log, metric, and trace settings. A dedicated response keeps the
  Gateway contract narrow and avoids exporting internal integration details.
  Date/Author: 2026-05-13 / Codex.
- Decision: Compatibility boundary is latest release tag `v1.4.11`; the Studio
  API change is additive and leaves existing route shapes unchanged.
  Rationale: Gateway import is a new integration surface. No released fields,
  persisted Redis record shapes, or existing wire formats are removed or changed.
  Date/Author: 2026-05-13 / Codex.
- Decision: Publish two separate GHCR images named
  `ghcr.io/sarattha/relayna-studio-backend` and
  `ghcr.io/sarattha/relayna-studio-frontend`.
  Rationale: The repository already has separate Dockerfiles and Makefile
  targets for Studio backend and frontend images.
  Date/Author: 2026-05-13 / Codex.

## Outcomes & Retrospective

Issues #72 and #73 are implemented locally. Studio now exposes a Gateway-safe
service catalog at `/studio/gateway/services`, the Services page links to that
export, docs describe the field mapping and ownership split, and the release
workflow builds and pushes backend/frontend Studio images to GHCR on `v*` tags.
Verification passed for SDK and Studio backend Python checks, Studio frontend
test/build, and both local Studio Docker image builds.

## Context and Orientation

Studio backend code lives under `/Users/jobz/Works/relayna/studio/backend`.
The service registry is implemented in
`/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/registry.py` and is
mounted by `/Users/jobz/Works/relayna/studio/backend/src/relayna_studio/app.py`.
Registry tests live in
`/Users/jobz/Works/relayna/studio/backend/tests/test_studio_registry.py`.

Studio frontend code lives under `/Users/jobz/Works/relayna/apps/studio`. The
service list page is
`/Users/jobz/Works/relayna/apps/studio/src/pages/ServicesPage.tsx`, shared API
helpers are in `/Users/jobz/Works/relayna/apps/studio/src/api.ts`, and shared
types are in `/Users/jobz/Works/relayna/apps/studio/src/types.ts`.

Release automation is in
`/Users/jobz/Works/relayna/.github/workflows/release.yml`. Existing CI already
builds the Studio Docker images locally.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.11`; additive Studio backend
response shape only. Existing `/studio/services` behavior, persisted Redis
service records, SDK imports, task/status/workflow contracts, and RabbitMQ/Redis
wire formats remain unchanged. The GHCR workflow adds release artifacts without
changing runtime code.

## Plan of Work

Add Gateway export response models in `registry.py`, including service-level
metadata with `studio_service_id`, Gateway-safe name suggestion, display name,
base URL, environment, tags, auth mode, status, capabilities, and a deterministic
default route pattern. Add `GET /studio/gateway/services` to the existing router
so it can reuse registry listing and health attachment without exposing log,
metric, trace, or credential-bearing fields.

Extend registry tests to cover the response shape, deterministic name/route
normalization, URL normalization policy behavior through existing create/update
validation, and omission of secret/config fields.

Add frontend type/API support and a compact Services page affordance that points
operators to the Gateway import endpoint. Keep this visible but non-invasive.

Update Studio backend/frontend docs to document the endpoint, field mapping, and
Gateway ownership split. Update the release workflow with GHCR permissions,
registry env vars, Docker login, metadata, and build-push steps for the two
Studio images.

## Concrete Steps

Run from `/Users/jobz/Works/relayna`:

    make -C studio/backend format
    make -C studio/backend lint
    make -C studio/backend typecheck
    make -C studio/backend test
    make -C apps/studio test
    make -C apps/studio build
    .codex/skills/code-change-verification/scripts/verify-code-change.sh

## Validation and Acceptance

Acceptance for #72:

- `GET /studio/gateway/services` returns `{ "count": N, "services": [...] }`.
- Each exported service has `studio_service_id`, `name`, `display_name`,
  `base_url`, `environment`, `tags`, `auth_mode`, `status`, `capabilities`, and
  `default_route_pattern`.
- The response excludes `log_config`, `metrics_config`, `trace_config`, health
  internals, and any credential-like config fields.
- Tests cover export shape, name/route normalization, and existing URL policy
  behavior.
- Docs and frontend mention Gateway Admin import from Studio.

Acceptance for #73:

- Release workflow has `packages: write`.
- Release workflow logs in to GHCR on `v*` tag releases.
- Both Studio backend and frontend Dockerfiles are built from repository root
  and pushed with semver, major.minor, and `latest` tags.
- Existing Python release artifact behavior remains intact.

## Idempotence and Recovery

All edits are normal source changes and can be rerun safely. If verification
fails, fix the reported issue and rerun the failing Makefile target, then rerun
the full code-change verification script. The release workflow changes are inert
until a `v*` tag push.

## Artifacts and Notes

Relevant issue URLs:

- https://github.com/sarattha/relayna/issues/72
- https://github.com/sarattha/relayna/issues/73
- https://github.com/sarattha/relayna-gateway/issues/17

## Interfaces and Dependencies

The new Studio endpoint is:

    GET /studio/gateway/services

The release workflow depends on existing Dockerfiles:

    studio/backend/Dockerfile
    apps/studio/Dockerfile
