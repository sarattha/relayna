# Redis topology integration environments

These Docker Desktop environments use Redis Stack Server 7.4 and expose four
deployment shapes:

- `standalone`: one writable Redis Stack server.
- `replication`: one primary and two read replicas. Relayna connects to the
  primary-aware endpoint; the integration test separately verifies both
  replicas receive the writes.
- `cluster`: three Redis Cluster primaries.
- `replicated-cluster`: three Redis Cluster primaries and three replicas.

Run all four, sequentially, from the repository root:

```bash
bash tests/integration/redis/run-topologies.sh
```

The script uses a fixed, repository-specific Compose project name, waits for
health checks, creates each Redis Cluster, runs both real-Redis regression test
modules with RESP3, and tears down that exact project between environments.

The suite pre-populates a completed seven-page task before opening the real
`GET /events/{task_id}` HTTP route, then asserts that SSE replays the stored
terminal event and finishes without falling through to keepalives. This is the
end-to-end regression for issue #123.
