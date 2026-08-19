# Studio PostgreSQL and Redis persistence

Relayna Studio 1.6.0 uses PostgreSQL and Redis together. PostgreSQL is the
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

The application does not run migrations. Startup fails if PostgreSQL is
unreachable or its Alembic revision is not exactly the application head.
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
the same operation can be a one-shot deployment job:

```bash
docker run --rm \
  -e RELAYNA_STUDIO_DATABASE_URL="$RELAYNA_STUDIO_DATABASE_URL" \
  ghcr.io/sarattha/relayna-studio-backend:1.6.0 \
  alembic upgrade head
```

For each future schema change, edit the SQLAlchemy metadata, generate a new
revision against a disposable current database, review all generated DDL, and
exercise `upgrade head`, the supported downgrade, and `upgrade head` again on
real PostgreSQL. Never edit a revision after it has shipped.

The initial downgrade removes every Studio PostgreSQL table and is therefore
destructive. `make db-migration-cycle` is reserved for disposable databases.

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
