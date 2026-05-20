# Execution Graph Live State

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should make the execution graph reflect live runtime state, not only a
post-hoc reconstruction of task and workflow history. A live execution graph
shows which nodes are queued, running, retrying, waiting, blocked, expired,
dead-lettered, replayed, or complete, and it updates as status, lease, retry,
DLQ, and workflow events arrive.

After this feature, operators can inspect a workflow or task graph and
understand what is happening now, where pressure or failure exists, and what
will likely happen next.

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [ ] Confirm freeze boundary and implementation strategy before code changes.
- [ ] Inventory current execution graph events, summaries, API routes, and
  Studio views.
- [ ] Define live graph state transitions and event inputs.
- [ ] Implement live state projection, APIs, Studio support, and tests.

## Surprises & Discoveries

- Observation: Relayna already exports `ExecutionGraph`,
  `ExecutionGraphService`, and Mermaid rendering.
  Evidence: `src/relayna/observability/execution_graph.py` and
  `src/relayna/api/execution_routes.py`.
- Observation: Studio already has an execution view adapter.
  Evidence: `studio/backend/src/relayna_studio/execution_view.py`.

## Decision Log

- Decision: Treat live state as a projection over existing events first.
  Rationale: A projection can preserve current persisted event contracts while
  adding richer derived state.
  Date/Author: 2026-05-20 / Codex.
- Decision: Keep graph state separate from transport ownership.
  Rationale: Leases, RabbitMQ delivery, status events, and DLQ records are
  inputs to graph state, but the graph should not become the source of truth
  for message handling.
  Date/Author: 2026-05-20 / Codex.

## Outcomes & Retrospective

Planning only. No runtime behavior has changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/observability/execution_graph.py` builds graph nodes, edges,
  summaries, and Mermaid output.
- `src/relayna/observability/events.py` defines runtime observation events.
- `src/relayna/observability/store.py` persists observation events.
- `src/relayna/status/` provides task status history.
- `src/relayna/workflow/run_state.py` models workflow stage state.
- `src/relayna/api/execution_routes.py` exposes execution graph routes.

Relevant Studio areas:

- `studio/backend/src/relayna_studio/execution_view.py`
- `studio/backend/src/relayna_studio/federation.py`

Definitions:

- Execution graph: nodes and edges representing task attempts, workflow stages,
  fan-in, retry, replay, DLQ, and status transitions.
- Live state: the latest derived state for each node and edge.
- Projection: a materialized interpretation of events and records into graph
  state.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. Live graph state can alter
route response shapes, status interpretation, and Studio API/type contracts.
Use `$production-freeze-guard` and `$implementation-strategy` before editing
code.

Default compatibility posture: keep existing graph nodes, edges, and summaries
stable. Add optional `state`, `state_reason`, `updated_at`, and related fields
instead of changing existing meaning.

## Plan of Work

Inventory all event inputs used to build execution graphs today. Identify gaps
for queued, running, retrying, waiting, blocked, lease-expired, dead-lettered,
replayed, and complete states.

Define a state machine for graph nodes and edges. Keep it small and compatible
with current status names where possible.

Add live state projection logic to `ExecutionGraphService` or a neighboring
module. Inputs should include status history, observation events, workflow run
state, retry metadata, DLQ records, lease state, and pressure signals where
available.

Expose live fields in execution graph API responses as optional additive
fields. Update Mermaid rendering only if it can represent live state without
breaking existing output expectations.

Update Studio execution views to show live state, stale indicators, retry
edges, DLQ transitions, and replay links.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "ExecutionGraph|execution_graph|run_state|StatusEvent|Observation|DLQ" src/relayna studio/backend/src tests studio/backend/tests
    make test
    make -C studio/backend test

Full implementation verification:

    make format
    make lint
    make typecheck
    make test
    make -C studio/backend format
    make -C studio/backend lint
    make -C studio/backend typecheck
    make -C studio/backend test

## Validation and Acceptance

Acceptance criteria:

- Existing execution graph API consumers continue to receive current fields.
- Live state fields are additive and deterministic from event inputs.
- A running task appears as running, then complete, retrying, or dead-lettered
  as events arrive.
- A workflow graph shows waiting fan-in and blocked or failed stages.
- Lease expiry and DLQ transitions appear when those features provide inputs.
- Studio can render mixed states without overlapping or broken empty states.

Expected tests:

- Unit tests for graph state transitions.
- Projection tests from representative status and observation event streams.
- API tests for additive live state fields.
- Studio backend tests for execution view transformation.

## Idempotence and Recovery

Projection should be rebuildable from persisted events and records. If newer
inputs such as leases or pressure signals are unavailable, graph state should
fall back to status and observation history. Reprocessing the same event stream
must produce the same graph state.

## Artifacts and Notes

Candidate node states:

    queued
    running
    waiting
    blocked
    retrying
    dead_lettered
    replayed
    succeeded
    failed
    expired
    unknown

Candidate edge states:

    pending
    traversed
    blocked
    retry_path
    replay_path

## Interfaces and Dependencies

Potential SDK interfaces:

- `ExecutionGraphLiveState`
- `ExecutionGraphStateProjector`
- `ExecutionGraphStateReason`
- `ExecutionGraphNodeState`
- `ExecutionGraphEdgeState`

Potential dependencies on other future plans:

- Task leases and heartbeat expiry for expired/running ownership states.
- Adaptive retry policies for retry decision state.
- DLQ diagnosis bundles for dead-letter evidence.
- Runtime backpressure signals for pressure annotations.

