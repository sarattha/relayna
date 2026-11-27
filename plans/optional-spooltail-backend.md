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
- [x] (2026-11-21) Shipped an opt-in installer in the Relayna wheel and used it for live Spooltail verification on Python 3.13 and 3.14.
- [x] (2026-11-27) Coordinated the single-task HTTP contract with Gateway, Protocol, and Cell.
- [x] (2026-11-27) Implemented idempotent HTTP submission, broker history, and attempt correlation.
- [x] (2026-11-27) Wired broker history into the registered-service task detail and added the native and Cell reference handlers.
- [x] (2026-11-27) Verified the SDK with the released Spooltail wheel, the Studio registry and task detail against the running reference service, and the optional handler against real Cell/gVisor.
- [x] (2026-11-27) Added the opt-in durable cancellation path and reusable local and Cell profiles.
- [x] (2026-11-27) Projected broker history through Studio’s existing event store and displayed correlated component evidence and usage.
- [x] (2026-11-27) Documented the SDK and Studio environment contract and verified the basic Protocol Compose and Helm configurations.
- [x] (2026-11-27) Added separate SDK and Studio tag validation and release workflows alongside the existing combined tag.

## Surprises & Discoveries

Spooltail's native gRPC client is `relayna-spooltail==0.0.1`. The wheel initially required a source checkout. It is now available in the private repository's [GitHub release](https://github.com/sarattha/relayna-spooltail/releases/tag/v0.0.1). The broker recognizes `completed` and `failed` as terminal and can atomically append either while acknowledging the lease. A retry advances the lease generation twice by the next claim. An expired lease advances it once when reclaimed.

An isolated `uv lock --no-sources` with a named Spooltail extra failed because the package was absent from PyPI. The private browser download URL returned 404 for both Basic and Bearer auth. The GitHub asset API returned a binary redirect with the authenticated octet-stream request. The GitHub CLI handles that request and can download by release tag and wheel name.

## Decision Log

Use a dedicated `relayna.spooltail` namespace for Relayna integration. Applications construct and own the official `relayna_spooltail.SpooltailClient`. They call it directly for publish, latest status, history, and subscriptions. Pass that client to the Relayna consumer, or supply a getter to the FastAPI router so the client can be created on the server's event loop. Package `relayna-install-spooltail` to download the pinned release wheel with the GitHub CLI, verify the digest, and install it into the interpreter running the command. Use that entry point for application setup and integration tests. The public dependency metadata and lockfile continue to resolve without private repository access. Reuse the task and status contracts, retry policy, and shared status-route implementation.

## Outcomes & Retrospective

Both Spooltail integration cases passed with installed wheels on Python 3.13 and 3.14 while RabbitMQ and Redis were stopped. They verified delayed redelivery, renewed leases, atomic successful and failed completion, native results and history, and SSE with exclusive resume and a cursor gap. The default RabbitMQ and Redis smoke test passed on live services. SDK and Studio backend verification passed, and SDK coverage met the 98% gate on Python 3.13 and 3.14. The strict docs build and distribution checks passed. The built Relayna wheel installed in a clean environment with no preinstalled native SDK. Running `relayna-install-spooltail` there installed the official SDK from GitHub. `make test-spooltail` exercised the same installer and passed all 19 tests on each Python version. GitHub read access and differences in workflows, broker administration, and high availability remain deployment constraints.

## Context and Orientation

Work from the Relayna repository root. `src/relayna/consumer` owns worker lifecycle and retry configuration. `src/relayna/rabbitmq` and `src/relayna/status` provide the released RabbitMQ and Redis path. `src/relayna/api/status_routes.py` serves latest status and SSE. Spooltail uses one work topic, one named consumer group that shares task claims, and a separate status topic with per-task history.

## Compatibility Boundary

The contributor guide specifies the v1.4.30 production freeze and the latest repository release is v1.14.0. Replace the unreleased client wrapper directly. Preserve released public signatures, RabbitMQ/Redis settings, envelopes, and route responses. Leave the freeze manifests as recorded.

The core milestone extends the unreleased Spooltail router. Existing Studio task-detail routes and response models carry Spooltail history through the existing `history` field. A capability flag selects the task-scoped broker endpoint. Released RabbitMQ and Redis paths continue to use their existing history route and stream-offset semantics.

## Core task milestone

Spooltail owns one canonical work event with event ID `task` per task ID and work topic. The HTTP submitter binds submissions to its configured service. It publishes first and reads the committed record when the broker reports a conflict. An omitted creation timestamp adopts the first record's timestamp for duplicate comparison. An explicit timestamp must match the stored instant. Broker append is the only write in submission. SDK status events carry a per-claim opaque attempt ID. The reference service uses the native handler by default and can invoke a deployment-configured Cell launcher with a fixed image for one trusted task at a time.

Equivalent concurrent HTTP requests returned 202 and produced one work record against Spooltail v0.0.1. Differing inputs returned 409, and HTTP history and SSE replay resumed exclusively after the same event ID. The SDK reclaimed an abandoned lease and reported the result with an opaque attempt ID. A running reference service registered in Studio returned the same status, result, and history. The optional handler ran in real Cell/gVisor with the launcher from Cell commit `c36918cd`. The broker recorded the cell and execution identifiers, and the guest container was confirmed deleted.

## Expanded milestone

Phase 6 is excluded. Applications opt in to a separate cancellation topic only with a broker that accepts atomic completion as `cancelled`. A stable control record represents intent, and a worker checks the record before starting a task and while waiting for its handler. The lease renews through cooperative cancellation and cleanup. The broker remains the authority for the terminal result. Existing handlers and transports continue to use their current APIs.

Reuse Studio’s existing event ingest and Postgres store for durable task evidence. Keep broker status ordered by the broker cursor. Associate Gateway and Cell observations by the registered service, task ID, and available attempt and cell IDs. Keep any additional read endpoints scoped to the existing Studio outbound and operator policies. Package SDK and Studio entry points for trusted local integration and simple Helm installs using deployment-supplied images and infrastructure.

The released Spooltail Python wheel works with both broker versions. The older broker passed the single-task contract with cancellation disabled. The upgraded broker passed queued and active cancellation, cleanup, status, history, and SSE. The ecosystem coordinator exercised scoped inference through a real Cell/gVisor runtime and Gateway with a synthetic upstream. The broker stored Cell audit events, and Gateway attributed the synthetic tokens to the same task attempt. Studio applies the 64-event Cell audit bound and reads Gateway usage only for explicitly mapped services through the operator export. The production freeze authorization covers these additive profiles and features. Existing RabbitMQ and Redis callers and freeze manifests remain compatible.

The release entry point selects SDK artifacts for `sdk-vX.Y.Z`, Studio artifacts for `studio-vX.Y.Z`, or both for the existing `vX.Y.Z` tag. Studio shares one backend/frontend version and validates its explicit minimum SDK against the SDK bundled in its container. Package versions and combined assets stay at their existing values. Local release fixtures exercise version divergence, changelog selection and asset selection without creating Git tags or publishing.

## Plan of Work

Implement Relayna task serialization, handler semantics, and the FastAPI integration under `src/relayna/spooltail`. Let the official SDK own the connection and all broker operations. Document deployment, retry accounting, cursors, and unsupported features in `docs/spooltail.md`. Use focused tests with the optional package absent and with a real local broker.

## Concrete Steps

Run these commands from the repository root after installing development dependencies.

    make sync
    make -C studio/backend sync
    bash .codex/skills/code-change-verification/scripts/run.sh
    make coverage
    SPOOLTAIL_TEST_TARGET=127.0.0.1:<port> make test-spooltail

The last command invokes `relayna-install-spooltail` with the authenticated GitHub CLI. It requires a local plaintext loopback broker built from the release tag.

## Validation and Acceptance

The default stack passes, including production-freeze checks. A real broker accepts a task through Relayna, delivers it to a worker, delays a retry when requested, settles the lease, and returns canonical terminal status with the result through latest, history, and SSE. The flow uses the Spooltail broker independently.

## Idempotence and Recovery

Use distinct topics per end-to-end test run. Keep the broker source, build, and data in temporary directories. Reuse the same event ID and serialized payload for a publish retry. A lost worker lease becomes available to other workers in its group.

## Interfaces and Dependencies

The optional native client connects to a gRPC target with TLS by default. Applications share explicit topic and consumer group names. The distribution installer requires `uv` and an authenticated GitHub CLI. Spooltail status cursors are event IDs.
