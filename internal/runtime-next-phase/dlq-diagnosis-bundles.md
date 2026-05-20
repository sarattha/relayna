# DLQ Diagnosis Bundles

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should preserve enough structured evidence at dead-letter time for an
operator to understand what failed, why it failed, how it retried, what worker
owned it, and whether replay is safe. A DLQ diagnosis bundle is a compact,
structured record attached to a dead-lettered task or workflow message.

After this feature, DLQ views and replay tooling can show a failure timeline,
policy decisions, lease history, source envelope metadata, final exception
classification, and suggested replay options.

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [ ] Confirm freeze boundary and implementation strategy before code changes.
- [ ] Inventory current DLQ record schema and Studio DLQ views.
- [ ] Design additive diagnosis bundle schema.
- [ ] Implement bundle capture, persistence, replay display, and tests.

## Surprises & Discoveries

- Observation: Relayna already stores DLQ records and exposes message detail,
  queue summaries, replay, and broker inspection.
  Evidence: `src/relayna/dlq/models.py`, `src/relayna/dlq/store.py`,
  `src/relayna/dlq/service.py`, and `src/relayna/api/capabilities_routes.py`.
- Observation: Studio already has a DLQ view model.
  Evidence: `studio/backend/src/relayna_studio/dlq_view.py`.

## Decision Log

- Decision: Make diagnosis bundles additive to DLQ records.
  Rationale: Existing DLQ records and replay behavior must remain readable and
  stable across deployments.
  Date/Author: 2026-05-20 / Codex.
- Decision: Store diagnosis as structured data, not rendered prose.
  Rationale: Studio, MCP tools, tests, and replay policies need machine-readable
  evidence.
  Date/Author: 2026-05-20 / Codex.

## Outcomes & Retrospective

Planning only. No runtime behavior has changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/dlq/models.py` defines `DLQRecord`, summaries, detail response
  models, and replay result shapes.
- `src/relayna/dlq/store.py` persists DLQ records in Redis.
- `src/relayna/dlq/service.py` summarizes, details, inspects, and replays DLQ
  messages.
- `src/relayna/consumer/task_consumer.py` records DLQ entries after retry
  exhaustion or failure handling.
- `src/relayna/observability/task_timeline.py` and
  `src/relayna/observability/execution_graph.py` can provide timeline context.

Relevant Studio areas:

- `studio/backend/src/relayna_studio/dlq_view.py`
- `studio/backend/src/relayna_studio/federation.py`

Definitions:

- DLQ: dead-letter queue or Redis-indexed dead-letter record for failed work.
- Diagnosis bundle: structured evidence captured when work is dead-lettered.
- Replay safety: whether a dead-lettered item can be retried without duplicate
  side effects or policy conflicts.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. DLQ diagnosis changes
persisted Redis data and API response shapes. Use `$production-freeze-guard`
and `$implementation-strategy` before editing code.

Default compatibility posture: preserve old DLQ record reads and add optional
diagnosis fields. Never require existing records to have diagnosis data. Avoid
changing replay semantics in the same implementation slice unless explicitly
approved.

## Plan of Work

Define a `DLQDiagnosisBundle` model with sections for failure, retry timeline,
lease ownership, runtime pressure, envelope summary, workflow lineage, and
replay guidance.

Capture available evidence at the point `DLQRecord` is created. The first
implementation can include current fields, exception type, final retry attempt,
consumer name, task type, headers, selected status metadata, and policy decision
reason. Later implementations can add lease history and pressure signals.

Persist the bundle as an optional nested field on `DLQRecord`. Ensure Redis
deserialization supports records without the field.

Expose diagnosis in DLQ detail responses. Keep summaries small and include only
high-value diagnosis hints in list responses.

Update Studio DLQ view models to show the bundle as sections: failure, retry,
ownership, replay guidance, and raw envelope metadata.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "DLQRecord|create_dlq_record|record_dead_letter|replay_message|dlq_view" src/relayna studio/backend/src tests studio/backend/tests
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

- New DLQ records include diagnosis when runtime evidence is available.
- Old DLQ records without diagnosis still load, list, detail, and replay.
- DLQ detail responses expose diagnosis through additive fields.
- Diagnosis includes retry attempt history or final retry decision when
  available.
- Studio can display diagnosis without relying on string parsing.
- Replay still uses existing conflict and claim behavior.

Expected tests:

- Model tests for optional diagnosis bundle validation.
- Redis store tests for old and new DLQ record payloads.
- Consumer tests proving diagnosis is recorded on retry exhaustion.
- API and Studio backend tests for additive detail response fields.

## Idempotence and Recovery

Diagnosis capture should tolerate missing evidence. A failure to enrich
diagnosis must not prevent DLQ recording. Replay claim and release behavior must
remain idempotent and must not depend on diagnosis being present.

## Artifacts and Notes

Candidate bundle outline:

    failure:
      exception_type
      message
      terminal_status
    retry:
      attempt
      max_retries
      policy_name
      policy_reason
    ownership:
      worker_id
      consumer_name
      lease_expires_at
    replay:
      recommended_action
      requires_force
      warnings

## Interfaces and Dependencies

Potential SDK interfaces:

- `DLQDiagnosisBundle`
- `DLQFailureDiagnosis`
- `DLQRetryDiagnosis`
- `DLQOwnershipDiagnosis`
- `DLQReplayGuidance`

Potential dependencies on other future plans:

- Task leases and heartbeat expiry for ownership evidence.
- Adaptive retry policies for policy decision evidence.
- Runtime backpressure signals for pressure evidence.

