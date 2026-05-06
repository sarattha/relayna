# Codex Execution Plans (ExecPlans)

This file defines how to write and maintain an ExecPlan: a self-contained,
living specification that a contributor can follow to deliver observable,
working behavior in this repository.

## When to Use an ExecPlan

Use an ExecPlan for multi-step or multi-file work, new features, refactors,
architecture changes, compatibility-sensitive behavior, or tasks expected to
take more than about an hour.

An ExecPlan is optional for small fixes, typos, narrow docs updates, or
single-file changes. If you skip it for substantial work, state why in your
handoff.

## How to Use This File

Read this file before drafting a plan. Start from the skeleton below and embed
all needed context: paths, commands, definitions, environment assumptions, and
acceptance criteria.

While implementing, move directly to the next milestone when possible. Keep the
living sections current at every stopping point so another contributor can
resume from the plan alone.

When scope changes, revise the affected plan sections instead of appending
contradictory notes.

## Non-Negotiable Requirements

- Self-contained and beginner-friendly. Define Relayna-specific terms such as
  SDK, Studio backend, task/status/workflow contracts, topology, DLQ,
  observability event, and execution graph when they matter.
- Living document. Keep Progress, Surprises & Discoveries, Decision Log, and
  Outcomes & Retrospective updated as work proceeds.
- Outcome-focused. Describe what a user or operator can do after the change and
  how to observe it working.
- Explicit acceptance. State behaviors, commands, and observable outputs that
  prove success.
- Compatibility-aware. Record the compatibility boundary when the work touches
  released SDK APIs, Studio API responses, persisted data, serialized state, or
  RabbitMQ/Redis wire behavior.

## Formatting Rules

The default envelope is a single fenced code block labeled `md` when sharing an
ExecPlan in chat. Do not nest other triple-backtick fences inside it; indent
commands, transcripts, and diffs instead.

If a file contains only the ExecPlan, omit the enclosing code fence.

Use blank lines after headings. Prefer prose for plan narrative. Checklists are
permitted only in the Progress section, where they are required.

## Guidelines

Anchor the plan on observable outcomes. For internal changes, specify tests,
sample payloads, logs, metrics, or API responses that demonstrate the behavior.

Name repository context explicitly: full paths, modules, functions, Makefile
targets, working directories, required services, and environment variables.

Keep milestones independently verifiable. Each milestone should advance the
goal, describe the expected result, and name proof that the result works.

Be idempotent and safe. Explain how to retry commands, handle partially applied
changes, and roll back risky steps.

Validation is required. State exact commands and expected results. Use the
existing Makefile targets:

- SDK: `make format`, `make lint`, `make typecheck`, `make test`.
- Studio backend: `make -C studio/backend format`,
  `make -C studio/backend lint`, `make -C studio/backend typecheck`,
  `make -C studio/backend test`.
- Studio frontend: `make -C apps/studio test` and
  `make -C apps/studio build` when frontend behavior changes.

Use `$code-change-verification` for substantial SDK or Studio backend Python
changes. Use `$implementation-strategy` before editing compatibility-sensitive
behavior.

## Living Sections

These sections must be present and maintained:

- Progress: checkbox list with timestamps. Every pause should update what is
  done and what remains.
- Surprises & Discoveries: unexpected behaviors, constraints, bugs, or useful
  evidence discovered during work.
- Decision Log: each meaningful decision, rationale, date, and author.
- Outcomes & Retrospective: what was achieved, remaining gaps, and lessons
  learned compared with the original purpose.

## Prototyping and Parallel Paths

Prototypes are allowed to reduce risk. Keep them additive, clearly labeled, and
validated. Remove or retire prototype code before completing the task unless it
is intentionally part of the final design.

Parallel implementations are acceptable when comparing approaches. Describe how
to validate each path and how to retire the losing path safely.

## ExecPlan Skeleton

```md
# <Short Plan Title>

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

If `PLANS.md` is present in the repo, maintain this document in accordance with
it and link back to it by path.

## Purpose / Big Picture

Explain the user-visible or operator-visible behavior gained after this change
and how to observe it.

## Progress

- [x] (2026-05-06 00:00Z) Example completed step.
- [ ] Example incomplete step.
- [ ] Example partially completed step. Completed: X. Remaining: Y.

## Surprises & Discoveries

- Observation: ...
  Evidence: ...

## Decision Log

- Decision: ...
  Rationale: ...
  Date/Author: ...

## Outcomes & Retrospective

Summarize outcomes, gaps, and lessons learned. Compare the result to the
original purpose.

## Context and Orientation

Describe the current state relevant to this task as if the reader knows nothing
about the codebase. Name key files and modules by full path. Define non-obvious
terms.

Include affected Relayna surfaces when relevant:

- SDK package under `src/relayna/`.
- SDK tests under `tests/`.
- Studio backend under `studio/backend/`.
- Studio frontend under `apps/studio/`.
- Docs under `docs/`.
- Build, packaging, and CI files such as `pyproject.toml`, `uv.lock`,
  `Makefile`, and `.github/workflows/`.

## Compatibility Boundary

State whether the change affects released behavior. If it does, identify the
latest release tag used for comparison and explain the compatibility strategy.

Examples:

- `Compatibility boundary: latest release tag v1.4.10; branch-local interface rewrite, no shim needed.`
- `Compatibility boundary: released Redis status schema; preserve backward reads and add migration coverage.`
- `Compatibility boundary: Studio backend response shape; additive field only, existing fields unchanged.`

## Plan of Work

Describe the sequence of edits and additions in prose. For each edit, name the
file, the area of the file, and what will change.

## Concrete Steps

List exact commands to run, including working directory and expected short
outputs when useful.

Examples:

    cd /Users/jobz/Works/relayna
    make test
    make -C studio/backend test
    make -C apps/studio build

## Validation and Acceptance

Describe behavioral acceptance criteria and the commands that prove them.

Include expected API responses, CLI output, logs, metrics, screenshots, or test
results when relevant.

## Idempotence and Recovery

Explain how to safely rerun steps. Describe how to recover from partial
application, failed migrations, stale local services, or interrupted test runs.

## Artifacts and Notes

Include concise transcripts, diffs, sample payloads, or snippets as indented
examples.

## Interfaces and Dependencies

Prescribe libraries, modules, function signatures, environment variables,
service dependencies, data formats, and API shapes that must exist at the end.
```

## Revising a Plan

When the scope shifts, rewrite affected sections so the document remains
coherent and self-contained. After significant edits, add a short note in the
Decision Log or Outcomes & Retrospective explaining what changed and why.
