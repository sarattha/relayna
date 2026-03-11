# Development

## Environment

This repository uses `uv` and a `src/` layout.

Install development dependencies:

```bash
uv sync --extra dev
```

## Run tests

Use one of these commands:

```bash
uv run --extra dev pytest -q
```

or, after syncing the environment:

```bash
uv run pytest -q
```

## Build artifacts

```bash
uv build
```

This produces:

- `dist/relayna-<version>.tar.gz`
- `dist/relayna-<version>-py3-none-any.whl`

## Public API policy

The package root `relayna` is intentionally small and only exposes
`relayna.__version__`.

Import all concrete runtime APIs from submodules:

```python
from relayna.rabbitmq import RelaynaRabbitClient
from relayna.status_store import RedisStatusStore
```

This keeps imports predictable and avoids turning the package root into a
catch-all compatibility layer.
