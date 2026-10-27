# Recovery Center

Recovery Center is Studio-owned orchestration over the existing single-failure
retry endpoint. It requires PostgreSQL; Redis-only Studio returns `503` for
recovery operations. The SDK, task envelopes, and RabbitMQ/Redis formats are
unchanged. The user explicitly approved this additive production-perimeter
change; the Studio route, frontend, and feature freeze manifests record it.

## Recover a cohort

In **Failed Tasks**, select **Include in recovery plan** for failures from one
service, then **Prepare recovery**. Alternatively open **Recovery** and enter up
to 200 distinct failure IDs. The exact service, environment, endpoint, failure
IDs, order, queue choices, and payload overrides are pinned when the plan is
saved. Put representative failures first; each batch takes the next pending IDs
in that order. Plans do not depend on Incident Inbox groups.

Preview checks retained payload availability, current failure eligibility, retry
capability, and permitted queues. The original payload must still exist even
when providing an override, following current service retry semantics. Already
retried snapshots are blocked. Queue overrides must name a currently advertised
workflow stage queue on the same service; the original source queue is always
permitted. Existing Studio administrator authorization and upstream credential
checks apply. There are no new operator roles or service queue policies.

Optional overrides are a JSON object keyed by selected failure ID, for example:

```json
{
  "failure-1": {
    "target_queue": "payments.authorize",
    "override_payload": {"task_id": "replacement-1", "amount": 100}
  }
}
```

A payload override replaces the complete payload according to the existing
upstream endpoint. Supply the full intended envelope, including any aliases
required by the service. A preview checks eligibility; it cannot prove that
business side effects are safe. Saving creates a durable plan without retrying
anything. A changed preview must be reviewed again before saving.

Review the next batch (five by default), confirm its service/environment, IDs,
queues, overrides, and rate, and authorize it. Studio automatically pauses when
that batch's dispatch allowance is exhausted. Inspect outcomes, then review and
explicitly authorize the remaining items or another small batch. The configured
interval is 1–60 seconds between new requests **per plan**, shared across Studio
instances. Actual dispatch can be slower because of upstream latency and polling.
It is not a service-wide rate or concurrency limit.

**Pause** stops new claims and preserves the authorized allowance; **resume**
continues that allowance. An exhausted batch requires a new authorization.
**Stop** permanently stops this plan's new dispatch. Already claimed requests
and accepted work continue; none of these controls undo side effects or pause
workers. A blocked preflight item pauses the plan for inspection and is not sent;
other pending items can be authorized in another batch within that plan.
A retryable preflight read or revoked executing actor leaves the unsent item
pending and pauses dispatch; an administrator can resume after repair.

## Read the report

The plan report links each original task and returned replacement task to the
pinned service. Dispatch completion is separate from outcome completion:

- **Requested** means a durable dispatch intent was committed. A process can
  stop between committing it and making the network request.
- **Accepted** means a valid upstream retry receipt was received or reconciled.
- **Observed success/failure** requires fresh terminal task-status evidence.
  Canonical `completed` is success; `failed`, `DLQ`, `retry_exhausted`, and
  `terminal_failed` are failures. Unknown/custom statuses do not imply success.
- **Acceptance unknown** means a request may have been accepted but cannot yet
  be proved. It is never automatically resent.
- **Awaiting evidence** means acceptance is known but fresh terminal evidence
  has not arrived. After 24 hours it becomes **outcome unknown**; reconciliation
  continues, so later evidence can still resolve it.

Current SDK retries can reuse the original task ID. Studio requires a terminal
status timestamp at or after the upstream retry receipt and strictly newer than
its pre-dispatch baseline. Old retained failures never count as replay results.
This is temporal attribution, not a new attempt identifier: concurrent retries
outside Recovery Center, missing/expired evidence, custom terminal statuses,
and unsynchronized service clocks can make attribution uncertain. Use unique
replacement task IDs in explicit payload overrides when appropriate to the
service's contract; Studio never rewrites payload IDs implicitly. Observed task
completion does not independently verify downstream business effects.

Cohort recovery time runs from first batch authorization to the latest observed
success, and appears only when **every selected item** has observed success.
Repeated failures after replay count observed failed items. These measures go
beyond accepted API calls. The page includes a copyable JSON report. Unknowns
remain visible instead of being counted as recovered.

## Durable execution and ambiguous acceptance

PostgreSQL stores `studio_recovery_plans` documents and unique
`studio_recovery_claims` reservations. Short row-lock transactions serialize
controls and progress updates; committed dispatch intent and its audit record
precede the network call. Shared persisted deadlines enforce the per-plan rate.
A restarted worker resumes pending items. An interrupted dispatch intent becomes
unknown after 60 seconds, never pending. No lease expiry permits a resend.

Each request includes a unique Studio marker in the existing retry note.
Reconciliation reads the retained failure receipt and accepts only its exact
marker, valid receipt timestamp, and retried state. If publication succeeded but
the upstream receipt write failed, or somebody overwrote the receipt, acceptance
remains unknown. HTTP errors, timeouts, invalid responses, or missing receipts do
not prove that publication failed. Safe automatic resend requires upstream
idempotency support, which this version does not add.

A failure snapshot can belong to only one recovery plan, including stopped or
completed plans. This deliberately prevents overlap from silently resending it;
review its existing plan instead. It does not serialize the existing individual
retry endpoint or operators calling the service directly. Operators must avoid
concurrent manual retries of these snapshots. Control requests carry an expected
plan version; stale or duplicated authorization returns `409`. Refresh and
inspect progress before deciding what to authorize next. Plan creation carries
a client UUID and is safe to repeat with exactly the same request after response
loss. Audit entries cover creation, controls, dispatch intent/results, and
observed state transitions, with the executing operator retained for background
work. The initiating administrator is revalidated before dispatch.

## API and rollout

The additive Studio routes are:

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/studio/recovery/preview` | Preview an exact selection |
| POST | `/studio/recovery/plans` | Save using `request_id` and `preview_digest` |
| GET | `/studio/recovery/plans?environment=staging&offset=0` | List 50 plans per page |
| GET | `/studio/recovery/plans/{plan_id}` | Read progress and report |
| POST | `/studio/recovery/plans/{plan_id}/control` | `batch`, `pause`, `resume`, or `stop`, with `version` and optional `count` |

Backends with automatic startup migrations apply the bundled chain before
starting Recovery workers. For older releases, or to migrate explicitly before
deployment, run:

```bash
make -C studio/backend db-upgrade
```

The expected schema revision is `0003_recovery`, following
`0003_incident_inbox` in the Studio feature stack. Existing tables and route
responses are preserved. Each backend instance runs a recovery worker; it uses
PostgreSQL claims and reads status from the pinned service. Pending observation
continues after dispatch completes or stops. Unresolved plans are polled fairly;
large numbers of plans or slow upstreams can increase observation/dispatch lag.
Recovery history has no automatic deletion policy in this first version. Monitor
ledger size and include these tables in PostgreSQL backups.

To roll back, stop every new backend instance first, export recovery reports,
and understand any in-flight/unknown requests before deciding on further retries.
`alembic downgrade 0003_incident_inbox` drops recovery history and reservations
while preserving Incident Inbox state;
restore from backup if that loss was unintended. Never downgrade while a
recovery worker is running.

This is a Recovery-only rollback to an intermediate feature schema, requiring a
backend built for exactly `0003_incident_inbox`. It is not sufficient for
published 1.11.0, which expects `0002_load_profiles`; rolling back to that release
also drops Incident Inbox state/history. See the
[coordinated 1.12.0 upgrade and rollback](releases.md#upgrading-to-1120).
