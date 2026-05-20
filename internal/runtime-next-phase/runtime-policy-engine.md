# Runtime Policy Engine

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should centralize runtime decisions such as lease expiry behavior,
retry action, concurrency limits, queue pressure responses, TTLs, DLQ routing,
and workflow stage controls. A runtime policy engine gives operators and SDK
users a single explainable place to define how the runtime reacts to conditions
instead of scattering decisions across consumers, topology objects, and Studio
configuration.

After this feature, runtime decisions can be evaluated, explained, tested, and
surfaced consistently.

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [ ] Confirm freeze boundary and implementation strategy before code changes.
- [ ] Inventory current policy-like settings across topology, consumers,
  workflow, retry, DLQ, and Studio config.
- [ ] Define minimal policy model and evaluation boundaries.
- [ ] Implement the first policy decisions after dependent signals are stable.

## Surprises & Discoveries

- Observation: Relayna already has several policy-shaped concepts spread across
  workflow, retry, topology, DLQ, and Studio outbound configuration.
  Evidence: `src/relayna/workflow/policies.py`,
  `src/relayna/topology/workflow.py`, consumer retry settings, and
  `studio/backend/src/relayna_studio/federation.py`.
- Observation: A policy engine should probably come after leases,
  backpressure, and adaptive retry.
  Evidence: those features provide the concrete decision inputs and actions a
  policy engine would otherwise have to invent prematurely.

## Decision Log

- Decision: Plan the policy engine as a consolidation layer, not the first
  implementation step.
  Rationale: Relayna needs stable runtime signals and action semantics before
  centralizing decisions.
  Date/Author: 2026-05-20 / Codex.
- Decision: Policy evaluation must produce an explanation.
  Rationale: Operators need to know why the runtime delayed, dead-lettered,
  requeued, throttled, or allowed work.
  Date/Author: 2026-05-20 / Codex.

## Outcomes & Retrospective

Planning only. No runtime behavior has changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/workflow/policies.py` contains workflow policy concepts.
- `src/relayna/topology/workflow.py` contains per-stage settings such as retry,
  timeout, max in-flight, deduplication, and queue argument overrides.
- `src/relayna/consumer/task_consumer.py` and
  `src/relayna/consumer/workflow_consumer.py` apply retry and failure behavior.
- `src/relayna/dlq/` applies replay and dead-letter state transitions.
- `src/relayna/observability/` can expose decisions and explanations.

Relevant Studio areas:

- `studio/backend/src/relayna_studio/config.py`
- `studio/backend/src/relayna_studio/federation.py`

Definitions:

- Policy: configured rules that choose runtime behavior.
- Decision: one evaluated outcome for a specific runtime context.
- Explanation: structured metadata describing which rule matched and why.
- Enforcement: applying a decision to change runtime behavior.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. A policy engine can
change public configuration, runtime behavior, persisted policy state, route
response shapes, and task/status/workflow contracts. Use
`$production-freeze-guard` and `$implementation-strategy` before editing code.

Default compatibility posture: add the engine behind explicit configuration and
preserve existing runtime defaults. Avoid new public exported APIs until the
production perimeter is explicitly approved.

## Plan of Work

Inventory existing policy-like settings and classify them by decision type:

- retry decisions
- lease expiry recovery
- concurrency and max in-flight limits
- workflow stage admission
- backpressure response
- DLQ replay safety
- timeout and TTL handling

Define a minimal policy model that is declarative and serializable. Avoid
arbitrary code execution in configuration. Use explicit rule types and typed
conditions.

Define a `PolicyDecision` shape with `action`, `allowed`, `reason`,
`policy_name`, `rule_id`, `severity`, and optional metadata.

Introduce policy evaluation in one bounded area first, likely adaptive retry or
lease expiry observe-only behavior. Do not attempt to move every runtime
decision into the policy engine in the first implementation.

Emit decisions through observations and optional response fields so Studio and
operators can inspect policy behavior.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "Policy|policy|max_retries|max_inflight|timeout|ttl|replay|outbound_policy" src/relayna studio/backend/src tests studio/backend/tests
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

- Existing runtime defaults are unchanged without explicit policy config.
- One bounded decision path uses the policy engine and emits explanations.
- Invalid policies fail fast with actionable validation errors.
- Policy evaluation is deterministic and covered by unit tests.
- Policy decisions can be observed without high-cardinality metrics labels.
- Studio can display policy decisions if exposed, while preserving old response
  fields.

Expected tests:

- Model validation tests for policy config.
- Evaluator tests for matching, precedence, fallback, and invalid rules.
- Integration tests for the first enforced decision path.
- API or Studio backend tests for additive decision display.

## Idempotence and Recovery

Policy loading should be repeatable and validation should happen before
enforcement. If policy evaluation fails at runtime, the system should choose a
documented fallback action and emit an observation. Policy changes should be
versioned or fingerprinted so operators can correlate decisions with the active
configuration.

## Artifacts and Notes

Candidate policy rule shape:

    id: retry-downstream-outage
    when:
      decision_type: retry
      pressure_kind: dependency_unhealthy
      task_type: document.parse
    then:
      action: retry
      delay_ms: 60000
      reason: downstream_unhealthy

Do not store secrets or arbitrary expressions in policy definitions.

## Interfaces and Dependencies

Potential SDK interfaces:

- `RuntimePolicy`
- `RuntimePolicyRule`
- `RuntimePolicyEngine`
- `PolicyDecision`
- `PolicyDecisionContext`

Potential dependencies on other future plans:

- Runtime backpressure signals.
- Adaptive retry policies.
- Task leases and heartbeat expiry.
- DLQ diagnosis bundles.

