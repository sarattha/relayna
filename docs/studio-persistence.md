# Studio PostgreSQL and Redis persistence

Since 1.6.0, Relayna Studio uses PostgreSQL and Redis together. PostgreSQL is the
authoritative system of record for the Studio control plane. Redis remains a
required low-latency transport and ephemeral-state service. This change is
limited to Studio: Relayna SDK service runtimes continue to use their existing
Redis stores and do not require PostgreSQL.

## Storage ownership

| Data | Authority | Notes |
| --- | --- | --- |
| Registered services and provider configuration | PostgreSQL | Active environment/base-URL pairs are unique. Deletes retain a tombstone so event history keeps referential integrity. |
| Studio members and RBAC | PostgreSQL | Last-active-admin enforcement is transactionally serialized. |
| Operator settings | PostgreSQL | Failed-task email settings are relationally anchored and audited. |
| Events, pull cursors, task/service search projections | PostgreSQL | Event dedupe, projection update, cursor state, and outbox enqueue use database transactions. |
| Current and historical service health | PostgreSQL | Current state and retained history are both indexed. |
| Notification pending state, delivery and dedupe history | PostgreSQL | Multi-replica execution uses a PostgreSQL advisory lock. |
| Operator audit log | PostgreSQL | A database trigger rejects updates and deletes. |
| Incident identity, owner/lifecycle, grouping corrections and history | PostgreSQL | Counts/evidence are live reads; history and audit writes commit with state changes. |
| Browser sessions and OIDC login transactions | Redis | Opaque-token hashes retain the existing fixed TTL behavior. |
| SSE/pub-sub delivery | Redis | PostgreSQL outbox rows publish the existing event payloads and channel names after commit. |
| Caches and short-lived coordination | Redis | No durable Studio state should rely solely on these keys. |
| SDK status, leases, workflows, dedupe and runtime stores | Redis | Unchanged; no SDK PostgreSQL dependency was added. |

## Database configuration and readiness

`RELAYNA_STUDIO_DATABASE_URL` is required by the production settings loader and
must use PostgreSQL. Both URL spellings are accepted; `postgresql://` is
normalized internally to `postgresql+asyncpg://`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_DATABASE_URL` | none | Required async PostgreSQL connection URL. |
| `RELAYNA_STUDIO_DATABASE_POOL_SIZE` | `10` | Per-replica steady connection pool. |
| `RELAYNA_STUDIO_DATABASE_POOL_MAX_OVERFLOW` | `20` | Per-replica temporary overflow connections. |
| `RELAYNA_STUDIO_OUTBOX_RELAY_INTERVAL_SECONDS` | `0.25` | Delay between outbox relay polls. |

Budget PostgreSQL connections across all replicas. For example, four replicas
with the defaults can open up to 120 connections under peak overflow.

Capability and health refreshes update task search metadata only when the
service name or environment changes. Those edits remain atomic with the service
record; an unchanged refresh does not rewrite or lock retained task rows.
PostgreSQL task-index retention uses the existing expiry index and commits
deletes in batches of at most 1,000 rows. It skips rows locked by concurrent
writers and retries them on a later retention pass, checking their current
expiry before deletion. Tasks without an expiry are retained.

Background worker coordination keeps a session advisory lock on a dedicated
pooled connection while work is running, without holding a database transaction
open during upstream requests. An error during lock acquisition or release
invalidates that connection so an uncertain session lock cannot return to the
pool. These changes require no schema migration or configuration changes.

Since 1.13.0, the backend automatically runs its bundled Alembic migrations to `head` during
application startup, before schema validation, authentication initialization, or
background workers. This applies to Docker, Compose, Kubernetes, and Python
launches. Fresh databases are initialized and already-current databases are a
no-op. PostgreSQL transaction advisory locking serializes concurrent replicas
and explicit Alembic commands. Failed upgrades roll back and abort startup;
unknown/newer revisions fail rather than downgrade. Fix the reported cause and
restart to retry.

The configured database role must own the Studio schema objects or have the
DDL privileges required by the bundled migrations. Back up before upgrades and
allow startup probes enough time for migration work and waiting replicas. Normal
deployment no longer needs a separate migration job; Compose waits for healthy
PostgreSQL and Redis, then the backend migrates itself. Explicit pre-deployment
upgrades remain supported, and legacy Redis backfill remains a separate
operator-controlled process.

`/readyz` checks PostgreSQL connectivity, the exact schema revision, and Redis;
`/livez` and `/healthz` only report process liveness. Deployment systems should
send traffic only after `/readyz` succeeds.

## Alembic operations

Run commands from `studio/backend/` with the database URL set:

```bash
export RELAYNA_STUDIO_DATABASE_URL='postgresql+asyncpg://user:password@db/relayna_studio'
make db-current
make db-upgrade
make db-downgrade
```

The backend image contains `alembic`, `alembic.ini`, and all revision files, so
the same operation can optionally be a one-shot deployment job:

```bash
docker run --rm \
  -e RELAYNA_STUDIO_DATABASE_URL="$RELAYNA_STUDIO_DATABASE_URL" \
  ghcr.io/sarattha/relayna-studio-backend:1.13.0 \
  alembic upgrade head
```

For each future schema change, edit the SQLAlchemy metadata, generate a new
revision against a disposable current database, review all generated DDL, and
exercise `upgrade head`, the supported downgrade, and `upgrade head` again on
real PostgreSQL. Never edit a revision after it has shipped.

The initial downgrade removes every Studio PostgreSQL table and is therefore
destructive. `make db-migration-cycle` is reserved for disposable databases.

## Release 1.12.0 schema upgrade and rollback

The coordinated 1.12.0 release requires the full chain
`0002_load_profiles -> 0003_incident_inbox -> 0003_recovery`.
The current backend expects exactly `0003_recovery`; 1.11.0 expects exactly
`0002_load_profiles`. Stop old backends in a maintenance window, back up both
stores and the settings encryption key, run `make -C studio/backend db-upgrade`,
confirm the head, then start only matching 1.12.0 backends/frontends. No mixed
schema-version rollout is supported. The released 1.12.0 image requires this
explicit migration; newer backends with startup migration support apply the
bundled chain automatically. Keep the maintenance window and backup procedure
when crossing schema versions.

Before downgrading, stop every backend/recovery worker, export Recovery reports
and Incident state/history, and reconcile accepted or ambiguous dispatch with
upstream services. Downgrade does not undo published retries. From
`studio/backend`, with `RELAYNA_STUDIO_DATABASE_URL` set:

```bash
# Recovery-only: drops recovery plans/claims, keeps Incident Inbox.
uv run alembic downgrade 0003_incident_inbox
# Full rollback to published 1.11.0: also drops Incident Inbox tables/history.
uv run alembic downgrade 0002_load_profiles
```

Choose the target appropriate to the rollback, rather than running both by
default. Recovery-only rollback requires a deliberately built intermediate
backend expecting `0003_incident_inbox`; neither released 1.11.0 nor 1.12.0 accepts
that head. For published 1.11.0 restore matching packages/images only after the
database reaches `0002_load_profiles`. Load profiles and earlier tables remain;
dropped Incident/Recovery data needs a backup to recover. Stop exported reliability
callbacks/rules when rolling back to a version without that endpoint. See the
[release procedure](releases.md#upgrading-to-1120) for the complete checklist.

## Incident Inbox state

Incident Inbox adds revision `0003_incident_inbox` after `0002_load_profiles`.
Backends with automatic startup migrations apply this revision as part of the
bundled chain; older releases require `alembic upgrade head` before deployment.
Startup/readiness requires the exact new head; coordinate backend deployment rather
than running mixed schema expectations indefinitely. Existing SDK stores and
Studio tables retain their formats and behavior.

`studio_incidents` stores stable grouping identities, normalized dimensions,
owner, status, revision, and merge redirects. `studio_incident_overrides` maps
explicitly split failure IDs within their original environment/service.
`studio_incident_history` records lifecycle and grouping changes, actor, note,
and before/after or source/target details. A database trigger rejects history
updates/deletes. Every operator change, its history, and its existing operator
audit entry share one transaction; a failure rolls them all back.

Discovery upserts never reset owner/status. A short PostgreSQL transaction
advisory lock serializes lifecycle/correction writes across replicas, and source
and target revision checks reject stale edits. Upstream network I/O occurs
before taking this lock. Counts, failure bodies, payloads, and traceback evidence
are not retained in these tables. Normalized error text is part of durable group
identity; include it in the same data-access and backup policy as operator notes.

Incident state/corrections/history currently have no automatic retention policy.
They survive service tombstoning and upstream evidence expiry. Back them up and
budget their growth alongside the unpruned operator audit log. Do not reuse
service/failure identities for different external records; split overrides refer
to their exact environment/service/failure identity.

An explicit downgrade from `0003_incident_inbox` to `0002_load_profiles` removes
only the three incident tables and history trigger/function, preserving older
Studio data. From the combined `0003_recovery` head, a one-revision downgrade
removes Recovery only and leaves Incident Inbox. Removing Incident permanently loses
incident ownership, lifecycle/history, and manual corrections. Export/backup
those tables before downgrading, and restore the matching backend/schema as a
coordinated operation. Upgrade/downgrade/upgrade was exercised on disposable
PostgreSQL with existing earlier tables; never use the destructive initial
downgrade as an operational shortcut.

## Transactional outbox and multi-replica workers

Event ingestion inserts the event, updates its search projection, and enqueues
service and task publication rows in one PostgreSQL transaction. The outbox
relay claims rows with `FOR UPDATE SKIP LOCKED`, commits only after Redis
publication succeeds, and retries failures with backoff. Multiple Studio
replicas may safely run relays concurrently.

Publication is at-least-once because a process can stop after Redis accepts a
message but before PostgreSQL records delivery. The stable event `dedupe_key`
is the consumer identity; REST history remains authoritative. Delivered outbox
rows are retained for seven days before the coordinated retention worker
removes them.

Pull sync, health refresh, retention, and failed-task notification scans use
PostgreSQL advisory locks. Only one replica performs each periodic pass, while
event uniqueness and row constraints remain the final concurrency guard.

Email delivery is not exactly-once unless the configured provider supports an
idempotency key. A process loss after the provider accepts a message but before
the delivery row commits can cause a retry and duplicate email. The durable
pending/delivery history prevents ordinary duplicate scans but cannot close
that external side-effect crash window.

## Redis backfill and maintenance-window cutover

Mixed old and new Studio versions are unsupported: old replicas write durable
state to Redis while 1.6.0 writes it to PostgreSQL. Use a short maintenance
window.

1. Stop every old Studio backend replica and confirm no Studio writer remains.
2. Save a Redis snapshot and a PostgreSQL backup. Record image digests and the
   exact Redis database/prefixes.
3. Run `alembic upgrade head` against the new PostgreSQL database.
4. Validate the frozen Redis source without writing PostgreSQL:

   ```bash
   relayna-studio-migrate-redis \
     --redis-url "$RELAYNA_STUDIO_REDIS_URL" \
     --database-url "$RELAYNA_STUDIO_DATABASE_URL" \
     --validate-only
   ```

5. Resolve every malformed record. `--allow-invalid` is an explicit emergency
   override and reports every skipped key; do not use it silently.
6. Run the same command without `--validate-only`. The importer reads only the
   configured `studio:*` control-plane families. It never scans or migrates SDK
   runtime keys.
7. Record the returned source fingerprint, checksum, counts, invalid-key list,
   and tombstone-service count. Run the importer again and require
   `already_imported` with the same checksum.
8. Start only 1.6.0 replicas and require `/readyz` before enabling traffic.
   Compare service/member/event/task/health/notification counts and inspect
   representative records and the migration audit entry.

The importer is transactional and idempotent. It preserves event and task
history for deleted services by creating non-visible service tombstones. If
the Redis source changes after a completed import, a rerun fails rather than
silently combining snapshots. Backfilled event rows preserve each Redis event
key's remaining expiry; keys configured without expiry remain unbounded.

## Backup, restore, and rollback

Back up both systems. PostgreSQL contains durable Studio data; Redis contains
active browser sessions, login transactions, live-delivery state, and caches.
A typical PostgreSQL backup is:

```bash
pg_dump --format=custom --no-owner --file=relayna-studio.dump "$RELAYNA_STUDIO_DATABASE_URL"
pg_restore --clean --if-exists --no-owner --dbname="$RESTORE_DATABASE_URL" relayna-studio.dump
```

Test restores regularly and verify the Alembic revision, table counts,
append-only audit trigger, recent events, member/admin invariant, and `/readyz`.
Follow the Redis service's snapshot/AOF backup procedure separately.

Before any 1.6.0 write, rollback is: stop all new replicas, restore the
pre-cutover Redis snapshot, and start only the old image. After 1.6.0 accepts
writes, PostgreSQL contains state the old release cannot read. A rollback then
has a declared data-loss boundary unless an operator builds and validates a
reverse export for the exact incident. Do not run Alembic downgrade as a
rollback shortcut; the initial downgrade deletes the Studio database schema.

## Retention and audit

Event and task projection expiry follow the existing event/task TTL settings.
The coordinated retention pass also removes expired notification state,
30-day health history, and delivered outbox rows older than seven days. Current
health and service configuration remain until superseded or deleted.

The audit table records material registry, RBAC, settings, notification, and
failed-task operations. Synchronous upstream failed-task actions write a
`requested` record before the call and a `succeeded` or `failed` record after
it. Audit records are append-only and are not automatically pruned; size and
archive them according to the operator's compliance policy.

## Recovery ledger

Revision `0003_recovery` follows `0003_incident_inbox` and adds `studio_recovery_plans` and
`studio_recovery_claims`. They retain exact cohorts, dispatch intent, acceptance,
and observed outcomes, and reserve failure snapshots against overlapping plans.
Creation, controls, dispatch, and state transitions also write the operator audit
log. See [Recovery Center](studio-recovery-center.md) for crash/timeout behavior,
backup and downgrade guidance. Downgrading drops recovery history and reservations;
stop all recovery workers and export reports first.

## Reliability goals and delivery

Reliability goals use versioned `reliability:v1:<goal-id>` rows in the existing
`studio_operator_settings` table. Each JSON document contains immutable goal
configuration and bounded delivery diagnostics (the last 100 alert episodes plus
counters). PostgreSQL row locks serialize duplicate callbacks across replicas.
No migration is required. Configuration and receipts persist until goal deletion;
mutation and delivery audits use the existing audit log. These records are not a
business-work ledger or a substitute for external alert history. See
[Reliability goals](studio-reliability.md) for the at-least-once delivery boundary.

## Doctor evidence retention

Feature 07 reuses the existing event, health and outbox read models. It adds no
SQL migration, changes no existing event envelope and does not extend history.
Service event samples are limited to 20 records; outbox samples use the existing
pending availability index with a 101-row limit. Outbox sample age is not an
exact total or a global oldest-age guarantee.

Optional refresh and email-provider-acceptance observations are stored in Redis
under `<event_store_prefix>:doctor:pull:<service_id>:{attempt,outcome,success}`
and `<failed_task_email_prefix>:doctor:delivery:{attempt,outcome,success}`.
Each key expires after seven days. Writes have a 250 ms budget and are best
effort: evidence failures do not fail ingestion or delivery. Failed attempts
preserve the last success timestamp; absent/expired evidence remains unknown.
These observations are auxiliary operational evidence, not durable audit or a
complete notification-attempt history. No payloads, recipients, API keys or
provider URLs are recorded there. Existing PostgreSQL notification delivery and
audit persistence remain unchanged.
