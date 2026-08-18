# Studio Entra Login and Role-Based Access

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds. This document follows `PLANS.md` at the repository root.

## Purpose / Big Picture

Relayna Studio currently trusts every browser that can reach its backend. This
change makes Microsoft Entra sign-in mandatory for human users, gives active
users either an `admin` or `readonly` role, and adds an administrator screen for
approving and blocking users. Machine integrations used by Relayna Gateway and
the SDK remain network-only. Operators can observe the result by signing in to
the real Studio UI, approving a pending user, and confirming that readonly users
can inspect data but cannot mutate it.

## Progress

- [x] (2026-08-18 00:00Z) Approved the v1.5.0 compatibility and production-freeze exception.
- [x] (2026-08-18 00:00Z) Created `codex/studio-entra-rbac` from current `origin/main` before edits.
- [x] (2026-08-18 16:35Z) Implemented backend OIDC, Redis membership/session storage, authorization, and API tests.
- [x] (2026-08-18 16:38Z) Implemented frontend authentication states, role-aware navigation, access management, and tests.
- [x] (2026-08-18 16:40Z) Reused the Gateway development OIDC issuer and added deployment documentation, synchronized v1.5.0 metadata, and intentional freeze manifests.
- [x] (2026-08-18 16:44Z) Completed real-browser Computer Use QA with the Gateway issuer.
- [x] (2026-08-18 16:52Z) Passed the mandatory repository verification, coverage targets, frontend build, strict docs build, release validator, and Studio Docker image builds.
- [x] (2026-08-18 16:55Z) Cleared the first CI run's dependency and backend-image security findings by locking cryptography 50.0.0 and applying current Debian security updates during the runtime image build.
- [x] (2026-08-18 16:55Z) Opened ready PR #121, posted the single Codex review request, and received its two actionable threads.
- [x] (2026-08-18 16:58Z) Fixed the active-admin identifier mismatch and local Docker OIDC example with focused regression coverage; the full verification stack passes.
- [ ] Push the review fixes, reply to and resolve both threads, and leave required checks green.

## Surprises & Discoveries

- Observation: The repository has no existing human authentication boundary;
  human APIs and machine routes share the `/studio` prefix.
  Evidence: route factories in `studio/backend/src/relayna_studio/` expose both
  UI endpoints and `/studio/gateway/services` plus `/studio/ingest/events`.
- Observation: Gateway's committed development issuer accepts one configurable
  browser callback and validates the same PS256 certificate assertion expected
  by Studio, so it can exercise Studio without a duplicate fixture.
  Evidence: a real Chrome run used the issuer at `127.0.0.1:18090`, the Vite
  origin at `127.0.0.1:5173`, and the Studio backend at `127.0.0.1:8000`.
- Observation: Composite member IDs are required even with a single configured
  tenant so persisted identity is explicitly tenant plus immutable object ID.
  Evidence: the UI mutation log used the encoded ID
  `tenant-id:object-id`, while display email remained mutable metadata.
- Observation: Security advisory data changed between local verification and
  the first CI run: the root lock still selected cryptography 49.0.0 and the
  mutable Python slim base contained nine fixable util-linux findings.
  Evidence: CI run 677 reported PYSEC-2026-3552 and CVE-2026-53615; the patched
  dependency audits and CI-equivalent image scan report no fixable findings.

## Decision Log

- Decision: Treat v1.4.32 as the latest released compatibility boundary and
  intentionally publish the approved breaking Studio behavior as v1.5.0.
  Rationale: Mandatory login changes released route behavior, and the user
  explicitly approved breaking the production freeze.
  Date/Author: 2026-08-18 / Codex.
- Decision: Mirror Relayna Gateway's confidential OIDC BFF pattern while using
  Studio-specific Redis keys, sessions, callback, and certificate.
  Rationale: This keeps tokens out of the browser and avoids sharing private
  key material between deployments.
  Date/Author: 2026-08-18 / Codex.
- Decision: Keep the Gateway catalog export, push event ingestion, metrics, and
  operational probes outside cookie authentication.
  Rationale: These are existing machine contracts and have no browser session.
  Date/Author: 2026-08-18 / Codex.
- Decision: Reuse the sibling Gateway development OIDC issuer for real-browser
  QA instead of adding a duplicate issuer to this repository, and wait for only
  the first requested Codex review.
  Rationale: The user explicitly narrowed the development and review workflow;
  backend unit tests will still use isolated mocked discovery/JWKS responses.
  Date/Author: 2026-08-18 / Codex.

## Outcomes & Retrospective

Studio now requires certificate-backed Entra login for human routes, resolves
role and lifecycle state from Redis on every request, preserves the four
network-only machine routes, and exposes access administration only to active
administrators. The frontend covers signed-out, pending, blocked, expired, and
authorization-error states while preserving read pages for readonly users.

Repository verification passed with 678 SDK tests (7 skipped), 253 Studio
backend tests, 98% backend coverage, and 104 Studio frontend tests. Frontend
coverage reached 98.09% statements, 89.12% branches, 98.46% functions, and
98.01% lines. Formatting, linting, type checking, frontend production build,
strict MkDocs, both Studio Docker images, and `v1.5.0` release metadata also
passed. The first Codex review identified two actionable issues; both now have
verified local fixes and regression coverage. The remaining work is pushing
those review fixes, resolving their threads, and waiting for CI. Production
Entra registration, certificate-secret installation, and deployment remain
documented operator actions.

## Context and Orientation

The Studio backend is the FastAPI service in
`studio/backend/src/relayna_studio/`; `app.py` assembles its runtime and route
factories, while `config.py` maps environment configuration. The Studio
frontend is the React application in `apps/studio/src/`. Both use the same
backend route prefix. Redis already stores Studio registry, event, health, and
search data; authentication will use a new `studio:auth` namespace without
rewriting existing records. Relayna Gateway's committed `main` implementation
in the sibling `relayna-gateway` checkout is the behavior reference.

## Compatibility Boundary

Compatibility boundary: latest release tag v1.4.32. This feature intentionally
changes released Studio human-route behavior from unauthenticated to mandatory
cookie authentication and adds public auth/admin routes, configuration, Redis
records, and frontend types. The user approved that freeze break. Existing
response bodies and existing Redis records remain unchanged, and exact machine
routes remain unauthenticated.

## Plan of Work

Add an authentication module to the backend containing validated Entra OIDC
configuration, certificate-backed client assertions, Redis member/login/session
stores, auth routes, and a default-deny request policy. Integrate its lifecycle
into `app.py` and settings into `config.py`. Add API tests around cryptography,
login transactions, bootstrap, sessions, CSRF, roles, and exempt routes.

Add a frontend authentication provider and API contracts, gate the existing
application on the session result, add pending/blocked/signed-out surfaces, add
an admin access view, and hide mutation controls for readonly users. Add unit
and component tests for each state and role.

Reuse Relayna Gateway's local OIDC/JWKS development issuer for full browser
testing, document the shared Entra app plus separate Studio certificate deployment, update all release
metadata and production-freeze manifests to v1.5.0, and publish the breaking
behavior in the changelog and release docs.

## Concrete Steps

From `/Users/jobz/Works/relayna`:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build
    make studio-docker-build
    python3 scripts/validate-release-metadata.py v1.5.0

Start Redis, the mock issuer, Studio backend, and Studio frontend using the
documented development commands. Use the Computer Use skill in Chrome for the
real sign-in, approval, readonly, blocking, and local-logout flows.

## Validation and Acceptance

An unauthenticated browser sees sign-in instead of Studio data. A configured
bootstrap identity becomes an active administrator after a verified callback.
An unlisted identity becomes pending; an administrator can activate it as
readonly. The readonly user can use every read surface but receives server-side
403 responses for mutations and sees no mutation controls. Blocking the member
takes effect on the next request. Unsafe requests without the session CSRF token
fail. Login transactions are single use, session cookies contain only an opaque
token, and Redis stores only its SHA-256 digest. Gateway catalog export, event
ingestion, metrics, and probes work without a browser cookie. All commands above
complete successfully and the real-browser flow is recorded here.

## Idempotence and Recovery

All auth records live under a new namespace and can be removed independently in
a development Redis instance. Tests use isolated prefixes and fixtures. Failed
verification commands are safe to rerun. The branch is not merged automatically;
review fixes are committed incrementally, and a deployment can roll back to
v1.4.32 without transforming existing Studio records.

## Artifacts and Notes

Browser screenshots and concise QA observations are recorded below. Transient
screenshots are not committed.

Computer Use evidence, 2026-08-18:

- Signed-out Chrome rendered `Sign in to Studio`; choosing Gateway
  Administrator in the Gateway issuer returned to Studio as an active Admin
  with Access navigation.
- An isolated Chrome Incognito session chose Pending Service Owner and rendered
  the pending surface. The admin Access view listed the identity as pending and
  readonly; changing status to active succeeded through the real CSRF-protected
  PATCH route.
- Refreshing the isolated session rendered the full read-only Studio shell.
  Access navigation and New Service were absent while registry reads and empty
  states remained usable.
- The admin changed the active readonly member to blocked. Refreshing that
  member immediately rendered `Access blocked`; local Sign out returned to the
  Studio sign-in screen at the Studio origin without visiting issuer logout.
- Transient screenshots were captured as
  `/tmp/relayna-studio-admin.png` and
  `/tmp/relayna-studio-readonly.png`; neither is tracked or committed.

## Interfaces and Dependencies

The backend adds `/studio/auth/config`, `/studio/auth/login`,
`/studio/auth/callback`, `/studio/auth/session`, `/studio/auth/logout`,
`/studio/admin/users`, and `/studio/admin/users/{user_id}`. It uses
`PyJWT[crypto]`, `httpx`, and Redis. Entra settings are prefixed
`RELAYNA_STUDIO_ENTRA_`; session and login TTLs default to 28,800 and 600
seconds. Roles are `admin` and `readonly`; account states are `pending`,
`active`, and `blocked`. Unsafe cookie-authenticated requests require
`X-CSRF-Token`.
