# Task Leases and Heartbeat Expiry

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md`.

## Purpose / Big Picture

Relayna should be able to distinguish healthy in-flight task ownership from
dead, stuck, or disconnected workers. A task lease is a time-bound ownership
claim held by the worker currently processing a task. A heartbeat extends that
claim while work is still active. When the lease expires, Relayna can publish
observable state, requeue or fail the task according to policy, and give
operators a deterministic explanation.

After this feature, operators can answer: which worker owns this task, when did
it last heartbeat, when does ownership expire, and what action will Relayna take
if the worker disappears?

## Progress

- [x] (2026-05-20) Created future implementation plan.
- [ ] Confirm freeze boundary and implementation strategy before code changes.
- [ ] Design lease record schema, Redis keys, and ownership transition rules.
- [ ] Implement lease acquisition, heartbeat refresh, expiry detection, and
  recovery behavior.
- [ ] Add SDK and Studio backend tests plus verification commands.

## Surprises & Discoveries

- Observation: Studio already models service-level worker heartbeat freshness.
  Evidence: `studio/backend/src/relayna_studio/health.py` and related tests
  inspect `last_heartbeat_at` for worker health.
- Observation: Task processing and retry paths already centralize around
  `TaskConsumer` and `WorkflowConsumer`.
  Evidence: `src/relayna/consumer/task_consumer.py` and
  `src/relayna/consumer/workflow_consumer.py`.

## Decision Log

- Decision: Treat leases as runtime ownership state, not as a replacement for
  RabbitMQ acknowledgement semantics.
  Rationale: RabbitMQ remains responsible for message delivery. Relayna leases
  add Redis-backed operator visibility and recovery decisions across consumers,
  workflows, and Studio.
  Date/Author: 2026-05-20 / Codex.
- Decision: Plan lease support as opt-in until compatibility is reviewed.
  Rationale: New expiry behavior can change failure timing and duplicate-work
  risk for existing deployments.
  Date/Author: 2026-05-20 / Codex.

## Outcomes & Retrospective

Planning only. No runtime behavior has changed.

## Context and Orientation

Relevant SDK areas:

- `src/relayna/consumer/task_consumer.py` owns task message processing, retry
  status publishing, failure handling, and DLQ recording.
- `src/relayna/consumer/workflow_consumer.py` owns workflow stage processing.
- `src/relayna/status/` stores latest and historical task state.
- `src/relayna/storage/` contains Redis-backed repository utilities and
  workflow contract storage.
- `src/relayna/observability/` emits runtime observations and execution graph
  data.
- `src/relayna/api/health_routes.py` exposes runtime health surfaces.

Relevant Studio areas:

- `studio/backend/src/relayna_studio/health.py` summarizes service and worker
  health.
- `studio/backend/src/relayna_studio/federation.py` proxies service runtime
  capabilities to Studio.

Definitions:

- Lease: a bounded ownership claim for one task or workflow message.
- Heartbeat: a periodic refresh that proves the owner is still active.
- Expiry: the point at which Relayna considers ownership stale and applies a
  configured recovery action.

## Compatibility Boundary

Compatibility boundary: `v1.4.11` production freeze. Lease support changes
runtime behavior, persisted Redis state, and likely Studio route response
shapes. Use `$production-freeze-guard` and `$implementation-strategy` before
editing code.

Default compatibility posture: additive and disabled by default until the team
explicitly approves enabling lease enforcement. Existing consumers should keep
their current acknowledgement, retry, and DLQ behavior unless lease management
is configured.

## Plan of Work

Introduce a lease model in the SDK with fields such as `task_id`, optional
`message_id`, `task_type`, `workflow_id`, `stage`, `owner_id`, `consumer_name`,
`attempt`, `acquired_at`, `heartbeat_at`, `expires_at`, and `recovery_action`.

Add Redis-backed lease storage with atomic acquire, heartbeat, release, and
expire operations. Use short TTLs on lease keys and indexes that support lookup
by task, owner, and expiry time.

Integrate lease acquisition and release into `TaskConsumer` and
`WorkflowConsumer`. The worker should acquire before user handler execution,
heartbeat during long-running execution, and release on success, retry, DLQ, or
known cancellation.

Add an expiry scanner or service hook that can mark expired leases and apply
the configured action. Candidate actions are observe-only, publish stale status,
requeue, retry, or dead-letter. Start with observe-only and explicit status
publication unless the compatibility decision approves automatic recovery.

Expose lease state through SDK health/capability routes and Studio backend
federation. Keep existing fields stable and add new nested lease fields rather
than changing current health shapes.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    rg -n "TaskConsumer|WorkflowConsumer|RetryPolicy|StatusEvent|health" src/relayna studio/backend/src tests studio/backend/tests
    make test
    make -C studio/backend test

Implementation should include focused tests first, then the full required
checks:

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

- A worker acquires a lease before processing and releases it on success.
- A long-running handler refreshes `heartbeat_at` before `expires_at`.
- A crashed or stopped worker leaves a lease that becomes expired and visible.
- Expiry behavior publishes a stable status or observation event.
- Existing consumers behave unchanged when leases are disabled.
- Studio can display lease owner, latest heartbeat, expiry time, and stale
  state without breaking existing health responses.

Expected tests:

- Unit tests for lease model validation and Redis store operations.
- Consumer tests for acquire, heartbeat, release, retry, DLQ, and handler
  exception paths.
- Expiry scanner tests for observe-only and recovery actions.
- Studio backend tests for additive response fields and stale lease rendering.

## Idempotence and Recovery

Lease acquisition must be atomic and safe to retry. Heartbeats from a non-owner
must not extend someone else's lease. Release must tolerate missing or expired
leases. Expiry scanning must be idempotent so repeated scanner runs do not
double-requeue, double-DLQ, or publish duplicate terminal status.

## Artifacts and Notes

Candidate Redis keys:

    relayna:lease:task:{task_id}
    relayna:lease:owner:{owner_id}
    relayna:lease:expiries

Candidate status values or observation kinds must be reviewed against freeze
manifests before becoming public:

    lease_acquired
    lease_heartbeat
    lease_expired
    lease_recovered

## Interfaces and Dependencies

Potential SDK interfaces:

- `TaskLease`
- `TaskLeaseStore`
- `RedisTaskLeaseStore`
- `LeasePolicy`
- `LeaseRecoveryAction`

Potential configuration:

- `lease_enabled`
- `lease_ttl_seconds`
- `lease_heartbeat_interval_seconds`
- `lease_recovery_action`
- `lease_expiry_scan_interval_seconds`

