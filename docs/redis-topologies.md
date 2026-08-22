# Redis Topologies

Relayna 1.7.0 supports two explicit Redis client modes. Both use redis-py 8
and RESP3, but they describe different server-side topologies. Choose the mode
from Redis configuration and endpoint behavior, not from the number of pods or
whether the image is called Redis Stack.

## Quick decision

| Deployment condition | `redis_mode` | Endpoint Relayna needs |
| --- | --- | --- |
| One writable Redis or Redis Stack server | `standalone` | That server. |
| One primary with one or more replicas | `standalone` | A primary-only service or proxy that follows promotion. |
| Sentinel-managed primary and replicas | `standalone` | A separate Sentinel-aware service or proxy that exposes the current primary. |
| Genuine sharded Redis Cluster | `cluster` | A reachable seed node; all advertised cluster nodes must also be reachable. |
| Several independent writable Redis pods | Unsupported as one Relayna store | Give each isolated deployment its own namespace, or build a genuine cluster. |

Use `standalone` unless all cluster conditions below are true. Pod count alone
does not identify a Redis Cluster: an AKS StatefulSet with three pods is often
one primary and two replicas, while three independent Redis Stack pods may have
no replication or sharding relationship at all.

## When to use `standalone`

Use `redis_mode="standalone"` when Redis presents one writable primary to
Relayna. This includes a single server and a replicated high-availability
deployment. Replication changes availability, but it does not make the
deployment a Redis Cluster.

All of these conditions must hold:

- The configured endpoint sends every Relayna command to the current writable
  primary. A Kubernetes Service must not randomly balance traffic across the
  primary and read-only replicas.
- After failover, the endpoint or an external proxy/operator updates to the new
  primary. Relayna does not discover Sentinel or perform promotion itself.
- Every Relayna process sharing data uses the same Redis endpoint, database,
  credentials, TLS policy, key prefixes, and SDK version.
- The server is Redis 7.2 or later, as required by redis-py 8, and accepts
  RESP3. Redis Stack Server 7.4.0-v8 is used by the repository acceptance suite.
- The selected logical database exists. Standalone mode may use database zero
  or another configured Redis database.

Example:

```python
app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://redis-primary.default.svc.cluster.local:6379/0",
        redis_mode="standalone",
    )
)
```

For TLS, use `rediss://` and provide the certificates and hostname behavior
required by the deployment. Authentication may be encoded in the URL. Keep
secrets outside source control.

### Primary plus replicas on AKS

For one primary and two replicas, the safe service shape is:

```text
Relayna pods -> redis-primary Service/proxy -> current writable primary
                                      replicas <- Redis replication
```

The service selector must select only the current primary, or a Redis-aware
proxy/operator must route writes to it and update promptly after promotion. A
headless service that returns all three pod addresses is not a primary-aware
endpoint. A normal Service that selects all three pods is also unsafe: Relayna
can receive `READONLY` errors or read different data depending on which pod
answers.

Inspect the endpoint from the same AKS network path used by Relayna. If the
application ACL cannot run `CONFIG GET`, verify `cluster-enabled no` from the
deployment configuration or with an operator credential:

```bash
redis-cli -u "$REDIS_URL" INFO replication
redis-cli -u "$REDIS_URL" CONFIG GET cluster-enabled
```

For the Relayna endpoint, `INFO replication` must report `role:master` (Redis
also uses the term primary in documentation), and `cluster-enabled` must be
`no`. Repeat the check during a controlled failover: the same application
endpoint must eventually report the newly promoted writable primary.

Sentinel can manage this topology, but Relayna does not connect to Sentinel
addresses. Put a Sentinel-aware proxy, operator-managed primary Service, or
equivalent stable primary endpoint in front of Relayna and continue to select
`standalone`.

## When to use `cluster`

Use `redis_mode="cluster"` only for a genuine Redis Cluster: Redis Cluster mode
is enabled, keys are partitioned over hash slots, and clients discover and
connect to multiple nodes.

All of these conditions must hold:

- `CONFIG GET cluster-enabled` returns `yes` on the seed nodes.
- `CLUSTER INFO` reports `cluster_state:ok` and
  `cluster_slots_assigned:16384`.
- Every address and port returned by `CLUSTER SHARDS` or `CLUSTER NODES` is
  resolvable and reachable from every Relayna pod. This includes the addresses
  Redis advertises after initial seed discovery, not only the seed Service.
- Network policies, firewalls, TLS, and authentication allow Relayna to connect
  to every data endpoint. The Redis cluster bus must also be healthy between
  Redis nodes.
- Database zero is used. Redis Cluster does not support multiple logical
  databases, so do not select `/1` or higher in the URL.
- All Relayna processes sharing the cluster use `redis_mode="cluster"`, the
  same credentials/TLS policy, compatible key prefixes, and the same SDK
  version.

Example:

```python
app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url=(
            "redis://redis-cluster-0.redis-cluster.default."
            "svc.cluster.local:6379/0"
        ),
        redis_mode="cluster",
    )
)
```

The URL is a seed, not a permanent single-node route. redis-py discovers slot
owners and sends commands directly to them. A seed Service can help bootstrap
discovery, but it cannot hide node addresses that are unreachable from the
client. In AKS, a headless Service with stable StatefulSet DNS names is a common
shape when those same DNS names are advertised by Redis and resolvable from
Relayna pods.

Three primary nodes are a practical minimum for a sharded cluster. For
production availability, give each primary one or more replicas and distribute
primary/replica pairs across failure domains. Relayna works with both three
primaries and three primaries plus three replicas; Redis operations and your
platform team remain responsible for quorum, promotion, slot allocation,
resharding, backup, and recovery.

Verify the cluster from the same network context as Relayna. If the application
ACL cannot run `CONFIG GET`, use an operator credential for that check; Relayna
does not itself require access to the `CONFIG` command:

```bash
redis-cli -u "$REDIS_URL" CONFIG GET cluster-enabled
redis-cli -u "$REDIS_URL" CLUSTER INFO
redis-cli -u "$REDIS_URL" CLUSTER SHARDS
redis-cli --cluster check "${REDIS_HOST}:${REDIS_PORT}"
```

`CLUSTER SHARDS` is preferred for inspecting advertised endpoints. If the
server does not expose that command, use `CLUSTER NODES`. Test DNS resolution
and a TCP/TLS connection from a Relayna pod to every advertised endpoint. Add
the matching `redis-cli` authentication and TLS options to `--cluster check`
when the deployment requires them.

Relayna uses cluster hash tags for related SDK keys. That keeps each Lua script,
bulk read, pipeline, and live SSE Pub/Sub operation within the required hash
slot while still allowing different task or service families to distribute
across the cluster.

## AKS examples

| AKS layout | Correct choice | Reason |
| --- | --- | --- |
| Three-pod StatefulSet: pod 0 primary, pods 1-2 replicas; primary-only Service follows failover | `standalone` | There is one writable dataset and no hash-slot sharding. |
| Three-pod StatefulSet: pod 0 primary, pods 1-2 replicas; Service selects all pods | Fix the Service, then use `standalone` | Replica-inclusive balancing can produce `READONLY` errors and stale or inconsistent reads. |
| Three independent Redis Stack pods behind one Service | Unsupported | Requests reach unrelated datasets; pod count does not create replication or clustering. |
| Three Redis Cluster primaries with all 16,384 slots covered and reachable pod DNS | `cluster` | Data is sharded and the client must route by hash slot. |
| Three primaries plus three replicas in a healthy Redis Cluster | `cluster` | Replicas add availability without changing the cluster client model. |
| Sentinel-managed primary and replicas with only Sentinel endpoints exposed | Add a primary-aware endpoint, then use `standalone` | Relayna does not perform Sentinel discovery directly. |

## Wrong-mode and endpoint symptoms

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `MOVED` or `ASK` response reaches the application | `standalone` is connected to a genuine Redis Cluster | Verify slot coverage and advertised endpoints, then use `cluster`. |
| Cluster support is disabled or cluster commands fail | `cluster` is connected to a non-cluster server | Use `standalone`. |
| Intermittent `READONLY` errors | A standalone endpoint includes replicas | Route only to the writable primary. |
| Recently written status is intermittently missing | Requests are reaching replicas or independent pods | Correct the Service/proxy; do not load balance unrelated Redis servers. |
| Seed connects, then operations time out on other hosts | Advertised cluster nodes are unreachable from Relayna | Fix advertised addresses, DNS, ports, network policy, or TLS for every node. |
| Cross-slot errors | Old keys, custom code, or mixed SDK versions are using incompatible key layouts | Complete the 1.7.0 coordinated upgrade in an empty namespace. |
| Authentication or certificate errors vary by node | Cluster nodes do not share a client-compatible auth/TLS configuration | Make credentials, certificates, names, and trust valid for every advertised node. |

## Version and rollout conditions

The SDK depends on `redis>=8.0.1,<9` and always negotiates RESP3 for its runtime
clients. Already deployed Redis data is a separate concern from the Python
client version: redis-py 8 can connect to a compatible existing Redis server,
but Relayna 1.7.0 intentionally changes all SDK Redis key names to
cluster-tagged keys.

Relayna does not read or migrate pre-1.7.0 SDK keys. Use this rollout:

1. Stop or drain every pre-1.7.0 Relayna process that shares the namespace.
2. Preserve the old namespace only if it is needed for a defined rollback
   window.
3. Start all Relayna 1.7.0 processes with the same selected mode and an empty
   namespace.
4. Validate status history, live SSE, observations, DLQ indexing, leases, and
   workflow operations.
5. Discard the old namespace after the rollback window.

Do not mix old and new Relayna SDK processes in the same logical namespace.
Studio's PostgreSQL-backed durable control-plane state is unchanged by this SDK
key migration.

## Local acceptance matrix

Docker Desktop acceptance assets live under `tests/integration/redis/`. The
runner validates standalone Redis Stack, one primary plus two replicas, three
cluster primaries, and three cluster primaries plus three replicas:

```bash
bash tests/integration/redis/run-topologies.sh
```

The runner uses a fixed repository-specific Compose project, runs RESP3 tests
for status history, live SSE, observations, service-event Lua scripts, DLQ
indexing, task leases, workflow contracts, and replication, then removes that
exact project.
