# Queue & Worker Cockpit

This living ExecPlan follows `PLANS.md`. Keep Progress, Surprises & Discoveries,
Decision Log, and Outcomes & Retrospective current.

## Purpose / Big Picture

Operators open Queue & Workers to explain waiting work using queue pressure,
consumer coverage, individual worker freshness, lease ownership/expiry, active
work and service retry/DLQ trends. Every reading names its source and time;
missing data stays unknown. Investigation links preserve service/environment.
This feature provides visibility only, with no runtime commands.

## Progress

- [x] (2026-10-25) Fetched origin/main; clean feature branch HEAD and merge-base
  are `89954699d2e43aebfee54e4ec64c06edb0caf2ce`.
- [x] (2026-10-25) Read contributor rules, freeze/strategy/verification/PR skills,
  source contracts, operational docs and official broker reference patterns.
- [x] (2026-10-25) Implement additive runtime queue observations and cockpit read aggregation.
- [x] (2026-10-25) Build scoped responsive cockpit and investigation links.
- [x] (2026-10-25) Cover freshness, aggregation, missing/capability states and drain math.
- [x] (2026-10-25 15:49Z) Update documentation/manifests, run full verification and visual QA.
- [ ] Commit/push, open/link PR, request at most one extra Codex review and watch.

## Surprises & Discoveries

- SDK QueuePressureCollector currently returns ready depth and only emits a
  consumer signal when consumers are zero. AMQP passive declarations expose
  ready and consumer counts, but not unacknowledged counts, oldest age or rates.
- Existing runtime publish counters can label routing keys, while completions
  label queues. They cannot safely be paired for queue drain estimates.
- Worker lease details already exist in the SDK/backend but frontend worker
  types omit them. Reading cached fleet health alone could hide individual stale
  workers behind a newer heartbeat; the cockpit will read fresh worker evidence.
- Long sweeps must finish before automatic refresh restarts, otherwise services
  near the end of large scopes can starve. Refresh preserves visible evidence
  within the same scope; failures replace it with explicit errors.
- NaN/Infinity metrics can occur for empty Prometheus histograms. Sanitize them
  to null, retain gaps and evaluate the newest sample per metric group.
- Current manifests were advanced to v1.10.1; preserve their version metadata.

## Decision Log

- Decision: user-authorized production perimeter exception; freeze boundary
  v1.4.30, latest released compatibility baseline v1.11.0. Use additive queue
  observations and a new Studio read endpoint/page. Preserve existing signal
  and collector APIs and all persisted data/wire formats. No migration needed.
  Date/Author: 2026-10-25 / Codex.
- Decision: RuntimePressureSnapshot gains optional queue observations; collectors
  may implement collect_snapshot, while existing collect-only providers continue
  working. Built-in AMQP collector reports only ready/consumers. Custom configured
  sources can report unacknowledged, oldest age and matching queue arrival/
  completion rates with an explicit observation window; absent fields are null.
  Date/Author: 2026-10-25 / Codex.
- Decision: Estimate drain only from fresh ready + unacknowledged backlog and
  matching queue-scoped rates in the same observation/window. Do not substitute
  service Prometheus publish/start metrics. Arrival >= completion means not
  draining; unknown fields prevent an estimate. Show scope/window/assumptions.
  Date/Author: 2026-10-25 / Codex.
- Decision: new per-service cockpit read reuses federation URL/auth/capability
  checks, worker health payloads and existing Prometheus metrics queries. Partial
  source failures return explicit source states. Fleet fan-out stays bounded.
  Date/Author: 2026-10-25 / Codex.
- Decision: follow-up authorization includes one PR against main and monitoring;
  coordination thread owns later stack assembly (main <- 01 <- 02 <- 03 <- 05). No sibling checkout edits.
  Date/Author: 2026-10-25 / Codex.

## Outcomes & Retrospective

Implementation complete; PR handoff in progress. The mandatory verification
script passes; SDK coverage passes; backend coverage is 98.22% with dedicated
PostgreSQL/Redis and 570 passing tests; frontend coverage passes with 287 tests,
98.07% statements and 89.53% branches. Frontend production build passes. Desktop
1440x1000 and mobile 390x844 layouts preserve missing-data states with no page
overflow. Captures and notes are under docs/qa/studio-queue-worker-cockpit/.

## Context and Orientation

`src/relayna/observability/backpressure.py` collects runtime pressure. RabbitMQ
passive queue inspection lives in `src/relayna/rabbitmq/client.py`. Worker health
and lease models live in `src/relayna/api/health_routes.py`. Studio federation
in `studio/backend/src/relayna_studio/federation.py` enforces registry scope,
capabilities and outbound URL policy. Metrics queries live in `metrics.py`.
`apps/studio/src/App.tsx`, `ui.tsx`, `services-context.tsx`, `api.ts` and `types.ts`
define Studio routes, navigation, registry scope, requests and contracts.
DLQ means dead-letter queue; a lease records one task attempt's owner, heartbeat
and expiry. Backend registration and existing readonly authorization apply.

## Compatibility Boundary

Latest release v1.11.0; production freeze boundary v1.4.30. The user's explicit
exception covers this feature. New queue observations, collector read support,
Studio cockpit route, frontend API/types/page and related manifest entries are
intentional additive perimeter changes. Existing providers, responses' existing
fields, public exports and persisted schemas remain compatible. Feature tests
cover old signal-only providers as well as new observations.

## Plan of Work

Extend backpressure snapshots with validated optional queue observations and
preserve collect-only providers. Normalize queue and worker observations in a
new internal Studio cockpit module, fetch optional existing runtime metrics, and
wire a readonly per-service endpoint. Add the lazy frontend route and navigation,
service/environment filtering, source-status panels, queue diagnoses, worker and
lease cards, trend evidence and existing service/task/DLQ links. Update feature
manifests and operational docs deliberately.

## Concrete Steps

From repository root run focused SDK/backend pytest and cockpit Vitest while
iterating. Finish with:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build

Start the frontend dev server on an unused port; use T3 collaborative preview
with representative fixtures for desktop/mobile visual and missing-data checks.

## Validation and Acceptance

Tests must show zero differs from missing, old signal-only payloads remain useful,
stale/future/invalid timestamps cannot yield live diagnoses, one failing source
cannot discard successful sources, disabled/unsupported services stay explicit,
individual worker heartbeat and lease expiry are evaluated separately, and queue
identity includes service. Estimates require complete fresh same-queue rates and
backlog; equal/excess arrivals report not draining. Retry/DLQ series retain nulls,
source warnings and window labels. Desktop/mobile must have usable controls,
wrapped identities, and no horizontal page overflow. Required checks pass before
PR creation. Handle actionable review findings before enabling T3 PR watch.

## Idempotence and Recovery

No data migration. Rerun checks after fixes. Preserve unrelated work. Shut down
only dev servers started here. Reverting this feature removes additive UI/read
surfaces without changing broker data. Do not rebase onto sibling worktrees.

## Artifacts and Notes

Original main merge-base: 89954699d2e43aebfee54e4ec64c06edb0caf2ce.
Reference patterns: https://www.rabbitmq.com/docs/management and
https://docs.confluent.io/control-center/current/clients/consumers.html .
Extra Codex review: not yet requested. Native T3 Codex catalog selection is
codex / gpt-6.1-sol with inherited reasoningEffort=xhigh and serviceTier=priority.
Use one bounded readonly review after opening the PR; do not also request @codex.

Verification commands additionally used a disposable PostgreSQL on localhost:56889
and Redis on localhost:56892, with schema upgraded via the backend Alembic target.
SDK tests retain 9 environment-dependent skips; backend integration tests all ran.
Coverage/build output is retained under /tmp/relayna-cockpit-*.log, not committed.

## Interfaces and Dependencies

No new dependency or credentials. Configured runtime collectors supply only
observed fields; built-in AMQP source has no rate/oldest-age capability. Existing
Prometheus configuration supplies service-scoped retry/DLQ, active-task and p95
handler metrics. `/studio/services/{service_id}/runtime/cockpit` returns separate
pressure/worker/metric source states and normalized evidence. No mutation route.
