# Retry Priority and StatusHub Fail-Fast

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan follows `PLANS.md`.

## Purpose / Big Picture

Implement GitHub issues #103 and #102 for the Relayna SDK.

After this change, operators can configure broker retries to publish delayed
retry messages with progressively elevated AMQP priority, allowing repeat
failures to move ahead of newer lower-priority work on queues declared with
`x-max-priority`.

Operators can also configure `StatusHub` to fail fast for selected startup
exception types before it has successfully opened the status queue iterator,
so a FastAPI lifespan or similar supervisor can abort startup instead of
running healthy while status consumption is broken.

## Progress

- [x] (2026-06-29 05:47Z) Loaded `$production-freeze-guard`,
  `$implementation-strategy`, and `$code-change-verification`.
- [x] (2026-06-29 05:47Z) Created branch
  `feat/retry-priority-statushub-failfast`.
- [x] (2026-06-29 05:50Z) Confirmed latest compatibility boundary is tag
  `v1.4.25` and SDK freeze manifests are pinned to `v1.4.25`.
- [x] (2026-06-29 06:20Z) Implemented retry priority escalation for task,
  aggregation, and workflow retry paths.
- [x] (2026-06-29 06:20Z) Implemented `StatusHub` fail-fast startup error
  handling.
- [x] (2026-06-29 06:32Z) Added SDK tests and updated approved freeze
  manifests to `v1.4.26`.
- [x] (2026-06-29 06:32Z) Bumped SDK, Studio backend, and Studio frontend
  versions to `1.4.26`; updated changelog and docs.
- [x] (2026-06-29 07:26Z) Ran `$code-change-verification`; SDK and Studio
  backend format, lint, typecheck, and tests passed.
- [x] (2026-06-29 07:27Z) Ran full real RabbitMQ/Redis smoke suite against
  disposable Docker containers; all scripts passed.
- [x] (2026-06-29 07:27Z) Ran Studio frontend `make -C apps/studio test` and
  `make -C apps/studio build`; both passed.
- [ ] Open PR.

## Surprises & Discoveries

- Observation: `RelaynaRabbitClient` already maps normal task and workflow
  envelope `priority` fields to AMQP message priority and validates against
  configured max-priority, but `publish_raw_to_queue()` has no `priority`
  argument. Retry paths use `publish_raw_to_queue()`, so they cannot currently
  preserve or elevate AMQP priority.
  Evidence: `src/relayna/rabbitmq/client.py`,
  `src/relayna/consumer/task_consumer.py`, and earlier `rg` output.

- Observation: `StatusHub.run_forever()` currently emits `StatusHubLoopError`
  and sleeps two seconds for every non-cancel exception, regardless of whether
  the hub has ever successfully started consuming.
  Evidence: `src/relayna/status/hub.py`.

- Observation: `WorkflowConsumer._effective_retry_policy()` rebuilds
  `RetryPolicy` to apply stage retry overrides, so new `RetryPolicy` fields must
  be explicitly carried through that helper.
  Evidence: focused workflow retry test initially returned `priority is None`
  until `retry_priority_step` was preserved.

## Decision Log

- Decision: Treat the compatibility boundary as latest release tag `v1.4.25`,
  while noting the user's explicit approval to break/update freeze perimeters.
  Rationale: Both requested changes alter public SDK signatures and runtime
  behavior, so freeze manifest updates are intentional review artifacts.
  Date/Author: 2026-06-29 / Codex.

- Decision: Define `StatusHub` startup success as successfully entering the
  queue iterator context, not processing at least one message.
  Rationale: an idle but healthy status queue may process zero messages. The
  operator wants failures to open the queue/iterator surfaced at startup.
  Date/Author: 2026-06-29 / Codex.

- Decision: Keep retry priority additive and opt-in through
  `RetryPolicy.retry_priority_step`.
  Rationale: `None` preserves existing behavior; positive values compute
  `retry_attempt * retry_priority_step` and clamp to AMQP's `0..255` range.
  Date/Author: 2026-06-29 / Codex.

## Outcomes & Retrospective

Runtime implementation, focused behavior tests, docs, version bumps, and freeze
manifest updates are complete. Full SDK/Studio backend verification, frontend
test/build, and real RabbitMQ/Redis smokes passed. PR publication remains.

## Context and Orientation

The SDK lives under `/Users/jobz/Works/relayna/src/relayna`. The changes affect:

- `src/relayna/consumer/context.py`: public `RetryPolicy` dataclass.
- `src/relayna/rabbitmq/client.py`: raw RabbitMQ queue publishing.
- `src/relayna/consumer/task_consumer.py`: task retry publication.
- `src/relayna/consumer/workflow_consumer.py`: workflow retry publication.
- `src/relayna/status/hub.py`: `StatusHub` startup loop behavior.
- `tests/`: SDK behavior and freeze tests.
- `docs/`, `README.md`, `CHANGELOG.md`, `pyproject.toml`: release-facing
  documentation and version metadata.

Relayna's SDK is the public Python runtime package. RabbitMQ task queues may be
declared with `x-max-priority`; RabbitMQ then schedules higher-priority messages
first. Relayna retry queues use TTL and DLX routing: a failed message is
published to a retry queue, waits for the TTL, and is dead-lettered back to the
source queue. RabbitMQ preserves message properties such as AMQP priority during
dead-letter routing.

`StatusHub` consumes the shared status queue and writes normalized events to
Redis via `RedisStatusStore`.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.25`; user explicitly approved
breaking/updating the freeze perimeters to properly implement both requested
issues. The implementation will preserve default behavior where practical:

- `RetryPolicy.retry_priority_step` defaults to `None`, so existing retry
  messages keep no explicit AMQP priority.
- `RelaynaRabbitClient.publish_raw_to_queue(..., priority=None)` keeps existing
  raw publish behavior.
- `StatusHub(..., fail_fast_errors=None)` keeps the existing retry-forever loop.

The SDK public-surface freeze manifest must be updated intentionally for the new
constructor/dataclass signatures and documented as an approved perimeter change.

## Plan of Work

Add `retry_priority_step: int | None = None` to `RetryPolicy`, validate it as a
non-negative integer when configured, and compute a retry message priority in
task and workflow retry publishers. Forward the priority through
`RelaynaRabbitClient.publish_raw_to_queue()` into `aio_pika.Message`, clearing
the default priority when no priority is supplied as the existing publish paths
do.

Add `fail_fast_errors: frozenset[type[Exception]] | set[type[Exception]] |
None = None` to `StatusHub.__init__`. Store it as a tuple of exception classes
for `isinstance`. In `run_forever()`, track whether the queue iterator has been
entered at least once. If a configured fail-fast exception occurs before that
point and stop has not been requested, raise `RuntimeError` from the original
exception. Otherwise preserve existing observation and retry behavior.

Add focused tests for retry priority calculation, clamping, raw publish priority,
default no-op behavior, workflow retry parity, and StatusHub fail-fast/default
retry behavior. Update the SDK public freeze manifest using the existing
production freeze tooling or direct manifest edits backed by tests.

Update version metadata to the next patch release, changelog entries, README and
docs sections that describe retry priority and StatusHub startup behavior.

## Concrete Steps

From `/Users/jobz/Works/relayna`:

    git switch -c feat/retry-priority-statushub-failfast
    make format
    make lint
    make typecheck
    make test
    make -C apps/studio test
    make -C apps/studio build
    bash .codex/skills/code-change-verification/scripts/run.sh

If package metadata changes require lock refresh:

    make sync
    make -C studio/backend sync

For PR publication:

    git status --short
    git add ...
    git commit -m "feat: add retry priority and statushub fail-fast"
    git push -u origin feat/retry-priority-statushub-failfast
    gh pr create --draft ...

## Validation and Acceptance

Behavior is accepted when tests prove:

- Retry priority remains unset when `retry_priority_step` is `None`.
- Retry priority is `retry_attempt * retry_priority_step` when configured.
- Retry priority clamps to `255`.
- Invalid negative `retry_priority_step` is rejected.
- Task, aggregation, and workflow retry publishers forward the priority to raw
  RabbitMQ publish.
- `publish_raw_to_queue()` passes `priority` into `aio_pika.Message` and leaves
  priority unset when omitted.
- `StatusHub` re-raises a `RuntimeError` for configured startup exceptions
  before first successful iterator entry.
- `StatusHub` keeps existing retry-loop behavior when `fail_fast_errors` is
  unset.

Verification is accepted only after the full SDK and Studio backend verification
stack passes, Studio frontend test/build passes, and the real local
RabbitMQ/Redis smoke suite passes.

## Idempotence and Recovery

All edits are plain source changes. Failed test or format commands can be rerun.
If a freeze manifest update is wrong, regenerate or inspect the failing
production-freeze test output and update only the intentionally changed SDK
signature entries. Do not reset unrelated user changes.

## Artifacts and Notes

GitHub issues:

- #103: Retry messages with elevated priority in the task queue.
- #102: StatusHub fail-fast on startup queue errors.

## Interfaces and Dependencies

Final public interfaces:

- `RetryPolicy(max_retries=3, delay_ms=30000, retry_queue_suffix=".retry",
  dead_letter_queue_suffix=".dlq", retry_priority_step: int | None = None)`.
- `RelaynaRabbitClient.publish_raw_to_queue(..., priority: int | None = None)`.
- `StatusHub(..., fail_fast_errors: frozenset[type[Exception]] | None = None)`.

No new third-party runtime dependency is expected.
