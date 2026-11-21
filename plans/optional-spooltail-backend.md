# Optional Spooltail backend for Relayna

Maintain this ExecPlan according to [PLANS.md](../PLANS.md). The living sections are Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective.

## Purpose / Big Picture

A Relayna service can submit tasks, run workers, and retrieve status and results using a Spooltail broker. The application uses the canonical Relayna task and status envelopes. RabbitMQ and Redis remain the default infrastructure for existing applications.

## Progress

- [x] (2026-11-21) Inspected Spooltail's merged Python SDK, broker implementation, and distribution availability.
- [x] (2026-11-21) Implemented the opt-in client, worker, and status integration.
- [x] (2026-11-21) Documented installation, both configurations, and capability differences.
- [x] (2026-11-21) Ran Relayna's verification stack and tested both backend flows against local services.

## Surprises & Discoveries

Spooltail's native gRPC client is `relayna-spooltail==0.0.1` at commit `4e59f51202de1a4f360378119a7f9f660bfa2379`. Its private Git repository has no GitHub releases, and the distribution is unavailable on public PyPI. The pinned source builds a Python wheel. The broker recognizes `completed` and `failed` as terminal and can atomically append either while acknowledging the lease. A retry advances the lease generation twice by the next claim. An expired lease advances it once when reclaimed.

## Decision Log

On 2026-11-21, use a dedicated `relayna.spooltail` namespace with explicit client and worker configuration. Keep the dependency separately installable from the pinned source or wheel so the public Relayna distribution and lockfile continue to resolve without access to a private repository. Reuse the task and status contracts, retry policy, and shared status-route implementation. Use native leases and retain terminal results in the broker.

## Outcomes & Retrospective

The native SDK wheel and local broker were built from a temporary Git archive of the merged ref. Both Spooltail integration cases passed on Python 3.13 and 3.14 with RabbitMQ and Redis stopped. They verified delayed redelivery, renewed leases, atomic successful and failed completion, results, and SSE with exclusive resume and a cursor gap. The default RabbitMQ and Redis smoke test passed on live services. SDK and Studio backend verification passed, and SDK coverage met the 98% gate on Python 3.13 and 3.14. The strict docs build, wheel metadata validation, and installed-wheel import smoke test also passed. Private source distribution and differences in workflows, broker administration, and high availability remain deployment constraints.

## Context and Orientation

Work from the Relayna repository root. `src/relayna/consumer` owns worker lifecycle and retry configuration. `src/relayna/rabbitmq` and `src/relayna/status` provide the released RabbitMQ and Redis path. `src/relayna/api/status_routes.py` serves latest status and SSE. Spooltail uses one work topic, one named consumer group that shares task claims, and a separate status topic with per-task history.

## Compatibility Boundary

The contributor guide specifies the v1.4.30 production freeze and the latest repository release is v1.14.0. Add the explicitly requested backend under its own namespace. Preserve existing public signatures, RabbitMQ/Redis settings, envelopes, and route responses. Leave the freeze manifests as recorded.

## Plan of Work

Implement publish, consume, and status reads under `src/relayna/spooltail`. Let the worker renew leases while a handler runs and settle them via the native SDK. Document deployment, retry accounting, cursors, and unsupported features in `docs/spooltail.md`. Use focused tests with the optional package absent and with a real local broker.

## Concrete Steps

Run these commands from the repository root after installing development dependencies.

    make sync
    make -C studio/backend sync
    bash .codex/skills/code-change-verification/scripts/run.sh
    make coverage
    SPOOLTAIL_TEST_TARGET=127.0.0.1:<port> uv run pytest tests/test_spooltail.py

The last command requires the pinned wheel installed in the SDK test environment and a local plaintext loopback broker built from the pinned revision.

## Validation and Acceptance

The default stack passes, including production-freeze checks. A real broker accepts a task through Relayna, delivers it to a worker, delays a retry when requested, settles the lease, and returns canonical terminal status with the result through latest, history, and SSE. The flow uses the Spooltail broker independently.

## Idempotence and Recovery

Use distinct topics per end-to-end test run. Keep the broker source, build, and data in temporary directories. Reuse the same event ID and serialized payload for a publish retry. A lost worker lease becomes available to other workers in its group.

## Interfaces and Dependencies

The optional native client connects to a gRPC target with TLS by default. Applications share explicit topic and consumer group names. The native Python distribution is installed separately. Spooltail status cursors are event IDs.
