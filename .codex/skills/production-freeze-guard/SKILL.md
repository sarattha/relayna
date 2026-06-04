---
name: production-freeze-guard
description: Use before adding features or changing public SDK, Studio backend, Studio frontend API/type, route, schema, configuration, persisted data, or wire behavior after the Relayna production freeze. Enforces the strict v1.4.21 freeze perimeter and manifest update rules.
---

# Production Freeze Guard

## Purpose

Relayna `v1.4.21` is the production freeze boundary. Use this skill before
feature work, public behavior changes, route/schema changes, exported API
changes, configuration changes, persisted data changes, or wire protocol
changes.

Strict freeze means no public API removals, signature changes, response-shape
breaks, or new exported APIs/functions unless the user explicitly approves the
perimeter change.

## Workflow

1. Identify whether the change touches a frozen surface:
   - SDK public exports under `src/relayna/`.
   - SDK task/status/workflow contracts, topology, RabbitMQ/Redis behavior,
     FastAPI route factories, DLQ, observability, metrics, or MCP behavior.
   - Studio backend exports, route responses, registry/federation/event/log/
     metric/search/trace behavior, or retained task shapes.
   - Studio frontend API functions, TypeScript contract types, pages, or
     assumptions about backend response shapes.
   - Build/test configuration that controls any frozen surface.

2. If the change touches runtime behavior or a public contract, use
   `$implementation-strategy` before editing code. Record the compatibility
   boundary as `v1.4.21`.

3. Check the freeze manifests and tests:
   - SDK: `tests/freeze/` and `tests/test_production_freeze_*.py`.
   - Studio backend: `studio/backend/tests/freeze/` and
     `studio/backend/tests/test_production_freeze_*.py`.
   - Studio frontend: `apps/studio/src/test/production-freeze-manifest.json`
     and `apps/studio/src/production-freeze.test.tsx`.

4. Do not update a freeze manifest just to make a test pass. A manifest update
   means the production perimeter changed. Require explicit user approval and
   include a compatibility note in the plan, PR summary, or final handoff.

5. Add or update behavioral tests for any approved feature change. Guard tests
   only protect the perimeter; they do not replace feature tests.

6. Before marking code work complete, use `$code-change-verification` for SDK
   or Studio backend Python changes. Run `make -C apps/studio test` and
   `make -C apps/studio build` for frontend changes.

## Default Stance

- Internal-only refactors are allowed when the freeze tests and behavioral tests
  continue to pass.
- Additive public exports, new public functions, new route fields, and new
  frontend API/type exports are blocked by default under strict freeze.
- If the user approves a perimeter change, keep it narrow and update only the
  relevant manifest entries.
