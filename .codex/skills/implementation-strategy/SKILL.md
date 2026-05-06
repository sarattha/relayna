---
name: implementation-strategy
description: Decide how to implement runtime, API, configuration, schema, and behavior changes in Relayna before editing code. Use when a task changes SDK or Studio public behavior and you need to choose the compatibility boundary, whether shims or migrations are warranted, and when unreleased interfaces can be rewritten directly.
---

# Implementation Strategy

## Overview

Use this skill before editing code when a task changes runtime behavior or
anything that may affect compatibility. The goal is to keep implementations
simple while protecting real released contracts.

Relayna has two main compatibility surfaces:

- The SDK under `src/relayna/`, including public imports, task/status/workflow
  contracts, topology behavior, Redis/RabbitMQ semantics, FastAPI route
  factories, DLQ behavior, observability events, and execution graph shapes.
- Studio under `studio/backend/` and `apps/studio/`, including backend API
  responses, registry behavior, event ingestion, retained task search, and UI
  expectations that depend on backend response shapes.

## Workflow

1. Identify the touched surface.
   - SDK runtime, public API, contracts, wire protocols, route responses,
     persisted Redis data, RabbitMQ topology, workflow state, observability, or
     Studio backend/frontend behavior.
   - Build/test tooling, docs, or examples that describe behavior.

2. Establish the compatibility boundary.
   - Prefer the latest release tag as the released SDK boundary.
   - Check tags with `git tag -l 'v*' --sort=-v:refname | head -n1`.
   - If needed, compare with `git show <tag>:<path>` or
     `git diff <tag>...HEAD -- <path>`.
   - Treat current branch churn after the latest release as unreleased unless it
     represents an explicitly supported durable external state boundary.

3. Decide whether compatibility is required.
   - Required when changing released public imports, constructor signatures,
     documented behavior, external config, environment variables, route response
     shapes, Redis key/value formats, RabbitMQ exchange/queue/routing behavior,
     serialized state, or wire protocols.
   - Usually not required for branch-local interfaces introduced after the
     latest release tag, internal helper refactors, or docs-only corrections.

4. Choose the implementation shape.
   - For unreleased interfaces, prefer direct replacement over aliases, shims,
     feature flags, dual paths, or migrations.
   - For released compatibility boundaries, preserve old behavior when feasible
     and add tests that cover both old and new behavior.
   - For persisted data or serialized state, add explicit migration or
     backward-read behavior and tests before changing the writer shape.
   - For route responses or wire protocols, prefer additive changes when
     clients may depend on existing fields.

5. Plan verification.
   - SDK changes: run `$code-change-verification` or at minimum the SDK stack.
   - Studio backend changes: run `$code-change-verification` or at minimum the
     Studio backend stack.
   - Frontend changes: run the relevant `make -C apps/studio test` or
     `make -C apps/studio build` target.

## Default Implementation Stance

- Keep the patch scoped to the existing package boundary.
- Prefer deleting or replacing unreleased abstractions instead of preserving
  confusing branch-local shapes.
- Do not add compatibility shims unless the old behavior is released, persisted,
  documented, or explicitly requested.
- If review feedback claims a change is breaking, verify it against the latest
  release tag and actual external impact before accepting the feedback.
- If a change truly crosses a released contract boundary, call that out in the
  plan, implementation notes, tests, and final summary.

## When to Stop and Confirm

Stop and confirm the approach with the user when:

- The change would alter behavior shipped in the latest release tag.
- The change would modify durable external data, protocol formats, or serialized
  state.
- The user explicitly asked for backward compatibility, deprecation, or
  migration support.
- There are two plausible implementation paths with meaningfully different API
  or migration costs.

## Output Expectations

When this skill materially affects the implementation approach, state the
decision briefly in the plan or handoff, for example:

- `Compatibility boundary: latest release tag v1.4.10; branch-local interface rewrite, no shim needed.`
- `Compatibility boundary: released Redis status schema; preserve backward reads and add migration coverage.`
- `Compatibility boundary: Studio backend response shape; additive field only, existing fields unchanged.`
