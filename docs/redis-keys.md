# Redis Key Reference

This reference lists the Redis key families Relayna uses at runtime. It is
intended for operators who need to choose safe prefixes, inspect data during an
incident, or understand which Redis state is required by the SDK and Studio.

Key names are shown with placeholders such as `{prefix}`, `{task_id}`, and
`{service_id}`. Prefixes should be isolated per environment when multiple
Relayna stacks share one Redis instance.

## SDK Runtime Keys

The SDK uses Redis for task status, status streaming, optional DLQ indexing,
optional observation history, optional service event feeds, and workflow helper
state.

### Status Store

`RedisStatusStore` stores normalized task status events and powers latest
status, history, child task discovery, and server-sent event fanout.

Default prefixes:

- `create_relayna_lifespan(...)`: `relayna`
- direct `RedisStatusStore(...)` construction: `task`

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:history:{task_id}` | list | Bounded status history for one task. The newest event is pushed first. |
| `{prefix}:channel:{task_id}` | pubsub channel | Realtime status fanout for one task. |
| `{prefix}:event:{task_id}:{token}` | string | Dedupe marker for one status event. `{token}` is the event id when present, otherwise a hash of the event payload. |
| `{prefix}:children:{parent_task_id}` | set | Child task ids discovered from status event metadata. |

Retention is controlled by `store_ttl_seconds`; bounded history length is
controlled by `store_history_maxlen`.

### Observation Store

`RedisObservationStore` stores per-task observability events used by execution
graphs and task timelines.

Default prefix: `relayna-observations`.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:history:{task_id}` | list | Bounded observation history for one task. |
| `{prefix}:event:{task_id}:{token}` | string | Dedupe marker for one observation event. `{token}` is a hash of the normalized event payload. |

Retention is controlled by `observation_store_ttl_seconds`; bounded history
length is controlled by `observation_history_maxlen`.

### Service Event Feed

`RedisServiceEventFeedStore` stores a merged feed of status and observation
events that a Studio backend can pull from a registered Relayna service.

Default prefix: `relayna-service-events`.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:feed` | list | Bounded feed of normalized service events. |
| `{prefix}:event:{cursor}` | string | Dedupe marker for one feed event cursor. |

Retention is controlled by `service_event_store_ttl_seconds`; bounded feed
length is controlled by `service_event_feed_maxlen`.

### DLQ Store

`RedisDLQStore` stores optional DLQ records used by SDK DLQ listing, detail, and
replay helpers.

Default prefix: `relayna` when the store is constructed directly. The FastAPI
lifespan only creates the DLQ store when `dlq_store_prefix` is provided.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:dlq:record:{dlq_id}` | string | Serialized DLQ record. |
| `{prefix}:dlq:records` | list | Ordered DLQ id index used for listing and queue summaries. |
| `{prefix}:dlq:replay-lock:{dlq_id}` | string | Short-lived replay claim lock. |

Record and list retention is controlled by `dlq_store_ttl_seconds`. Replay lock
claims use a short fixed TTL.

### Workflow Helper State

Workflow helper keys use the configured workflow storage prefix. Current helper
functions build these names directly, and `RedisWorkflowContractStore` defaults
to `relayna`.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:workflow:run:{task_id}` | implementation-owned value | Workflow run state for a task when a runtime persists run progress. |
| `{prefix}:workflow:fanin:{task_id}:{stage}` | implementation-owned value | Fan-in progress for a task and destination stage. |
| `{prefix}:workflow:contract:{stage}:dedup:{task_id}:{signature}` | string | Deduplication marker for a workflow stage action. |
| `{prefix}:workflow:contract:{stage}:inflight:{task_id}` | hash | Inflight dedup signatures for a workflow stage and task. |

`RedisWorkflowContractStore` retention is controlled by its `ttl_seconds`
argument.

### Task Lease Store

`RedisTaskLeaseStore` stores optional in-flight task and workflow lease
ownership. Leases are used by `TaskConsumer` and `WorkflowConsumer` when a
`LeasePolicy(enabled=True)` and lease store are supplied.

Default prefix: `relayna`.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:lease:task:{lease_id}` | string | Serialized `TaskLease` payload with a Redis TTL matching the lease expiry. |
| `{prefix}:lease:owner:{owner_id}` | set | Lease ids currently owned by one worker or consumer owner. |
| `{prefix}:lease:expiries` | sorted set | Lease ids scored by expiry timestamp for expiry scans. |
| `{prefix}:lease:expired_claims` | set | Claim markers used to prevent duplicate recovery processing for the same expired lease. |

`TaskLeaseExpiryScanner` claims expired leases from the sorted set and can call
a recovery publisher for leases whose `recovery_action` asks for a stale
status, requeue, retry, or dead-letter action. If the publisher fails
transiently, the Redis claim marker is released and the expiry index is restored
so the next scan can retry recovery.

## Studio Backend Keys

The Studio backend requires Redis. It owns control-plane state for service
registry records, ingested events, health snapshots, and search indexes.

### Registry

Default prefix: `studio:services`.

Configuration: `RELAYNA_STUDIO_REGISTRY_PREFIX`.

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:by-id:{service_id}` | string | Serialized service registry record. |
| `{prefix}:all` | set | All registered service ids. |
| `{prefix}:by-env-url:{environment}:{normalized_base_url}` | string | Uniqueness index from environment and normalized base URL to service id. |

### Event Store

Default prefix: `studio:events`.

Configuration:

- `RELAYNA_STUDIO_EVENT_STORE_PREFIX`
- `RELAYNA_STUDIO_EVENT_STORE_TTL_SECONDS`
- `RELAYNA_STUDIO_EVENT_HISTORY_MAXLEN`
- `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS`

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:event:{dedupe_key}` | string | Serialized Studio control-plane event. |
| `{prefix}:service:{service_id}:history` | list | Bounded event dedupe-key history for one service. |
| `{prefix}:task:{service_id}:{task_id}:history` | list | Bounded event dedupe-key history for one service task. |
| `{prefix}:task:{service_id}:{task_id}:latest-timestamp` | string | Latest event timestamp seen for one service task. |
| `{prefix}:service:{service_id}:latest-status-timestamp` | string | Latest status event timestamp seen for one service. |
| `{prefix}:service:{service_id}:latest-observation-timestamp` | string | Latest observation event timestamp seen for one service. |
| `{prefix}:service:{service_id}:latest-ingested-timestamp` | string | Latest Studio ingest timestamp for one service. |
| `{prefix}:pull-cursor:{service_id}` | string | Last pulled service event cursor for background pull sync. |
| `{prefix}:channel:service:{service_id}` | pubsub channel | Realtime Studio event fanout for one service. |
| `{prefix}:channel:task:{service_id}:{task_id}` | pubsub channel | Realtime Studio event fanout for one service task. |

### Health Store

Default prefix: `studio:health`.

Configuration:

- `RELAYNA_STUDIO_HEALTH_STORE_PREFIX`
- `RELAYNA_STUDIO_HEALTH_REFRESH_INTERVAL_SECONDS`
- `RELAYNA_STUDIO_CAPABILITY_STALE_AFTER_SECONDS`
- `RELAYNA_STUDIO_OBSERVATION_STALE_AFTER_SECONDS`
- `RELAYNA_STUDIO_WORKER_HEARTBEAT_STALE_AFTER_SECONDS`

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:{service_id}` | string | Serialized health document for one registered service. |

### Search Index

Default prefix: `studio:search`.

Configuration:

- `RELAYNA_STUDIO_TASK_SEARCH_INDEX_PREFIX`
- `RELAYNA_STUDIO_TASK_INDEX_TTL_SECONDS`
- `RELAYNA_STUDIO_RETENTION_PRUNE_INTERVAL_SECONDS`

| Key | Type | Purpose |
| --- | --- | --- |
| `{prefix}:task:doc:{document_id}` | string | Serialized task search document. |
| `{prefix}:task:all` | set | All task search document ids. |
| `{prefix}:task:filter:{field}:{value}` | set | Task document ids matching a filter such as service id, task id, status, stage, or correlation id. |
| `{prefix}:task:service:{service_id}` | set | Task document ids for one service. |
| `{prefix}:service:doc:{service_id}` | string | Serialized service search document. |
| `{prefix}:service:all` | set | All service search document ids. |
| `{prefix}:service:filter:{field}:{value}` | set | Service ids matching a filter such as environment, status, health, or tag. |
| `{prefix}:service:token:{token}` | set | Service ids matching a search token. |

## Studio Frontend

The Studio frontend does not connect to Redis and should not be configured with
Redis credentials. It reads Redis-backed state only through Studio backend
`/studio/*` APIs. Redis network access, prefix selection, TTLs, and retention
policy are backend and SDK runtime concerns.

## Prefix And Retention Guidance

- Use dedicated Redis databases or deployment-specific prefixes for local,
  staging, and production environments.
- Keep SDK prefixes aligned between producers, workers, FastAPI status routes,
  observation stores, DLQ stores, and any Studio pull integration that reads the
  service event feed.
- Treat prefix changes as new state locations. Existing data remains under the
  old prefix unless migrated or allowed to expire.
- Use TTLs intentionally. Disabling TTLs is useful for forensic retention but
  requires external cleanup for high-volume event and status streams.
