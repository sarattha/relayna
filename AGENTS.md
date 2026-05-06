# Contributor Guide

This guide helps agents and contributors work in the Relayna repository. It
covers the project layout, local workflows, and checks that must run before
pushing a branch or opening a pull request.

Location: `AGENTS.md` at the repository root.

## Policies & Mandatory Rules

### Mandatory Skill Usage

#### `$code-change-verification`

Run `$code-change-verification` before marking work complete when changes affect
runtime code, tests, packaging, or build/test behavior.

Run it when you change:

- `src/relayna/` or SDK shared utilities.
- `tests/`.
- `studio/backend/src/` or Studio backend shared utilities.
- `studio/backend/tests/`.
- Build or test configuration such as `pyproject.toml`, `uv.lock`,
  `Makefile`, `studio/backend/pyproject.toml`, `studio/backend/Makefile`, or CI
  workflows.

You can skip `$code-change-verification` for docs-only or repo-meta changes
such as `docs/`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, `.codex/`, or
`.github/`, unless a user explicitly asks to run the full verification stack.

#### `$implementation-strategy`

Before changing runtime code, exported APIs, external configuration, persisted
schemas, wire protocols, task/status/workflow contracts, route response shapes,
or other user-facing behavior, use `$implementation-strategy` to decide the
compatibility boundary and implementation shape.

Judge compatibility against the latest release tag, not unreleased branch-local
churn. Interfaces introduced or changed after the latest release tag may be
rewritten directly unless they define a released or explicitly supported durable
external state boundary, or the user explicitly asks for a migration path.

#### `$pr-draft-summary`

When a task finishes with moderate-or-larger changes, invoke
`$pr-draft-summary` in the final handoff to generate the required PR summary
block, branch suggestion, title, and draft description.

Use this by default after runtime code, tests, Studio backend/frontend behavior,
build/test configuration, or docs with behavior impact are changed. Skip it only
for trivial conversation-only work, repo-meta/doc-only tasks without behavior
impact, or when the user explicitly says not to include the PR draft block.

### ExecPlans

Use an ExecPlan when work is multi-step, spans several files, introduces a new
feature, performs a refactor, changes SDK or Studio architecture, affects
compatibility-sensitive behavior, or is likely to take more than about an hour.

Start with the template and rules in `PLANS.md`. Keep the plan self-contained
and update the living sections as work proceeds:

- Progress
- Surprises & Discoveries
- Decision Log
- Outcomes & Retrospective

Call out compatibility risk early only when the change affects behavior shipped
in the latest release tag or a released or explicitly supported durable external
state boundary. Do not treat branch-local interface churn or unreleased post-tag
changes as breaking by default; prefer direct replacement over compatibility
layers in those cases.

If the plan changes public SDK imports, task/status/workflow contracts, route
response shapes, Redis or RabbitMQ persisted/wire formats, serialized state, or
Studio backend/frontend behavior, use `$implementation-strategy` before editing
code and record the decision in the ExecPlan.

If you intentionally skip an ExecPlan for complex work, note why in your
response so reviewers understand the choice.

### Preserve User Work

The working tree may contain local changes that you did not make. Do not revert,
overwrite, or reformat unrelated changes. Read the affected files first and make
the smallest change that satisfies the task.

### Pre-Push and PR Checks

Before pushing a branch or creating a pull request, run formatting, linting, and
type checking for every Python area touched by the change.

For SDK changes under `src/relayna/`, `tests/`, root packaging, or root tooling,
run these commands from the repository root:

```bash
make format
make lint
make typecheck
```

For Studio backend changes under `studio/backend/`, run these commands from the
repository root:

```bash
make -C studio/backend format
make -C studio/backend lint
make -C studio/backend typecheck
```

If a change touches both the SDK and Studio backend, run both command groups
before pushing or opening the PR.

The `$code-change-verification` script runs the SDK and Studio backend
verification stacks in fail-fast order. If a command fails, fix the issue and
rerun the script so every required command passes in sequence.

### Tests

Add or update tests when changing behavior. Run the relevant test suite before
marking code work complete:

```bash
make test
make -C studio/backend test
```

Use focused pytest commands while iterating, but finish with the relevant
Makefile target when practical.

### Compatibility

Relayna is split into a public SDK and a separate Studio control plane. Treat
public SDK imports, task/status/workflow contracts, route response shapes, and
documented behavior as compatibility-sensitive. Call out compatibility risk
early when a change alters released behavior or persisted data shapes.

Use `$implementation-strategy` before editing compatibility-sensitive behavior.
Prefer direct replacement for unreleased branch-local interfaces, and preserve
compatibility or add migration coverage when a change crosses a released SDK,
Studio API, persisted data, or wire-protocol boundary.

## Project Structure Guide

### Overview

`relayna` is the service-side SDK for RabbitMQ, Redis, FastAPI, task processing,
status delivery, workflow topology, DLQ handling, and execution graph support.

`relayna-studio` is the separate control-plane deployment. It has a Python
backend under `studio/backend/` and a React frontend under `apps/studio/`.

### Important Paths

- `src/relayna/`: SDK source package.
- `tests/`: SDK test suite.
- `studio/backend/`: Studio backend package and tests.
- `apps/studio/`: Studio React frontend.
- `docs/`: Documentation source.
- `scripts/`: Repository utility scripts.
- `Makefile`: SDK and cross-workspace helper commands.
- `studio/backend/Makefile`: Studio backend commands.
- `apps/studio/Makefile`: Studio frontend commands.
- `pyproject.toml` and `uv.lock`: SDK dependency and tool configuration.
- `studio/backend/pyproject.toml`: Studio backend dependency and tool
  configuration.

### Package Ownership

- `relayna.topology`: Topology declarations, routing strategies, workflow
  topology shapes, and topology graph helpers.
- `relayna.contracts`: Canonical task, status, and workflow envelopes plus alias
  and compatibility helpers.
- `relayna.rabbitmq`: RabbitMQ client lifecycle, declarations, publish helpers,
  routing resolution, and retry infrastructure.
- `relayna.consumer`: Worker runtimes, handler contexts, lifecycle helpers,
  middleware, and idempotency support.
- `relayna.status`: Redis-backed latest/history storage, status hub, SSE
  delivery, and bounded stream replay.
- `relayna.api`: FastAPI runtime wiring and route factories.
- `relayna.dlq`: DLQ models, Redis-backed indexing, replay orchestration, and
  queue summary helpers.
- `relayna.observability`: Runtime observation events, collectors, exporters,
  execution graph contracts, Mermaid export, and Redis persistence.
- `relayna.workflow`: Workflow policies, transitions, fan-in, lineage, replay,
  and diagnostics.
- `relayna.mcp`: MCP-facing resources, adapters, and operator tools.

## Operation Guide

### Prerequisites

- Python 3.13 or newer.
- `uv` for Python dependency management.
- `make` for repository tasks.
- Node.js and npm for Studio frontend work.

### Setup

Install or refresh SDK dependencies:

```bash
make sync
```

Install or refresh Studio backend dependencies:

```bash
make -C studio/backend sync
```

Install or refresh Studio frontend dependencies:

```bash
make -C apps/studio sync
```

### Development Workflow

1. Create a focused branch for the change.
2. Sync dependencies when setup files change or the environment is stale.
3. Implement the change using existing package boundaries and local patterns.
4. Add or update tests for behavioral changes.
5. Run the relevant tests and mandatory pre-push/PR checks.
6. Keep commits small and use concise, imperative commit messages.
7. When reporting substantial code work as complete, use `$pr-draft-summary`
   unless the documented skip cases apply.

### Common Commands

SDK:

```bash
make format
make lint
make typecheck
make test
make coverage
```

Studio backend:

```bash
make -C studio/backend format
make -C studio/backend lint
make -C studio/backend typecheck
make -C studio/backend test
make -C studio/backend build
```

Studio frontend:

```bash
make -C apps/studio dev
make -C apps/studio test
make -C apps/studio build
```

Docker builds:

```bash
make studio-docker-build
make studio-backend-docker-build
make studio-frontend-docker-build
```

### Pull Request & Commit Guidelines

- Use the template at `.github/PULL_REQUEST_TEMPLATE/pull_request_template.md`.
- Include a concise summary, test plan, and linked issue when applicable.
- Keep pull requests focused on one behavior change, fix, or documentation
  update.
- Use concise, imperative commit messages. Conventional prefixes such as
  `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, and `chore:` are preferred
  when they clarify the change.
- Add or update tests for behavior changes when feasible.
- Update docs for user-facing SDK, Studio, configuration, or operational
  changes.
- Mention compatibility or migration considerations when public behavior,
  persisted data, route responses, task/status/workflow contracts, or wire
  protocols change.
- Run `make format`, `make lint`, and `make typecheck` for SDK changes before
  pushing or opening the PR.
- Run `make -C studio/backend format`, `make -C studio/backend lint`, and
  `make -C studio/backend typecheck` for Studio backend changes before pushing
  or opening the PR.
- Run the relevant test targets before marking the PR ready for review.
- Use `$pr-draft-summary` after substantial code work to prepare a branch
  suggestion, PR title, and draft description.

### Review Process & What Reviewers Look For

- Checks pass for the affected workspaces.
- Tests cover new behavior, bug fixes, and compatibility boundaries.
- Code follows existing package ownership and avoids unrelated refactors.
- Public SDK APIs, Studio backend responses, persisted data, and wire protocols
  preserve compatibility unless the PR clearly explains the breaking change.
- Error handling, retry behavior, idempotency, and async lifecycle behavior are
  explicit where relevant.
- Redis and RabbitMQ keys, queues, exchanges, routing keys, TTLs, and retention
  behavior are intentional and documented when user-visible.
- FastAPI routes return stable response shapes and appropriate status codes.
- Observability changes avoid high-cardinality metrics labels and preserve
  useful task-linked diagnostics.
- Studio frontend changes are usable across expected desktop and mobile
  layouts, with no overlapping text or broken empty states.
- Documentation and examples match the implemented behavior.
- The PR description states what changed, why, how it was verified, and any
  residual risk reviewers should consider.
