# Consumer Processing Benchmark

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Add a repository benchmark named `consumer-processing` that measures Relayna's
real inbound `TaskConsumer` work after RabbitMQ has already delivered a message.
Users will be able to select `per-message`, `consumer-loop`, or `all` from the
existing benchmark CLI. The first measurement times the complete
`TaskConsumer._handle_message()` path. The second times the public
`TaskConsumer.run_forever()` loop over a deterministic preloaded AMQP fake,
including iterator traversal and bounded async dispatch. Both exclude broker,
network, and application business logic.

A canonical run writes a self-contained report to
`reports/consumer-processing.html`. The report includes separate measurement
sections, exact payload bytes, configuration and optional-feature state,
environment/package metadata, methodology, counts, concurrency evidence, and
bottleneck conclusions.

## Progress

- [x] (2026-07-30 15:22Z) Created and checked out
  `codex/consumer-processing-benchmark` at `e00babf`; confirmed a clean
  worktree.
- [x] (2026-07-30 15:22Z) Read `AGENTS.md`, `PLANS.md`, the benchmark
  registry/CLI/reporting code, the existing exact-size benchmark pattern, and
  the relevant `TaskConsumer` paths.
- [x] (2026-07-30 15:32Z) Implemented deterministic exact-size fixtures, narrow AMQP/client fakes,
  measurement matrices, timing runners, metrics, and HTML report rendering.
- [x] (2026-07-30 15:32Z) Registered the benchmark and documented all CLI modes without changing
  existing benchmark commands.
- [x] (2026-07-30 15:32Z) Added deterministic focused tests for registry and CLI behavior, exact
  sizes, complete matrices, counts, loop termination and concurrency, metrics,
  HTML generation, and `run-all`.
- [x] (2026-07-30 15:41Z) Ran focused checks and all three benchmark modes,
  inspected the generated stable report, and recorded measured conclusions.
- [x] (2026-07-30 15:41Z) Ran SDK format, lint, typecheck, test, benchmark smoke/run-all checks, and
  the mandatory `$code-change-verification` stack.
- [x] (2026-07-30 15:41Z) Completed this plan's outcomes and prepared the
  `$pr-draft-summary` handoff.

## Surprises & Discoveries

- Observation: `TaskConsumer` has one bound `self._handler`, and its successful
  path directly parses transport JSON, normalizes aliases, validates a
  `TaskEnvelope`, builds `TaskContext`, invokes the handler, applies the ack
  decision, and emits observations.
  Evidence: `src/relayna/consumer/task_consumer.py`, especially
  `_handle_message()`, `_handle_message_impl()`, `_make_task_context()`, and
  `_process_task()`.

- Observation: bounded concurrent loop dispatch is already centralized in
  `relayna._async.run_bounded_iterator`; the benchmark can exercise it through
  public `run_forever()` without copying loop logic.
  Evidence: `TaskConsumer._run_concurrent_iterator()` calls
  `run_bounded_iterator()` with `concurrency=prefetch`.

- Observation: a 4-message 1 MB loop sample made fixed public-loop startup work
  too visible at prefetch 1 even though fixture construction was outside timing.
  Evidence: the first canonical report used 4, 8, and 32 messages for prefetch
  1, 8, and 32 respectively.

- Observation: the mandatory verification script initially stopped at Studio
  formatting because the fresh Studio virtual environment did not contain its
  declared development tools.
  Evidence: `make -C studio/backend format` could not spawn `ruff`; after
  `make -C studio/backend sync`, the full script passed from the beginning.

- Observation: for the no-op yielding loop, prefetch 32 achieved its configured
  concurrency in every canonical cell and reduced per-message loop time versus
  prefetch 1 on this machine.
  Evidence: final minimal-profile rows report peak concurrency 1, 8, and 32;
  1 KB loop cost moved from 47.757 to 37.648 microseconds/message, while 1 MB
  moved from 149.896 to 125.099 microseconds/message.

## Decision Log

- Decision: keep all executable changes outside `src/relayna` and do not update
  freeze manifests.
  Rationale: this task measures existing behavior only; runtime optimizations
  and public/runtime changes are explicitly out of scope.
  Date/Author: 2026-07-30 / Codex.

- Decision: use canonical and configured-alias inputs for both per-message
  profiles, but bound the loop matrix to canonical input because loop dispatch
  and prefetch conclusions do not depend on repeating alias normalization at
  every concurrency level.
  Rationale: this preserves meaningful alias coverage while controlling
  canonical runtime and report size. The exclusion will be explicit in the
  report.
  Date/Author: 2026-07-30 / Codex.

- Decision: use a deterministic async in-memory observation sink for the
  observability-enabled profile; disable lifecycle-status publication, retry,
  DLQ, leases, Redis, RabbitMQ networking, and handler work in canonical
  timing.
  Rationale: observation event construction and sink invocation are real
  optional consumer work and require no external service; the other features
  measure separate I/O or failure/lifecycle concerns.
  Date/Author: 2026-07-30 / Codex.

- Decision: use canonical loop message cardinalities of 1,024, 256, 64, and 32
  as bodies grow from 1 KB to 1 MB.
  Rationale: this keeps per-sample data bounded at 1, 4, 8, and 32 MiB while
  ensuring every prefetch value has enough steady-state work for one-time fake
  loop setup not to dominate.
  Date/Author: 2026-07-30 / Codex.

- Decision: warm every matrix cell first, then rotate and reverse case order
  across repeats.
  Rationale: this follows the existing publish benchmark's variance-control
  pattern and reduces systematic first/last-case bias without altering the
  measured operations.
  Date/Author: 2026-07-30 / Codex.

## Outcomes & Retrospective

The repository now exposes `consumer-processing` through the existing CLI with
`per-message`, `consumer-loop`, and default `all` selections. The final stable
`reports/consumer-processing.html` contains 16 real `_handle_message()` cells
and 24 public `run_forever()` cells. Every exact-size fixture, handler count,
ack count, zero-reject invariant, finite-loop termination, and achieved
prefetch concurrency is asserted by deterministic tests.

On the final machine/run, the minimal canonical per-message path measured
26.206 microseconds at 1 KB, 27.270 at 16 KB, 38.968 at 128 KB, and 116.250 at
1 MB. Minimal loop results ranged from 47.757 to 37.648
microseconds/message at 1 KB as prefetch increased from 1 to 32, and from
149.896 to 125.099 at 1 MB. These results suggest constant tracing/context/
resource/event and loop scheduling costs matter most for small bodies, while
transport parsing, normalization, Pydantic validation, and memory movement
become the likely large-body targets. Those are candidates for a separate
runtime decision, not changes made by this benchmark.

Focused benchmark/CLI tests passed (28). Existing benchmark smoke tests passed
(144). The SDK suite passed with 654 tests and 1 environment-dependent skip
after installing the optional benchmark engine. The Studio backend suite passed
244 tests. The full `$code-change-verification` script passed, and isolated
canonical `run-all` completed all five registered benchmark types. No runtime
file, freeze manifest, staged change, commit, push, or pull request was created.

## Context and Orientation

Relayna's SDK lives under `src/relayna/`. `TaskConsumer` in
`src/relayna/consumer/task_consumer.py` processes an already-delivered AMQP
message. An AMQP fake in this benchmark must expose only attributes read by
Relayna (`body`, identifiers, headers, content type, delivery tag, redelivery
state) plus async `ack()` and `reject()`.

The repository benchmark framework lives under `benchmarks/`.
`benchmarks/registry.py` registers benchmark definitions,
`benchmarks/cli.py` exposes `list`, `run`, and `run-all`, and
`benchmarks/reporting.py` collects environment metadata and atomically writes
artifacts. Existing exact-size fixture and report patterns live in
`benchmarks/publish_preparation.py`.

Focused benchmark tests live under `tests/`. The new tests will exercise the
benchmark's public helpers and CLI dispatch deterministically without
performance thresholds.

## Compatibility Boundary

Compatibility boundary: benchmark/tooling and test code only. No released SDK
public import, exported signature, external configuration, persisted schema,
wire protocol, task/status/workflow contract, route response, Studio behavior,
or frozen production manifest changes. The benchmark consumes the current
runtime exactly as shipped on this branch. `$implementation-strategy` and
`$production-freeze-guard` are therefore not required unless the implementation
unexpectedly needs a runtime edit; if that happens, stop before editing runtime
code and explain the blocker.

## Plan of Work

Create `benchmarks/consumer_processing.py`. Define exact byte targets of 1 KB,
16 KB, 128 KB, and 1 MB, deterministic canonical and configured-alias bodies,
minimal and observation-enabled profiles, and bounded canonical iteration and
loop-message counts.

Implement a narrow delivered-message fake whose acknowledgements, rejections,
and completion are counted, plus queue iterator, queue, channel, and Rabbit
client fakes supporting only what `TaskConsumer` reads. Build prepared
per-message operations that reuse one event loop and reset mutable counters
outside timed regions. Build loop samples whose iterator deterministically
ends and calls `consumer.stop()` only after all preloaded messages have been
yielded, allowing `run_forever()` to drain in-flight work and return without
timeout polling or sleeps.

For per-message timing, call the real `_handle_message()` once per operation.
Validate untimed probes and every timed aggregate: one handler call and one ack
per successful message, with zero rejects. Compute per-repeat nanoseconds per
message, median, median absolute deviation, messages per second, and MiB per
second.

For loop timing, perform an untimed warm-up using separately prepared fakes,
then time steady-state `run_forever()` samples for prefetch values 1, 8, and 32.
Track exact message cardinality and total bytes, total duration, per-message
cost, messages per second, MiB per second, achieved peak handler concurrency,
acks, rejects, and handler calls. The no-op handler will yield once only in
loop mode so scheduled tasks can overlap and the achieved concurrency proves
bounded dispatch.

Render both result families into one self-contained HTML report with embedded
CSS and no external assets. Include methodology, configuration/matrix,
environment and package metadata, exclusions, exact counts, optional feature
states, and evidence-backed bottleneck conclusions.

Register the definition in `benchmarks/registry.py`, document invocation in
`benchmarks/README.md`, and add `tests/test_consumer_processing_benchmark.py`
plus registry/CLI expectation updates. Preserve all existing commands and make
`--measurement all` the default used by direct and `run-all` invocation.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/b801/relayna`:

    uv run pytest -q tests/test_consumer_processing_benchmark.py tests/test_benchmark_cli.py
    make format
    make lint
    make typecheck
    make test
    uv run python -m benchmarks run consumer-processing --measurement per-message
    uv run python -m benchmarks run consumer-processing --measurement consumer-loop
    uv run python -m benchmarks run consumer-processing --measurement all
    uv run pytest -q tests/test_envelope_microbenchmarks.py tests/test_publish_preparation_benchmark.py tests/test_redis_storage_cpu_microbenchmarks.py
    uv run python -m benchmarks run-all
    bash .codex/skills/code-change-verification/scripts/run.sh

Quick iteration will override repeats, iterations, loop messages, and output so
the same paths are exercised with bounded cost before canonical runs.

## Validation and Acceptance

`uv run python -m benchmarks list` shows `consumer-processing` and its stable
report path. `--help` documents
`--measurement {per-message,consumer-loop,all}`. Omitting the option runs both
families, and `run-all` continues to dispatch every registered benchmark.

Every prepared body has length exactly 1,024, 16,384, 131,072, or 1,048,576
bytes. Each successful per-message operation calls the handler once, acks once,
and never rejects. Each loop sample terminates after its fixed preloaded
cardinality, reports exact total bytes, and has handler and ack counts equal to
message count with zero rejects. Peak concurrency is one at prefetch one and
does not exceed prefetch at 8 or 32; deterministic tests prove representative
concurrency is achieved.

The stable HTML report contains distinct per-message and consumer-loop
sections, methodology, environment/package metadata, matrix/configuration,
optional feature state, exact counts, and bottleneck conclusions. Tests check
metric formulas and artifact generation, not machine-specific performance
thresholds.

All focused and repository checks listed above pass. No file is staged,
committed, pushed, or used to open a pull request.

## Idempotence and Recovery

Fixture creation and benchmark runs are deterministic and safe to repeat.
Report writes are atomic through `write_text_artifact()`. Failed runs do not
publish partial reports. The fakes have no external connections or persisted
state. If a validation command formats files, inspect `git diff` and rerun the
same validation stack. Preserve unrelated worktree changes if any appear.

## Artifacts and Notes

Expected stable artifact:

    reports/consumer-processing.html

Expected direct commands:

    uv run python -m benchmarks run consumer-processing --measurement per-message
    uv run python -m benchmarks run consumer-processing --measurement consumer-loop
    uv run python -m benchmarks run consumer-processing --measurement all

## Interfaces and Dependencies

`benchmarks.consumer_processing` will export `BENCHMARK`, exact-size constants,
case/fixture/result dataclasses, matrix and fixture builders, measurement
runners, metric constructors, and HTML writer helpers used by focused tests.
It will depend only on installed Relayna benchmark/runtime dependencies and
Python's standard library. `BENCHMARK` will use name `consumer-processing`,
default output `reports/consumer-processing.html`, and canonical measurement
default `all`.
