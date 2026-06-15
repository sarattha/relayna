# Concurrent TaskConsumer Dispatch

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

If `PLANS.md` is present in the repo, maintain this document in accordance with
it and link back to it by path.

## Purpose / Big Picture

`TaskConsumer` should use RabbitMQ `prefetch` as an actual concurrency limit for
I/O-bound task handlers. With `prefetch` greater than one, a single consumer
process can handle multiple messages concurrently while keeping per-message
ack, reject, retry, DLQ, lifecycle status, observation, metrics, and lease
behavior inside the existing message handler.

## Progress

- [x] (2026-06-15 09:25Z) Confirmed issue #100 and current sequential loop.
- [x] (2026-06-15 09:25Z) Created branch `codex/concurrent-task-consumer-dispatch`.
- [x] (2026-06-15 09:35Z) Implemented bounded concurrent dispatch in
  `TaskConsumer.run_forever`.
- [x] (2026-06-15 09:35Z) Added SDK tests for sequential compatibility, concurrency, semaphore cap,
  and shutdown draining.
- [x] (2026-06-15 09:35Z) Ran focused consumer tests:
  `uv run pytest tests/test_consumer.py -q`.
- [x] (2026-06-15 09:38Z) Ran `make format`, `make lint`,
  `make typecheck`, and `make test`.
- [x] (2026-06-15 09:38Z) Ran
  `bash .codex/skills/code-change-verification/scripts/run.sh`.
- [x] (2026-06-15 09:51Z) Ran a real Docker smoke test against disposable
  RabbitMQ and Redis containers.
- [x] (2026-06-15 09:53Z) Ran a real Docker smoke test with `prefetch=20`
  and 25 RabbitMQ messages.
- [x] (2026-06-15 12:16Z) Bumped release metadata to `1.4.25`, updated
  changelog and install docs, and documented `TaskConsumer` prefetch
  concurrency.
- [x] (2026-06-15 12:16Z) Re-ran Python verification plus Studio frontend
  tests and build after version/docs updates.
- [x] (2026-06-15 12:20Z) Moved SDK, Studio backend, and Studio frontend
  production freeze manifests and tests to the `v1.4.25` perimeter.
- [x] (2026-06-15 12:20Z) Re-ran Python verification plus Studio frontend
  tests and build after the freeze-perimeter update.

## Surprises & Discoveries

- Observation: `TaskConsumer.run_forever` already passes the effective prefetch
  to RabbitMQ QoS but awaits `_handle_message` inside the iterator loop.
  Evidence: `src/relayna/consumer/task_consumer.py`.
- Observation: Existing tests have fake RabbitMQ queues, channels, and messages
  that can support deterministic async concurrency tests without a live broker.
  Evidence: `tests/test_consumer.py`.

## Decision Log

- Decision: Preserve the existing sequential code path for `prefetch <= 1`.
  Rationale: Default topology uses `prefetch_count=1`; preserving this path
  protects ordering-sensitive deployments and existing tests.
  Date/Author: 2026-06-15 / Codex.
- Decision: For `prefetch > 1`, schedule message handling with
  `asyncio.create_task` and bound it with `asyncio.Semaphore`.
  Rationale: This implements issue #100 with standard asyncio primitives and no
  public API changes.
  Date/Author: 2026-06-15 / Codex.
- Decision: Stop intake on `stop()` but drain in-flight handlers before closing
  the channel.
  Rationale: `_handle_message` owns ack/reject/retry/DLQ; closing the channel
  before handlers finish risks losing those per-message outcomes.
  Date/Author: 2026-06-15 / Codex.
- Decision: User-approved freeze exception permits this released behavior
  change without adding a compatibility flag.
  Rationale: The user explicitly approved breaking freeze perimeters for this
  feature.
  Date/Author: 2026-06-15 / Codex.

## Outcomes & Retrospective

Implemented concurrent task dispatch for `TaskConsumer` when effective
`prefetch` is greater than one. Focused and full verification passed. The
change keeps public SDK signatures and serialized contracts unchanged while
altering runtime behavior for deployments that opt into `prefetch > 1`.
Real-container smoke verification also passed with RabbitMQ task publishing and
consuming plus Redis status-store read/write. A `prefetch=20` smoke test with
25 messages observed `max_active=20` and confirmed task 21 did not start before
the first 20 handlers were released. Release metadata and docs now describe the
`1.4.25` feature release, and the freeze manifests now use `v1.4.25` as the
strict production perimeter.

## Context and Orientation

The SDK package lives under `src/relayna/`. `TaskConsumer` is the shared task
worker runtime in `src/relayna/consumer/task_consumer.py`. It consumes RabbitMQ
messages, normalizes task envelopes, invokes the configured handler, and then
acks, rejects, retries, or dead-letters the message.

The current loop computes `prefetch` from the constructor override or topology
default, applies it to the acquired channel, and then handles one message at a
time. The new behavior should only affect `TaskConsumer`; `AggregationConsumer`
and `WorkflowConsumer` are out of scope.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.24`; production freeze
boundary `v1.4.21`. This changes released SDK runtime behavior when
`prefetch > 1`, but keeps public imports, constructor signatures, envelope
formats, RabbitMQ queue declarations, and Redis/persisted schemas unchanged.

## Plan of Work

In `TaskConsumer.run_forever`, keep the current body for `prefetch <= 1`. Add a
concurrent dispatch path for `prefetch > 1` that:

- creates a semaphore sized to `prefetch`,
- acquires the semaphore before reading or scheduling a message,
- schedules `_handle_message` with `asyncio.create_task`,
- releases the semaphore in a task wrapper `finally`,
- tracks in-flight tasks in a set and removes them when complete,
- stops intake when `stop()` is set, and
- awaits all in-flight tasks before leaving the iterator context and closing the
  channel.

Add tests in `tests/test_consumer.py` using the existing fake helpers to prove:

- `prefetch=1` remains sequential,
- `prefetch=2` starts a second handler while the first is blocked,
- concurrency is capped at the effective prefetch count, and
- `stop()` drains in-flight handlers before channel close.

## Concrete Steps

    cd /Users/jobz/Works/relayna
    git switch -c codex/concurrent-task-consumer-dispatch
    make format
    make lint
    make typecheck
    make test
    bash .codex/skills/code-change-verification/scripts/run.sh

## Validation and Acceptance

Acceptance requires all new and existing SDK tests to pass. The new tests must
demonstrate observable overlap only when `prefetch > 1`, and must verify that
the channel is closed only after in-flight messages have finished.

## Idempotence and Recovery

The branch can be recreated from `main` if no commits have been made. The
verification commands are safe to rerun. If a formatter changes files, inspect
the diff and rerun lint/typecheck/tests afterward.

## Artifacts and Notes

GitHub issue: `https://github.com/sarattha/relayna/issues/100`.

## Interfaces and Dependencies

No new public API, dependency, environment variable, queue argument, Redis key,
or wire envelope is introduced. The implementation uses Python standard library
`asyncio.Semaphore` and `asyncio.create_task`.
