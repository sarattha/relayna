# Contributor guide

This repository is the Relayna ecosystem entry point. Keep the overview,
architecture, quickstart, and deployment profiles in `docs/`. Put component
implementation and component-specific guides in their owning repositories.

Update the README and MkDocs navigation when adding an ecosystem guide. Link
to the owning repository for current commands and contracts. Keep the
reference stack pinned to a verified Deploy revision and document legacy
redirects when moving published documentation URLs.

Run the strict documentation build before opening a change.

```sh
uv run --no-project --with-requirements requirements-docs.txt mkdocs build --strict
```

See [contributor setup](CONTRIBUTING.md) for preview and review guidance.
