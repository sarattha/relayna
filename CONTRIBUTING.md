# Contributing

## Local setup

Use Python 3.13 or newer and install the development environment with `uv`:

```bash
uv sync --extra dev
```

## Common commands

Run the test suite:

```bash
uv run --extra dev pytest -q
```

Build the distribution artifacts:

```bash
uv build
```

Build the docs locally:

```bash
uv run --extra dev mkdocs serve
```

## Pull requests

- Keep changes focused and update docs when the public behavior changes.
- Preserve the documented submodule imports as the public API surface.
- Add or update tests when behavior or packaging guarantees change.
- Ensure `uv run --extra dev pytest -q` and `uv build` both pass before opening a PR.
