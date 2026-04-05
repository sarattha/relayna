# Migrate From v1 To v2

Relayna v2 is an intentional breaking change. The main shift is structural:
the old flat public modules are gone, and the library now expects users to
import from package roots grouped by responsibility.

There are no v1 compatibility shims in v2. If your code still imports
`relayna.fastapi`, `relayna.status_store`, `relayna.status_hub`,
`relayna.sse`, or `relayna.history`, it must be updated.

## What changed

In v1, the public API was mostly described as flat modules. In v2, the public
surface is organized by package roots:

- `relayna.topology`
- `relayna.contracts`
- `relayna.workflow`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status`
- `relayna.observability`
- `relayna.api`
- `relayna.mcp`
- `relayna.studio`
- `relayna.dlq`

The root package stays minimal and only exports `relayna.__version__`.

## Migration strategy

Use this order:

1. Replace removed v1 imports with v2 package-root imports.
2. Update FastAPI wiring to use `relayna.api`.
3. Update status/history/SSE imports to use `relayna.status`.
4. Update any docs, examples, tests, and monkeypatch targets in your repo.
5. Run your real RabbitMQ/Redis smoke tests after the import conversion.

If you do the migration incrementally, start with imports and route wiring
first. That gets most projects compiling again before you touch tests or docs.

## Import mapping

The most important import replacements are:

| v1 import | v2 import |
| --- | --- |
| `from relayna.fastapi import create_relayna_lifespan` | `from relayna.api import create_relayna_lifespan` |
| `from relayna.fastapi import get_relayna_runtime` | `from relayna.api import get_relayna_runtime` |
| `from relayna.fastapi import create_status_router` | `from relayna.api import create_status_router` |
| `from relayna.fastapi import create_dlq_router` | `from relayna.api import create_dlq_router` |
| `from relayna.status_store import RedisStatusStore` | `from relayna.status import RedisStatusStore` |
| `from relayna.status_hub import StatusHub` | `from relayna.status import StatusHub` |
| `from relayna.sse import SSEStatusStream` | `from relayna.status import SSEStatusStream` |
| `from relayna.history import StreamHistoryReader` | `from relayna.status import StreamHistoryReader` |

These v2 imports stay conceptually the same and are still the primary entry
points for their domains:

| Domain | v2 import root |
| --- | --- |
| topology declarations | `relayna.topology` |
| canonical wire envelopes | `relayna.contracts` |
| RabbitMQ transport | `relayna.rabbitmq` |
| worker runtimes and contexts | `relayna.consumer` |
| status storage, hub, SSE, history | `relayna.status` |
| FastAPI runtime and routes | `relayna.api` |
| dead-letter indexing and replay | `relayna.dlq` |
| runtime observations | `relayna.observability` |
| workflow control-plane helpers | `relayna.workflow` |

## Step 1: Replace flat imports

Search your application for removed v1 imports:

```bash
rg -n "relayna\.(fastapi|status_store|status_hub|sse|history)"
```

Then replace them with the v2 package-root imports from the table above.

Also check test code and monkeypatch targets. For example, old tests that
patched `relayna.fastapi` internals should now patch modules inside
`relayna.api`, such as `relayna.api.fastapi_lifespan` or
`relayna.api.status_routes`.

## Step 2: Update FastAPI wiring

### v1 style

```python
from fastapi import FastAPI

from relayna.fastapi import create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.topology import SharedTasksSharedStatusTopology

topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
    )
)

runtime = get_relayna_runtime(app)
app.include_router(
    create_status_router(
        sse_stream=runtime.sse_stream,
        history_reader=runtime.history_reader,
        latest_status_store=runtime.store,
    )
)
```

### v2 style

```python
from fastapi import FastAPI

from relayna.api import create_relayna_lifespan, create_status_router, get_relayna_runtime
from relayna.topology import SharedTasksSharedStatusTopology

topology = SharedTasksSharedStatusTopology(
    rabbitmq_url="amqp://guest:guest@localhost:5672/",
    tasks_exchange="tasks.exchange",
    tasks_queue="tasks.queue",
    tasks_routing_key="task.request",
    status_exchange="status.exchange",
    status_queue="status.queue",
)

app = FastAPI(
    lifespan=create_relayna_lifespan(
        topology=topology,
        redis_url="redis://localhost:6379/0",
    )
)

runtime = get_relayna_runtime(app)
app.include_router(
    create_status_router(
        sse_stream=runtime.sse_stream,
        history_reader=runtime.history_reader,
        latest_status_store=runtime.store,
    )
)
```

The wiring is nearly identical. The important change is that FastAPI runtime
and route helpers now live under `relayna.api`.

## Step 3: Update direct status/SSE/history usage

### v1 style

```python
from relayna.history import StreamHistoryReader
from relayna.sse import SSEStatusStream
from relayna.status_hub import StatusHub
from relayna.status_store import RedisStatusStore
```

### v2 style

```python
from relayna.status import RedisStatusStore, SSEStatusStream, StatusHub, StreamHistoryReader
```

v2 treats status storage, hub fanout, SSE delivery, and stream replay as one
coherent domain owned by `relayna.status`.

## Step 4: Keep topology, contracts, transport, and workers separated

If you are moving a larger codebase, use the package boundaries intentionally:

- use `relayna.topology` for exchange/queue layout definitions only
- use `relayna.contracts` for envelope types and alias helpers only
- use `relayna.rabbitmq` for broker transport and publishing only
- use `relayna.consumer` for worker execution and handler contexts only
- use `relayna.api` for FastAPI runtime and route composition only
- use `relayna.status` for Redis-backed status state and delivery only

This matters because v2 is designed around those ownership boundaries. If you
continue treating the library like a flat module namespace, future upgrades
will be harder.

## Step 5: Update docs, examples, and tests in your application

After imports compile again, update the surrounding project:

- change README or internal docs snippets that still show removed v1 imports
- update test monkeypatch targets to point at `relayna.api.*`,
  `relayna.status.*`, `relayna.consumer.*`, or `relayna.rabbitmq.*`
- remove any internal assumptions that `relayna` exposes concrete runtime
  helpers at the package root

Recommended verification:

```bash
make format
make lint
make test
```

If your stack uses live infrastructure, also run your RabbitMQ/Redis smoke
tests after the import migration.

## Common mistakes

- Importing from `relayna` directly instead of a package root.
  Only `relayna.__version__` is exported from the package root.
- Treating `relayna.api` as a transport package.
  `relayna.api` composes runtime objects for FastAPI; broker lifecycle and
  publishing still belong to `relayna.rabbitmq`.
- Mixing status responsibilities into FastAPI code.
  `StatusHub`, `RedisStatusStore`, `SSEStatusStream`, and `StreamHistoryReader`
  now belong together under `relayna.status`.
- Assuming there are compatibility aliases for removed v1 modules.
  There are none in v2.

## Migration checklist

- Replace all removed flat-module imports.
- Update FastAPI imports to `relayna.api`.
- Update status/history/SSE imports to `relayna.status`.
- Update tests and monkeypatch targets.
- Re-run unit tests.
- Re-run real-stack RabbitMQ/Redis smoke tests.

## See also

- [Overview](index.md)
- [Getting started](getting-started.md)
- [Components](components.md)
