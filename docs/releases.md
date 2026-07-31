# Releases and installation

## Release model

`relayna` v1 is published through GitHub Releases.

- Releases page: [github.com/sarattha/relayna/releases](https://github.com/sarattha/relayna/releases)
- Source repository: [github.com/sarattha/relayna](https://github.com/sarattha/relayna)

Each release publishes:

- a wheel for direct installation
- a source distribution for source-based installs

## Install the wheel

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.32/relayna-1.4.32-py3-none-any.whl
```

## Install the source distribution

```bash
pip install https://github.com/sarattha/relayna/releases/download/v1.4.32/relayna-1.4.32.tar.gz
```

## Build artifacts locally

```bash
uv build
```

Expected artifacts:

- `dist/relayna-1.4.32.tar.gz`
- `dist/relayna-1.4.32-py3-none-any.whl`

## Versioning policy

The SDK, Studio backend, and Studio frontend share one stable SemVer release
line. The documented SDK API, documented Studio backend API, and
frontend/backend Studio contract follow semantic versioning. Undocumented
internals may change outside of SemVer guarantees.

### Upgrading to 1.4.32

No data, wire-format, configuration, or public-API migration is required.
Relayna `1.4.32` reuses the OpenTelemetry API's standard module-level proxy
tracer and stateless carrier adapters instead of repeating provider/tracer
lookup and wrapper allocation for every producer and consumer span. Tracing,
sampling, W3C/baggage propagation, span names and relationships, attributes,
exception/status handling, async context isolation, and exporter delivery
remain enabled and unchanged.

Upgrade the SDK, Studio backend, and Studio frontend together to keep the
shared release line aligned.

### Upgrading to 1.4.31

No data, wire-format, configuration, or public-API migration is required.
Relayna `1.4.31` snapshots delivered AMQP metadata once per task, workflow, or
aggregation message and reuses that internal immutable value for tracing,
contexts, retries, dead-letter publication, metrics, and observations. Header
precedence and defaults, handler and middleware-visible context values,
acknowledgement/rejection/requeue behavior, and task/status/workflow semantics
remain unchanged.

Upgrade the SDK, Studio backend, and Studio frontend together to keep the
shared release line aligned.

### Upgrading to 1.4.30

Relayna `1.4.30` moves AMQP JSON transport encoding and parsing to Pydantic
Core. Review the
[JSON transport migration after v1.4.29](json-transport-migration.md) before
upgrading because raw outbound bytes, invalid UTF-8 handling, and some input
coercions intentionally differ from `1.4.29`.

The consumer performance change needs no migration. `TaskConsumer` skips
resource sampling and successful-path observation construction only when no
observation sink or metrics recorder could receive the result. Observation-only,
metrics-only, combined instrumentation, OpenTelemetry tracing, acknowledgements,
retries, lifecycle statuses, and message contracts keep their existing
behavior. Upgrade the SDK, Studio backend, and Studio frontend together to keep
the shared release line aligned.

### Upgrading to 1.4.29

No data migration or API change is required. Studio `1.4.29` makes
Prometheus-backed pod ownership joins resilient to duplicate
`kube_pod_labels` series and adds bounded upstream error diagnostics. Upgrade
the SDK, Studio backend, and Studio frontend together to keep the shared release
line aligned.

### Upgrading to 1.4.28

The service-event Redis feed storage changes in `1.4.28`. The SDK keeps the
same `GET /events/feed` contract but does not read or migrate the old
`{prefix}:feed` list. Upgrade all SDK instances sharing a service-event prefix
together. The indexed feed begins with new post-upgrade events; after the old
instances are drained, the legacy list can be deleted. See
[Redis Keys](redis-keys.md#service-event-feed) for the v2 keys.
