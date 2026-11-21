# Optional Spooltail backend for Relayna

Maintain this ExecPlan according to [PLANS.md](../PLANS.md). The living sections are Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective.

## Purpose / Big Picture

A Relayna service can submit tasks, run workers, and retrieve status and results using a Spooltail broker. The application uses the canonical Relayna task and status envelopes. RabbitMQ and Redis remain the default infrastructure for existing applications.

## Progress

- [x] (2026-11-21) Inspected Spooltail's merged Python SDK, broker implementation, and distribution availability.
- [x] (2026-11-21) Implemented opt-in worker and status integration.
- [x] (2026-11-21) Documented installation, both configurations, and capability differences.
- [x] (2026-11-21) Ran Relayna's verification stack and tested both backend flows against local services.
- [x] (2026-11-21) Replaced the draft client wrapper with direct use of the official SDK at the worker and FastAPI entry points.
- [x] (2026-11-21) Verified the revision against both brokers and checked installation and metadata for both wheels.
- [x] (2026-11-21) Updated deployment and integration testing to download the official wheel from the pinned GitHub release.

## Surprises & Discoveries

Spooltail's native gRPC client is `relayna-spooltail==0.0.1`. The wheel initially required a source checkout. It is now available in the private repository's [GitHub release](https://github.com/sarattha/relayna-spooltail/releases/tag/v0.0.1). The broker recognizes `completed` and `failed` as terminal and can atomically append either while acknowledging the lease. A retry advances the lease generation twice by the next claim. An expired lease advances it once when reclaimed.

An isolated `uv lock --no-sources` with a named Spooltail extra failed because the package was absent from PyPI. Installing the pinned Git reference with repository access succeeded. The existing Relayna release tooling and CI can use the same public wheel dependency metadata and lockfile.

## Decision Log

Use a dedicated `relayna.spooltail` namespace for Relayna integration. Applications construct and own the official `relayna_spooltail.SpooltailClient`. They call it directly for publish, latest status, history, and subscriptions. Pass that client to the Relayna consumer, or supply a getter to the FastAPI router so the client can be created on the server's event loop. Download the pinned release wheel only for Spooltail deployments and integration tests so the public Relayna distribution and lockfile continue to resolve without private repository access. Reuse the task and status contracts, retry policy, and shared status-route implementation.

## Outcomes & Retrospective

Both Spooltail integration cases passed with installed wheels on Python 3.13 and 3.14 while RabbitMQ and Redis were stopped. They verified delayed redelivery, renewed leases, atomic successful and failed completion, native results and history, and SSE with exclusive resume and a cursor gap. The default RabbitMQ and Redis smoke test passed on live services. SDK and Studio backend verification passed, and SDK coverage met the 98% gate on Python 3.13 and 3.14. The strict docs build, wheel metadata validation, and default installed-wheel import test also passed. After the official wheel was published, `make test-spooltail` downloaded it from GitHub and passed all 19 tests on each Python version. GitHub read access and differences in workflows, broker administration, and high availability remain deployment constraints.

## Context and Orientation

Work from the Relayna repository root. `src/relayna/consumer` owns worker lifecycle and retry configuration. `src/relayna/rabbitmq` and `src/relayna/status` provide the released RabbitMQ and Redis path. `src/relayna/api/status_routes.py` serves latest status and SSE. Spooltail uses one work topic, one named consumer group that shares task claims, and a separate status topic with per-task history.

## Compatibility Boundary

The contributor guide specifies the v1.4.30 production freeze and the latest repository release is v1.14.0. Replace the unreleased client wrapper directly. Preserve released public signatures, RabbitMQ/Redis settings, envelopes, and route responses. Leave the freeze manifests as recorded.

## Plan of Work

Implement Relayna task serialization, handler semantics, and the FastAPI integration under `src/relayna/spooltail`. Let the official SDK own the connection and all broker operations. Document deployment, retry accounting, cursors, and unsupported features in `docs/spooltail.md`. Use focused tests with the optional package absent and with a real local broker.

## Concrete Steps

Run these commands from the repository root after installing development dependencies.

    make sync
    make -C studio/backend sync
    bash .codex/skills/code-change-verification/scripts/run.sh
    make coverage
    SPOOLTAIL_TEST_TARGET=127.0.0.1:<port> make test-spooltail

The last command downloads the pinned wheel with the authenticated GitHub CLI. It requires a local plaintext loopback broker built from the release tag.

## Validation and Acceptance

The default stack passes, including production-freeze checks. A real broker accepts a task through Relayna, delivers it to a worker, delays a retry when requested, settles the lease, and returns canonical terminal status with the result through latest, history, and SSE. The flow uses the Spooltail broker independently.

## Idempotence and Recovery

Use distinct topics per end-to-end test run. Keep the broker source, build, and data in temporary directories. Reuse the same event ID and serialized payload for a publish retry. A lost worker lease becomes available to other workers in its group.

## Interfaces and Dependencies

The optional native client connects to a gRPC target with TLS by default. Applications share explicit topic and consumer group names. The native Python distribution is installed separately. Spooltail status cursors are event IDs.
