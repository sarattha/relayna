# Redis Topologies

Relayna supports two explicit Redis client modes. Both use redis-py 8 and
RESP3.

| Deployment | `redis_mode` | Relayna endpoint |
| --- | --- | --- |
| One standalone Redis or Redis Stack server | `standalone` | The writable server. |
| One primary with one or more replicas | `standalone` | A primary-aware service or proxy that always routes writes to the current primary. |
| Genuine sharded Redis Cluster | `cluster` | Any reachable cluster seed node. |

`standalone` remains the default:

```python
app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://redis-primary.default.svc.cluster.local:6379/0",
        redis_mode="standalone",
    )
)
```

Select `cluster` only for a genuine Redis Cluster with hash slots:

```python
app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://redis-cluster-0.redis-cluster.default.svc.cluster.local:6379/0",
        redis_mode="cluster",
    )
)
```

Relayna does not infer topology from the URL. A three-pod StatefulSet is not
necessarily Redis Cluster: it may be one primary plus two replicas. In that
case, use `standalone` and give Relayna a primary-aware endpoint. Do not use a
load-balanced service that can send writes to read-only replicas. Relayna does
not currently perform Sentinel discovery or primary promotion itself.

For `cluster` mode, every endpoint advertised by Redis Cluster must be
resolvable and reachable from the Relayna process. Redis Cluster supports only
database zero, so the seed URL must not select another database. Relayna uses
cluster hash tags for related keys, allowing its Lua scripts, bulk reads,
pipelines, and live SSE Pub/Sub path to work across a sharded deployment.

## Versions and upgrade behavior

The SDK depends on `redis>=8.0.1,<9` and always negotiates RESP3 for runtime
clients. Redis server 7.2 or later is required by redis-py 8. The repository's
real-environment suite is pinned to Redis Stack Server 7.4.0-v8.

This change intentionally replaces all previous SDK Redis key names with
cluster-tagged keys. It does not read or migrate old keys. Upgrade all Relayna
processes that share a Redis namespace together and start with an empty
namespace, or explicitly discard the old namespace after its rollback window.

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
