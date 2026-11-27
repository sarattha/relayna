# Contributing to Relayna's ecosystem docs

Open a pull request here for cross-component architecture, deployment
profiles, the quickstart, or the component directory. Use each component's
repository for its implementation and operating guides.

## Build and preview

Use Python 3.12 or later and uv.

```sh
uv run --no-project --with-requirements requirements-docs.txt mkdocs build --strict
uv run --no-project --with-requirements requirements-docs.txt mkdocs serve
```

Open the address printed by MkDocs. Check the modified pages, navigation, and
redirects. For a quickstart change, run the commands against the exact Protocol
revision being documented and confirm the task appears in Studio.

GitHub Actions builds the docs on pull requests and publishes GitHub Pages
after a successful build on `main`.
