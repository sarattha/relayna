# Production Freeze Test Perimeter

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `PLANS.md`.

## Purpose / Big Picture

Freeze Relayna `v1.4.11` as the production perimeter. After this work, agents
and contributors can add implementation tests or internal refactors, but public
SDK exports, exported function/class signatures, Studio backend exports, route
declarations, and Studio frontend API/type exports cannot drift silently.

## Progress

- [x] (2026-05-17 00:00Z) Established strict freeze policy with the user.
- [x] (2026-05-17 00:00Z) Identified freeze surfaces: SDK subpackages, Studio
  backend package, SDK/Studio route declarations, and Studio frontend API/types.
- [x] (2026-05-17 00:00Z) Added freeze manifests and tests.
- [x] (2026-05-17 00:00Z) Added future-agent freeze skill and `AGENTS.md`
  policy.
- [ ] Run full mandatory verification stack.

## Surprises & Discoveries

- Observation: the SDK package root intentionally exports only `__version__`;
  public use is through subpackages such as `relayna.api`, `relayna.contracts`,
  and `relayna.topology`.
  Evidence: `src/relayna/__init__.py`.
- Observation: Studio frontend has a compact public local contract through
  `apps/studio/src/api.ts` and `apps/studio/src/types.ts`.
  Evidence: exported functions and types in those files.

## Decision Log

- Decision: Use strict freeze mode.
  Rationale: the user selected a production freeze where new public APIs and
  functions require explicit approval, not just breaking changes.
  Date/Author: 2026-05-17 / Codex.
- Decision: Keep guard tests manifest-driven.
  Rationale: reviewed JSON manifests make intentional freeze changes visible in
  PR diffs and avoid hidden dynamic baselines.
  Date/Author: 2026-05-17 / Codex.

## Outcomes & Retrospective

The repository now has perimeter tests and agent instructions that fail on
unapproved public-surface drift. Remaining work is to run the full verification
stack before merging.

## Context and Orientation

Relayna has a public SDK under `src/relayna/`, a Studio backend under
`studio/backend/src/relayna_studio/`, and a Studio frontend under
`apps/studio/`.

Frozen surfaces:

- SDK public exports from package `__all__` values.
- Selected exported SDK signatures that define constructor, function, and route
  factory contracts.
- SDK FastAPI route declarations and capability route IDs.
- Studio backend package exports and FastAPI route declarations.
- Studio frontend API functions, type exports, and route/page files.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.11`; strict production freeze.
Public API removals, signature changes, response-shape breaks, and new exported
APIs/functions require explicit user approval and a compatibility note.

## Plan of Work

Add freeze manifests in `tests/freeze/`, `studio/backend/tests/freeze/`, and
`apps/studio/src/test/`. Add Python and Vitest guard tests that compare current
exports and route declarations to those manifests. Add
`.codex/skills/production-freeze-guard/SKILL.md` and update `AGENTS.md` so
future agents use the guard before feature or public behavior changes.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    make test
    make -C studio/backend test
    make -C apps/studio test
    bash .codex/skills/code-change-verification/scripts/run.sh

## Validation and Acceptance

Acceptance requires:

- SDK freeze tests fail if a public subpackage export is added, removed, or
  renamed without updating the manifest.
- SDK signature tests fail if selected exported constructors/functions change.
- SDK and Studio backend route tests fail if route declarations drift.
- Studio frontend tests fail if exported API functions, type names, or page
  files drift.
- `AGENTS.md` names `$production-freeze-guard` as mandatory for new feature or
  public behavior changes.

## Idempotence and Recovery

The tests and manifests are additive. If a future change intentionally moves the
freeze perimeter, update the relevant manifest in the same PR with explicit user
approval and a compatibility note. Failed tests can be rerun safely.

## Interfaces and Dependencies

No runtime dependencies are added. Python tests use the standard library plus
existing test dependencies. Frontend tests use Vitest and Node `fs`/`path`.
