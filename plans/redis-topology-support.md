# Redis Topology Support

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with `PLANS.md` at the repository
root.

## Purpose / Big Picture

Relayna applications can use either a Redis primary endpoint backed by one or
more replicas or a genuine Redis Cluster that shards keys across hash slots.
Operators select the topology explicitly. Relayna creates the matching async
redis-py client, uses RESP3, preserves status history and live SSE delivery,
and keeps service-event and DLQ operations valid in cluster mode.

The result is observable through focused unit tests, more than 95 percent SDK
coverage (the repository gate is 98 percent), and real Docker Desktop
acceptance against standalone Redis, one primary with two replicas, a
three-primary cluster, and a three-primary/three-replica cluster.

## Progress

- [x] (2026-08-22 00:00Z) Applied `$implementation-strategy` and
  `$production-freeze-guard`; the user explicitly approved breaking the
  v1.4.30 public and persisted-Redis production perimeter and confirmed old
  Redis data need not be preserved.
- [x] (2026-08-22 00:00Z) Confirmed a clean `main` worktree at latest release
  tag `v1.6.0` and created `codex/redis-topology-support`.
- [x] (2026-08-22 16:15Z) Finalized the Redis client abstraction, explicit
  topology configuration, key-slot layout, and redis-py 8.1 behavior.
- [x] (2026-08-22 16:15Z) Implemented dependency, RESP3 runtime, storage,
  documentation, and approved freeze-manifest changes.
- [x] (2026-08-22 16:15Z) Added unit, regression, and opt-in real-environment
  tests plus a reproducible Docker Compose runner.
- [x] (2026-08-22 16:15Z) Passed focused tests, all four real Docker topology
  environments, the 98 percent coverage gate, and the mandatory SDK and Studio
  verification stack.
- [x] (2026-08-22 16:29Z) Committed and pushed the implementation, opened
  ready PR #124, received one actionable P1 from the first Codex review, fixed
  retry suppression after publication failures, and reran focused, Docker,
  coverage, and mandatory verification.
- [x] (2026-08-22 16:32Z) Replied to the P1 inline with verification evidence,
  resolved its thread, and confirmed all ten replacement CI jobs pass.
- [x] (2026-08-22 17:05Z) Chose a synchronized 1.7.0 feature release,
  refreshed SDK and Studio version metadata and lockfiles, intentionally
  advanced all three freeze manifests, and expanded the Redis topology and
  release documentation with selection and rollout conditions.
- [x] (2026-08-22 17:24Z) Passed release metadata validation, strict MkDocs,
  synchronized freeze tests, SDK/Studio/frontend builds, the mandatory Python
  verification stack, and coverage at 98%, 98.04%, and 98.09% statements.
- [ ] Push the 1.7.0 release update and confirm replacement CI.

## Surprises & Discoveries

- Observation: The repository coverage gate is stricter than the requested
  threshold.
  Evidence: `pyproject.toml` sets `fail_under = 98` and `make coverage` enforces
  it.

- Observation: The currently locked redis-py 7.2.1 async `RedisCluster` has no
  `pubsub()` method, while stable redis-py 8.0.1 includes async cluster Pub/Sub
  and a fix for blocking `listen()` behavior.
  Evidence: local runtime introspection and redis-py 8.0.1 release notes.

- Observation: `RedisServiceEventFeedStore` executes Lua scripts across
  multiple untagged keys and `RedisDLQStore` performs multi-record `MGET`, so a
  client-class swap alone cannot support Redis Cluster.
  Evidence: `src/relayna/observability/feed.py` and
  `src/relayna/dlq/store.py`.

- Observation: The current dependency resolver selects redis-py 8.1.0, whose
  async `ClusterPipeline` intentionally rejects `PUBLISH` even though the
  cluster client itself supports Pub/Sub.
  Evidence: local redis-py introspection and the real three-primary Redis Stack
  acceptance run. Status publication now follows the completed key pipeline.

- Observation: Redis Cluster endpoint advertisement is part of application
  reachability. A healthy Docker cluster initially advertised container-only
  addresses; preferring advertised hostnames made the slot map reachable by
  the host client, matching the DNS requirement operators face in AKS.
  Evidence: failed connection to a Docker-internal address followed by passing
  three-node and six-node cluster runs using reachable advertised hostnames.

- Observation: redis-py 8.1 defaults standalone pools to 100 connections. The
  existing 100-way, 5,000-event integration burst could consume the entire
  test pool before cleanup acquired a connection.
  Evidence: `MaxConnectionsError` in the stress-test cleanup. The integration
  clients use a test-only 256-connection pool; production defaults are not
  changed by this work.

- Observation: The first Codex review found that moving `PUBLISH` after the
  pipeline exposed a retry-suppression gap: the existing history dedupe marker
  caused a RabbitMQ redelivery to return before live publication or feed work.
  Evidence: PR #124 review thread `discussion_r3836465894`. Status events now
  remain `pending` until history, child indexing, feed ingestion, and Pub/Sub
  complete, while history itself is written only once.

## Decision Log

- Decision: Compatibility boundary is the frozen v1.4.30 perimeter and latest
  release tag v1.6.0; direct replacement is allowed for public configuration
  and persisted Redis key names.
  Rationale: The user explicitly approved breaking production freeze and
  stated all previous Redis data are gone, so no dual-read or migration shim is
  required.
  Date/Author: 2026-08-22 / Codex.

- Decision: Use explicit `standalone` and `cluster` modes rather than URL
  inference. A primary plus replicas is supported in `standalone` mode when
  infrastructure exposes a primary-aware endpoint.
  Rationale: Both modes use `redis://` or `rediss://`; a URL cannot reliably
  identify topology. Sentinel discovery remains outside this request unless
  current repository context shows it is required.
  Date/Author: 2026-08-22 / Codex.

- Decision: Move to redis-py 8 with RESP3 instead of retaining RESP2
  compatibility.
  Rationale: The user explicitly requested redis-py 8 and RESP3 and waived old
  data compatibility. The minimum version must include async cluster Pub/Sub
  and the blocking-listen fix.
  Date/Author: 2026-08-22 / Codex.

- Decision: Validate four representative Docker topologies: standalone,
  primary plus two replicas, three primary shards, and three primary shards
  with one replica each.
  Rationale: These cover the two supported client modes, replication reads and
  writes through a primary endpoint, minimum cluster sharding, and production
  cluster replication/failover layout without claiming every possible node
  count is a distinct behavior.
  Date/Author: 2026-08-22 / Codex.

- Decision: Prefix generated Redis hash tags before the configured human
  prefix and derive them from both the prefix and an operation-specific slot
  identity.
  Rationale: A user prefix containing braces cannot capture the cluster slot,
  and unrelated per-task keys can distribute while every Lua or bulk-key group
  remains co-located.
  Date/Author: 2026-08-22 / Codex.

- Decision: Publish status messages after the storage pipeline completes.
  Rationale: redis-py cluster pipelines block `PUBLISH`; performing the client
  command immediately after persistence preserves the required storage-before-
  fanout ordering and works for both client modes.
  Date/Author: 2026-08-22 / Codex.

- Decision: Represent status-event processing with `pending` and `complete`
  dedupe marker values rather than treating marker existence as completion.
  Rationale: RabbitMQ redeliveries must retry idempotent post-persistence work
  after a transient failure without duplicating status history. Feed ingestion
  already deduplicates atomically, and a completed marker suppresses ordinary
  duplicate delivery.
  Date/Author: 2026-08-22 / Codex.

- Decision: Release the Redis topology work as synchronized version 1.7.0
  across the SDK, Studio backend, and Studio frontend.
  Rationale: Explicit topology configuration, redis-py 8/RESP3, and the SDK
  key-layout replacement are feature and persisted-data changes relative to
  v1.6.0. A minor version communicates that impact better than a patch, while
  the repository release validator requires all three package versions to
  remain aligned.
  Date/Author: 2026-08-22 / Codex.

## Outcomes & Retrospective

Implementation and local validation are complete. Relayna now selects either
async `Redis` or `RedisCluster`, explicitly negotiates RESP3, uses cluster-safe
SDK key families, and closes modern async Pub/Sub objects with `aclose()`.

The normal SDK suite passed with 684 tests and 8 environmental skips. Coverage
passed at 98 percent. The mandatory verification script passed SDK and Studio
format, lint, type checking, and tests; Studio completed with 260 passing and
14 PostgreSQL-environment skips.

Docker Desktop acceptance passed twice per environment test module for each of
standalone Redis Stack, one primary plus two replicas, three cluster primaries,
and three cluster primaries plus three replicas. The replication layout also
used `WAIT` and direct replica reads to prove both replicas received status
history. The exact Compose project was removed afterward and Docker's API
reported no remaining containers.

PR #124 is open and ready for human review. Its first Codex review raised one
P1 about retry suppression; commit `be8e088` fixes it and has passed 686 SDK
tests, the 98 percent coverage gate, 260 Studio tests, all four real Redis Stack
topologies, and all ten replacement CI jobs. The inline thread has a concrete
verification reply and is resolved. The intentional residual limitation is
that Relayna does not discover Sentinel or promote primaries; a replicated
non-sharded deployment must expose a primary-aware endpoint.

The follow-up release pass advances all synchronized package and freeze
versions to 1.7.0, refreshes both Python lockfiles and the frontend lockfile,
and publishes detailed standalone-versus-cluster operator conditions. Release
metadata validates as v1.7.0; both Python wheels and the SDK source distribution
build with 1.7.0 metadata; strict documentation and the frontend production
bundle pass. Coverage remains above the requested threshold in every codebase:
SDK 98%, Studio backend 98.04% with real PostgreSQL/Redis integration, and
Studio frontend 98.09% statements.

## Context and Orientation

The SDK runtime factory is `src/relayna/api/fastapi_lifespan.py`; it currently
always calls `redis.asyncio.Redis.from_url`. `RedisStatusStore` and
`SSEStatusStream` under `src/relayna/status/` persist task history and combine
history replay with live Redis Pub/Sub. `RedisServiceEventFeedStore` under
`src/relayna/observability/feed.py` maintains a global ordered event feed using
Lua scripts. `RedisDLQStore` under `src/relayna/dlq/store.py` persists and lists
dead-letter records.

SDK tests live under `tests/`. `tests/test_fastapi_lifespan.py`,
`tests/test_sse.py`, `tests/test_status_store.py`,
`tests/test_service_event_feed.py`, `tests/test_service_event_feed_redis.py`,
and `tests/test_dlq.py` are the primary regression surfaces. Dependency and
coverage configuration live in `pyproject.toml`, `uv.lock`, and `Makefile`.

## Compatibility Boundary

Compatibility boundary: strict production freeze v1.4.30 and latest release
tag v1.6.0. This feature intentionally adds public Redis topology
configuration, raises the redis-py dependency to version 8, uses RESP3, and
changes persisted Redis key names where hash tags are required. The user
explicitly approved breaking the production perimeter and confirmed no
existing Redis data must be migrated. Freeze manifests will be updated only
for the exact approved public-surface changes, and the PR will contain this
compatibility note.

Standalone mode remains the default so existing source calls that provide only
`redis_url` continue to work against a primary endpoint. Existing persisted
keys are not preserved because the user waived that boundary.

## Plan of Work

Introduce a small internal Redis client protocol and explicit public topology
mode in `src/relayna/api/fastapi_lifespan.py`. Create `Redis` with RESP3 for
standalone/primary-endpoint deployments and `RedisCluster` with RESP3 for
cluster deployments. Ensure startup initializes cluster discovery, runtime
typing accepts both clients, and shutdown closes them consistently.

Update status, observation, service-feed, and DLQ Redis keys and commands for
cluster validity. Related keys used by one Lua script or atomic multi-key
operation will share a deliberate hash tag. Cross-slot bulk reads will use a
client-neutral helper or cluster non-atomic operation while preserving output
order. Keep standalone behavior, API responses, retention, deduplication, and
SSE semantics covered by regression tests.

Raise the redis-py dependency to a stable 8.x floor containing async Cluster
Pub/Sub fixes, refresh `uv.lock`, and document supported topologies, Redis
server requirements, RESP3, database-zero requirements, and the intentional
key-layout break.

Add deterministic unit tests for client selection, topology validation,
cluster Pub/Sub behavior boundaries, key-slot co-location, DLQ multi-key
reads, resource cleanup, and all existing behavior. Add opt-in real Redis tests
and Docker assets/scripts where they improve repeatability without making the
normal unit suite depend on Docker.

Use Computer Use to inspect Docker Desktop state, then use Docker CLI for
repeatable service creation and test execution. Validate standalone,
primary/replica, and both minimum and replicated cluster layouts. Tear down
only the named test resources created for this plan.

## Concrete Steps

Run from `/Users/jobz/Works/relayna`:

    uv lock --upgrade-package redis
    uv run pytest <focused test paths>
    make coverage
    bash .codex/skills/code-change-verification/scripts/run.sh

Use isolated Docker Compose project names and explicit local ports for each
topology. Inspect exact containers before cleanup and remove only those
project-scoped resources.

After validation, inspect the complete diff and worktree, stage only confirmed
plan-scoped paths, commit, push `codex/redis-topology-support`, and open a draft
PR against `main`. Monitor checks and the first Codex review, implement valid
feedback, reply to each addressed thread, resolve approved threads, and rerun
affected verification.

## Validation and Acceptance

The change is accepted when:

- Existing standalone tests remain green using RESP3.
- A primary with two replicas accepts Relayna writes through the primary and
  all replicas converge on status history.
- A three-primary Redis Cluster and a three-primary/three-replica Redis Cluster
  both pass status history, latest status, child lookup, SSE history replay and
  live delivery, observation history, service-event feed, DLQ add/list/update,
  TTL, deduplication, and shutdown tests.
- All Lua key groups map to one hash slot and no exercised operation reports
  `MOVED` or `CROSSSLOT`.
- Failure and cleanup paths close standalone and cluster clients.
- `make coverage` passes the repository's 98 percent floor, exceeding the
  requested 95 percent.
- `$code-change-verification` completes successfully.
- Documentation and freeze artifacts accurately describe the approved break.
- A draft PR is open, required CI is green or explained, and the first Codex
  review has no unresolved actionable comments.

## Idempotence and Recovery

Unit and verification commands are rerunnable. Real-environment tests use
dedicated names, ports, prefixes, and Docker Compose project names. Before any
cleanup, enumerate exact containers, networks, and volumes and ensure every
target belongs to this plan; do not traverse links or remove unrelated Docker
resources. If setup fails, preserve logs, stop the named test stack, and rerun
from its declarative configuration.

Because prior Redis data are explicitly out of scope, rollback means deploying
the previous Relayna version with a fresh compatible Redis namespace rather
than attempting reverse migration of new cluster-tagged keys.

## Artifacts and Notes

This section will contain concise focused-test, coverage, Docker, CI, and Codex
review transcripts as work proceeds.

## Interfaces and Dependencies

The finished public runtime factory will retain `redis_url` and add an explicit
topology selector whose default preserves source compatibility. The internal
client contract will cover commands used by Relayna stores, pipelines,
registered scripts, Pub/Sub, initialization when required, and `aclose()`.

The SDK dependency will require stable redis-py 8.x. Both clients will use
RESP3. Genuine Redis Cluster will require database zero, cluster-advertised
node addresses reachable from Relayna, and complete slot coverage. A
primary/replica topology will require a primary-aware `redis_url`; Relayna will
not load-balance writes across replicas.
