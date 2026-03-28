# Development

## Environment

This repository uses a `src/` layout and `uv` for development workflows.

Install the development environment:

```bash
uv sync --extra dev
```

## Run tests

```bash
uv run --extra dev pytest -q
```

## Run real-stack smoke checks

If you have RabbitMQ on `localhost:5672` and Redis on `localhost:6379`, run:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/real_fastapi_status_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_task_worker_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_workflow_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_sharded_aggregation_smoke.py
PYTHONPATH=src ./.venv/bin/python scripts/real_alias_batch_task_smoke.py
```

The sharded smoke uses deployment-scoped aggregation queue names so repeated
local runs do not collide on the default global shard queue names.

## Build artifacts

```bash
uv build
```

This produces:

- `dist/relayna-<version>.tar.gz`
- `dist/relayna-<version>-py3-none-any.whl`

## Serve the docs locally

```bash
uv run --extra dev mkdocs serve
```

## Design notes

Contributor-facing implementation plans live here when a feature needs design
alignment before code lands. For the proposed multi-stage workflow topology,
see [Workflow topology plan](workflow-topology-plan.md).

## Public API policy

The package root `relayna` is intentionally small and only exposes
`relayna.__version__`.

Import concrete APIs from documented submodules instead of the package root.
