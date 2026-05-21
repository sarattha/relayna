# Runtime Policy Engine Plan

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `PLANS.md`.

## Purpose / Big Picture

Relayna will expose a public SDK policy module that centralizes retry decisions
for task and workflow consumers. The first implementation preserves existing
runtime outcomes while creating a stable place for future runtime policy
extensions.

## Progress

- [x] (2026-05-21) Drafted approved implementation plan.
- [x] (2026-05-21) Add public `relayna.policies` module.
- [x] (2026-05-21) Wire task and workflow retry decisions through `RuntimePolicyEngine`.
- [x] (2026-05-21) Update freeze manifests and public import tests.
- [x] (2026-05-21) Add and update policy tests.
- [x] (2026-05-21) Run required SDK verification.

## Surprises & Discoveries

- Observation: `v1.4.11` is the strict freeze perimeter, while the latest local
  tag is `v1.4.12`.
  Evidence: repo policy and `git tag -l 'v*' --sort=-v:refname | head -n5`.

- Observation: The local shell does not expose `python`, but `uv run python`
  works for repo commands.
  Evidence: `python -m json.tool ...` failed with command not found; focused
  `uv run pytest ...` passed.

## Decision Log

- Decision: Add an approved public `relayna.policies` module and leave consumer
  constructor signatures unchanged.
  Rationale: This creates a narrow additive public surface without changing
  existing runtime configuration entry points.
  Date/Author: 2026-05-21 / Codex.

- Decision: Preserve aggregation retry behavior for this pass.
  Rationale: The requested v1 scope is task and workflow retry decisions.
  Date/Author: 2026-05-21 / Codex.

## Outcomes & Retrospective

Implemented a public `relayna.policies` module for retry decisions, wired task
and workflow consumers through the default runtime policy engine, and preserved
existing retry, reject, requeue, and DLQ behavior. The approved additive
production-freeze manifest update records the new public module and selected
signatures. `$code-change-verification` passed across SDK and Studio backend.

## Context and Orientation

Current retry behavior is split between `src/relayna/consumer/task_consumer.py`,
`src/relayna/consumer/workflow_consumer.py`, and the private helper
`src/relayna/consumer/_retry_decision.py`. `RetryPolicy` remains the existing
consumer configuration object for retry infrastructure.

## Compatibility Boundary

Compatibility boundary: strict production freeze `v1.4.11`. This task is an
approved additive public perimeter change. Existing exports, constructor
signatures, route shapes, persisted data, Redis keys, RabbitMQ topology, and
wire headers must remain unchanged.

## Plan of Work

Add `src/relayna/policies/` with public retry decision models, a policy
Protocol, default static policy, runtime engine, and `decide_static_retry`.
Update task and workflow consumers to instantiate the default engine internally
and use it for existing retry decision points. Keep the private
`_retry_decision` module as a compatibility wrapper for internal imports.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    make format
    make lint
    make typecheck
    make test

Run `$code-change-verification` before marking work complete.

## Validation and Acceptance

Unit tests must show unchanged retry, DLQ, reject, and requeue decisions. Public
surface tests must pass with the intentionally updated `relayna.policies`
exports and selected signatures.

## Idempotence and Recovery

All edits are ordinary source/test changes. If validation fails, fix the
specific failure and rerun the focused test before rerunning the full SDK stack.

## Artifacts and Notes

No runtime migrations or external services are required.

## Interfaces and Dependencies

The public API is `relayna.policies`. No new package dependencies are required.
