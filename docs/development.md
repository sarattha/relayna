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

## Public API policy

The package root `relayna` is intentionally small and only exposes
`relayna.__version__`.

Import concrete APIs from documented submodules instead of the package root.
