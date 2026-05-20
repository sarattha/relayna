# Adaptive Retry Policies

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should support retry decisions that adapt to error type, task context,
attempt count, workflow stage, downstream health, and runtime pressure. Static
retry counts and delays are useful, but production systems often need to stop
retry storms, fast-fail permanent errors, slow down during dependency outages,
and preserve capacity for healthier work.

After this feature, operators and SDK users can define retry behavior as a
policy decision: given a failure and runtime context, choose retry, delay,
dead-letter, requeue, or stop.

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [ ] Confirm freeze boundary and implementation strategy before code changes.
- [ ] Inventory current `RetryPolicy` behavior and status/DLQ contracts.
- [ ] Design adaptive retry decision inputs and outputs.
- [ ] Implement policy evaluation, tests, and Studio visibility.

## Surprises & Discoveries

- Observation: Retry behavior is already centralized around consumer failure
  handling and RabbitMQ retry infrastructure.
  Evidence: `src/relayna/consumer/task_consumer.py` uses retry headers,
  `RetryPolicy`, retry status publishing, and DLQ recording.
- Observation: Workflow topology already has per-stage retry fields.
  Evidence: `src/relayna/topology/workflow.py` defines stage retry settings.

## Decision Log

- Decision: Model adaptive retry as an additive decision layer above existing
  retry infrastructure.
  Rationale: The existing RabbitMQ retry queues and headers should remain the
  transport mechanism while policy decides what to do next.
  Date/Author: 2026-05-20 / Codex.
- Decision: Keep policy decisions deterministic and serializable.
  Rationale: Retry decisions must be testable, observable, and explainable in
  status history, DLQ records, and Studio.
  Date/Author: 2026-05-20 / Codex.

## Outcomes & Retrospective

Planning only. No runtime behavior has changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/consumer/task_consumer.py` handles task failures, retry
  scheduling, retry status publication, and DLQ recording.
- `src/relayna/consumer/workflow_consumer.py` should receive equivalent policy
  behavior for workflow stage messages if applicable.
- `src/relayna/rabbitmq/client.py` declares retry queues and publishes retry
  messages.
- `src/relayna/rabbitmq/retry.py` contains retry header helpers.
- `src/relayna/dlq/` stores dead-lettered task evidence and replay state.
- `src/relayna/workflow/policies.py` is a likely home for workflow-aware policy
  concepts.

Definitions:

- Retry decision: the runtime action chosen after a handler failure.
- Retry context: the error, task envelope, retry attempt, stage, worker, and
  runtime pressure inputs available to the decision.
- Permanent error: a failure class that should not be retried.
- Retry storm: repeated failures that increase queue pressure without progress.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. Adaptive retry can affect
task timing, status history, DLQ records, RabbitMQ retry headers, and Studio
response shapes. Use `$production-freeze-guard` and `$implementation-strategy`
before editing code.

Default compatibility posture: preserve existing `RetryPolicy` behavior as the
default path. Add adaptive policies as opt-in configuration or as internal
policy objects that compile down to the existing retry behavior when no adaptive
rules are provided.

## Plan of Work

Define a retry decision model with actions such as `retry`, `dead_letter`,
`requeue`, `fail`, and `ignore`. Include fields for `delay_ms`,
`max_attempts`, `reason`, `policy_name`, and optional structured metadata.

Define retry decision inputs. Minimum inputs should include task type,
workflow/stage, attempt number, exception class, exception message category,
headers, status metadata, lease state if available, and backpressure signals if
available.

Add policy evaluators that are pure functions or small classes. Start with
simple built-ins:

- immediate dead-letter for configured permanent exception types
- exponential or capped backoff by attempt
- circuit-open slowdown for downstream outages
- pressure-aware delay multiplier
- stage-specific overrides for workflow consumers

Wire the evaluator into consumer failure handling before scheduling retry or
DLQ. Emit status and observation metadata explaining the chosen decision.

Extend DLQ records and Studio views with retry decision evidence only as
additive fields after compatibility review.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "RetryPolicy|retry_attempt|retry_headers|dead_letter|DLQ" src/relayna tests studio/backend/src studio/backend/tests
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

- Existing retry behavior is unchanged when no adaptive policy is configured.
- A configured permanent error goes directly to DLQ with a policy reason.
- A transient error receives a computed delay and increments retry metadata.
- Backpressure or downstream health can increase delay without changing
  message body shape.
- Retry decision metadata appears in status, DLQ, or observations only through
  additive fields.
- Tests prove deterministic outcomes for each built-in policy rule.

Expected tests:

- Unit tests for retry decision models and policy evaluators.
- Consumer tests for retry, DLQ, and fail-fast paths.
- Workflow consumer tests for stage-specific decisions.
- Studio backend tests for additive decision evidence if exposed.

## Idempotence and Recovery

Policy evaluation should be deterministic for a fixed context. Publishing a
retry must remain idempotent at the consumer failure boundary where possible.
If a policy backend or signal source is unavailable, the runtime should fall
back to a documented default decision rather than blocking message handling.

## Artifacts and Notes

Candidate decision shape:

    action: retry
    delay_ms: 30000
    reason: downstream_unhealthy
    policy_name: default_pressure_aware_retry
    next_attempt: 3

Avoid adding user-provided arbitrary Python callbacks to public configuration
until the security and compatibility model is explicit.

## Interfaces and Dependencies

Potential SDK interfaces:

- `AdaptiveRetryPolicy`
- `RetryDecision`
- `RetryDecisionContext`
- `RetryDecisionAction`
- `ExceptionRetryRule`
- `PressureAwareRetryRule`

Potential integration points:

- `TaskConsumer(..., retry_policy=...)`
- `WorkflowConsumer(..., retry_policy=...)`
- Runtime policy engine, once available.

