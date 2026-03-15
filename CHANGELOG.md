# Changelog

All notable changes to this project will be documented in this file.

## 1.0.0 - 2026-03-15

`relayna` is now documented and packaged as a public, semver-stable library.

### Stable public modules

The v1 public API consists of the documented symbols from:

- `relayna.config`
- `relayna.contracts`
- `relayna.rabbitmq`
- `relayna.consumer`
- `relayna.status_store`
- `relayna.status_hub`
- `relayna.sse`
- `relayna.history`
- `relayna.fastapi`
- `relayna.observability`

The package root remains intentionally minimal and only exports
`relayna.__version__`.

### Supported behaviors

- RabbitMQ task publishing and shared status fanout
- Redis-backed status history, deduplication, and pubsub
- Server-Sent Events replay plus live updates
- FastAPI lifecycle and route helpers
- RabbitMQ stream history replay for operational endpoints
- Best-effort observability hooks for runtime loops

### Distribution

GitHub Releases are the canonical installation source for v1:

- wheel artifact: `relayna-1.0.0-py3-none-any.whl`
- source artifact: `relayna-1.0.0.tar.gz`

See the release installation guide in the hosted docs for install commands and
upgrade notes.
