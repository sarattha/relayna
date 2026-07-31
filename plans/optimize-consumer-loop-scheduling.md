# Optimize Consumer-Loop Scheduling

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Reduce repeatable CPU and allocation overhead between RabbitMQ delivery and
Relayna's real `TaskConsumer` message-processing operation while preserving the
released consumer lifecycle exactly. Operators should see higher sustained
consumer-loop throughput at the same QoS/prefetch and application work, without
changes to handler invocation, acknowledgement, rejection, retry, status,
workflow, tracing, metrics, observation, context propagation, cancellation,
fairness, or shutdown semantics.

The outcome will be observable in deterministic scheduling and lifecycle tests
and in an immutable matched baseline/candidate run of every registered
benchmark under the repository's disabled, enabled-unsampled, and
enabled-sampled-exported tracing configurations. The retained comparison will
include every benchmark cell exactly once and will distinguish the consumer's
per-message CPU path from full consumer-loop throughput.

## Progress

- [x] (2026-07-31 08:11Z) Read `AGENTS.md`, `PLANS.md`, and the complete
  `implementation-strategy`, `production-freeze-guard`,
  `code-change-verification`, and `pr-draft-summary` skill instructions.
- [x] (2026-07-31 08:11Z) Confirmed the supplied worktree was clean, fetched
  `origin/main`, removed an accidentally created extra clean worktree after the
  user's correction, and switched this worktree to
  `codex/optimize-consumer-loop-scheduling` at exact base
  `1459da95ddcbb2819de87eefc991711a51c24338`.
- [x] (2026-07-31 08:11Z) Confirmed performance items 1–3 are merged as PRs
  #116–#118 and inspected their touched consumer, metadata, tracing, benchmark,
  report, version, and documentation surfaces.
- [x] (2026-07-31 08:14Z) Enumerated and validated the five-benchmark canonical
  registry, three-mode runner, 1,224-case expectation, environment, commands,
  and existing 16-case per-message plus 24-case consumer-loop coverage; 38
  focused benchmark/harness tests pass.
- [x] (2026-07-31 08:18Z) Generalized the retained-suite tooling before
  baseline so this task records its own identity, `_async.py` runtime hash
  inventory, exact dependency-sync command, and event-loop implementation and
  policy; formatting/lint and 11 artifact/suite tests pass.
- [x] (2026-07-31 08:18Z) Captured and independently validated definitive
  untouched-runtime baseline `20260731T081626Z-1459da95`: 1,224 unique
  measurements, 15 standalone HTML reports, 15 raw JSON reports, 35 checksummed
  artifacts, three tracing modes, and exact case/prefetch/cardinality counts.
- [x] (2026-07-31 08:31Z) Profiled the actual delivery-to-handler path over
  8,192 real 1 KB `TaskConsumer` deliveries and compared direct-task,
  capacity-event, cached-state, pre-bound-handler, and batched-wait prototypes
  in memory before any runtime edit.
- [x] (2026-07-31 08:34Z) Implemented the measured private capacity-counter
  scheduler using only public loop futures/tasks and added deterministic
  high-cardinality cleanup, concurrency, fairness, ContextVar, cancellation,
  loop-resolution, and unobserved-exception coverage; 166 focused async,
  consumer, status, lease, tracing, workflow, and benchmark tests pass.
- [x] (2026-07-31 08:34Z) Rejected five non-improving exploratory designs and
  retained only the capacity-counter prototype, which improved every tested
  real concurrent cell by 1.8–2.8% across 15 alternating 8,192-message trials
  with exact behavior counts.
- [ ] Re-fetch `origin/main`, integrate any relevant merged work, and capture a
  fresh final matched baseline/candidate pair if the base or environment moved.
- [ ] Generate and validate complete-suite JSON/HTML comparison evidence,
  update the next available patch version, changelog, benchmark/runtime docs,
  and freeze/version notes.
- [ ] Run the mandatory verification stack, applicable RabbitMQ integration
  coverage, artifact integrity checks, and a final diff/secret/path/semantic
  audit.
- [ ] Commit only intentional files, push the requested branch, open a ready
  PR, monitor all CI, request the first Codex review, and resolve every
  actionable review finding.

## Surprises & Discoveries

- Observation: all three predecessor performance items are already on
  `origin/main`; item 3 is the branch head after tag `v1.4.31`.
  Evidence: history contains PR #116 at `44adab85`, PR #117 at `283782e4`
  (tagged `v1.4.31`), and PR #118 at `1459da95`.

- Observation: the consumer-loop scheduler is the shared private
  `relayna._async.run_bounded_iterator`, not logic embedded directly in
  `TaskConsumer`. It currently uses an `asyncio.Semaphore`, one wrapper
  coroutine and task per delivery, a mutable in-flight set, a done callback
  closure, callback-time exception retrieval, and a final gather.
  Evidence: `src/relayna/_async.py` and the call from
  `TaskConsumer.run_forever()` in `src/relayna/consumer/task_consumer.py`.

- Observation: the existing canonical consumer benchmark already exercises the
  public `TaskConsumer.run_forever()` path after broker delivery with a no-op
  handler that yields once. Its loop matrix covers both instrumentation
  profiles, four body sizes, and prefetch 1, 8, and 32, alongside a distinct
  real `_handle_message()` per-message CPU matrix.
  Evidence: `benchmarks/consumer_processing.py` and
  `tests/test_consumer_processing_benchmark.py`.

- Observation: the `benchmark` dependency extra intentionally lacks pytest;
  harness tests require the additional repository `dev` extra.
  Evidence: the first validation command could enumerate benchmarks but failed
  to spawn pytest. `uv sync --extra benchmark --extra dev --frozen` changed no
  tracked dependency files, and the same 38-test command then passed.

- Observation: item 3's otherwise complete retention manifest hard-coded the
  preceding task name, omitted `src/relayna/_async.py` from runtime provenance,
  and did not record event-loop implementation/policy.
  Evidence: `scripts/retain_tracing_benchmark_run.py` before the tooling-only
  generalization. Legacy defaults remain intact, while this task can supply its
  own task, runtime-path inventory, and sync command.

- Observation: the validated untouched-runtime baseline contains all expected
  matrices once and records CPython 3.13.2, uv 0.11.26, Apple M1 Pro,
  `asyncio.unix_events._UnixSelectorEventLoop`, the default Unix event-loop
  policy, exact lock digests, three tracing configurations, and 504,104 sampled
  exported spans.
  Evidence:
  `reports/optimize-consumer-loop-scheduling/20260731T081626Z-1459da95/baseline/manifest.json`,
  its `checksums.sha256`, and independent checksum/HTML/raw-matrix validation.

- Observation: at 1 KB the public consumer loop adds 32–45% over the same real
  per-message operation at prefetch 32, and 55–123% at lower prefetch depending
  on tracing/observation configuration.
  Evidence: unrounded baseline `consumer-processing.raw.json` data across
  disabled, enabled-unsampled, and enabled-sampled-exported modes.

- Observation: an 8,192-delivery cProfile run at prefetch 32 records exactly
  8,192 scheduled tasks, wrapper coroutines, completion callbacks, and
  exception retrievals plus 8,193 semaphore acquire/release pairs (the extra
  pair handles iterator exhaustion). Handler/ack counts remain 8,192/8,192,
  peak concurrency is 32, and no rejects occur.
  Evidence: exploratory in-memory instrumentation of
  `benchmarks.consumer_processing._run_loop_sample`; the run is profiling
  evidence, not final benchmark data.

- Observation: removing the wrapper and releasing capacity from completion
  callbacks regressed the tested real loop by roughly 0–8%; replacing the
  semaphore with an event regressed roughly 1–6%; pre-binding the real handler
  ranged from a 0.7% improvement to a 2.7% regression; batched
  `asyncio.wait(FIRST_COMPLETED)` ranged from a 2.8% improvement to an 8.4%
  regression with higher variance.
  Evidence: separate alternating-order exploratory runs of 4,096 or 8,192 real
  1 KB deliveries across minimal/observable profiles and prefetch 8/32. These
  rejected runs are excluded from release claims.

- Observation: resolving the running loop once and using its bound
  `create_task` method was the only consistently plausible micro-optimization,
  but isolated gains were small and noisy (about 0–2%). Repeated stable-method
  binding sometimes helped and sometimes regressed.
  Evidence: nine- and fifteen-repeat exploratory comparisons. This supports a
  deliberately narrow source trial followed by the canonical matched suite,
  not a throughput claim from exploratory data.

- Observation: a specialized integer capacity counter plus one public
  loop-created waiter only when capacity reaches zero improves the real
  concurrent loop consistently while retaining the existing wrapper release
  point.
  Evidence: across 15 alternating 8,192-message samples, minimal prefetch 8/32
  improved 2.83%/2.21% and observation-enabled prefetch 8/32 improved
  2.39%/1.83%. Every sample preserved exact handler/ack counts, zero rejects,
  and peak concurrency equal to prefetch.

## Decision Log

- Decision: use exact refreshed base
  `1459da95ddcbb2819de87eefc991711a51c24338` and keep the optimization
  independent of deleted or still-present predecessor worktrees.
  Rationale: this is both the supplied worktree's requested base and current
  `origin/main`; it already contains items 1–3, so this PR must build on rather
  than duplicate or revert them.
  Date/Author: 2026-07-31 / Codex.

- Decision: compare compatibility against latest release tag `v1.4.31` while
  honoring the repository's strict `v1.4.30` freeze perimeter.
  Rationale: the desired implementation is a private internal scheduling
  refactor. It must preserve released public imports and signatures, RabbitMQ
  QoS/ack/reject/requeue behavior, task/status/workflow and tracing contracts,
  configuration, persistence, routes, and wire formats. The user's explicit
  freeze authorization is recorded, but freeze manifests will change only if a
  genuine perimeter change becomes necessary.
  Date/Author: 2026-07-31 / Codex.

- Decision: use the repository's three-mode tracing benchmark suite as the
  definitive complete-suite runner rather than a tracing-disabled `run-all`
  alone.
  Rationale: it invokes all registered benchmarks with canonical defaults in
  isolated processes and preserves the instrumentation matrix established by
  item 3.
  Date/Author: 2026-07-31 / Codex.

- Decision: do not change production runtime code before the definitive
  baseline and scheduling profile are captured.
  Rationale: the task requires evidence for repeatable scheduling overhead and
  an untouched-runtime baseline. Any benchmark-only enhancement found necessary
  will be isolated and committed before that baseline so candidate and baseline
  use exactly the same harness.
  Date/Author: 2026-07-31 / Codex.

- Decision: preserve the current canonical consumer benchmark shape rather than
  add a scheduling-only production bypass.
  Rationale: the 1 KB loop cells each process 1,024 real deliveries per sample
  at prefetch 1, 8, and 32, enough to expose task scheduling and cleanup while
  the paired real per-message matrix provides the same processing-path control.
  Larger bodies, both instrumentation profiles, three repeats, and three
  tracing modes retain realistic variance and regression coverage without
  changing work between baseline and candidate.
  Date/Author: 2026-07-31 / Codex.

- Decision: reject direct-task, capacity-event, pre-bound-handler, and
  batched-wait scheduler rewrites.
  Rationale: none produced a repeatable improvement, and each would alter more
  task/capacity/error timing than the existing semaphore architecture. The task
  explicitly forbids retaining a clever but regressive or semantically weaker
  path.
  Date/Author: 2026-07-31 / Codex.

- Decision: retain the specialized capacity counter and reject the smaller
  scheduling-state-only trial.
  Rationale: the state-only trial measured essentially zero aggregate movement
  (+0.08% across six nearby pairs). The retained design removes all measured
  per-delivery semaphore coroutine/method operations while preserving the
  release point inside the handler wrapper. `available_capacity` is decremented
  before intake and incremented exactly once in `finally`; a single public
  future wakes intake only when all capacity was occupied. Per-message
  `loop.create_task` still honors custom task factories and copies ContextVars;
  in-flight set/callback exception retrieval, stop timing, cancellation, and
  final drain remain in place.
  Date/Author: 2026-07-31 / Codex.

## Outcomes & Retrospective

Work is in progress. This section will record the retained implementation,
quantified complete-suite and consumer-loop outcome, compatibility impact,
verification/CI/review status, and remaining risks.

## Context and Orientation

Relayna's SDK lives under `src/relayna/`. `TaskConsumer` in
`src/relayna/consumer/task_consumer.py` acquires a channel at the configured
prefetch, opens the RabbitMQ queue iterator, and passes each already-delivered
message to the private `run_bounded_iterator` helper in
`src/relayna/_async.py`. Prefetch is both RabbitMQ's delivery credit and
Relayna's maximum concurrently scheduled handler work. The scheduler must
retain tasks until completion, retrieve failures, drain or cancel them during
shutdown, and allow the event loop to run I/O, timers, heartbeat, cancellation,
and unrelated coroutines.

The real per-message operation is `TaskConsumer._handle_message()`. It parses
the envelope, constructs message metadata and task context, enters tracing,
applies lease/idempotency/retry/lifecycle logic, invokes the handler once, emits
configured status/metrics/observation output, and only then acknowledges or
rejects the delivered message according to the existing failure policy. PRs
#116–#118 already eliminate disabled instrumentation work, extract message
metadata once, and reduce tracing overhead. Their optimized branches must not be
reintroduced as alternate implementations.

Repository performance tooling lives under `benchmarks/`. The registry in
`benchmarks/registry.py` currently exposes `consumer-processing`,
`envelope-serialization`, `json-engine-evaluation`, `publish-preparation`, and
`redis-storage-cpu`. `benchmarks/tracing_suite.py` runs all five under disabled,
enabled-unsampled, and enabled-sampled-exported OpenTelemetry configurations.
`scripts/retain_tracing_benchmark_run.py` turns a completed suite into
standalone HTML, machine-readable raw JSON, environment/source metadata,
checksums, and uniqueness validation. Derived comparison tooling starts from
`scripts/compare_tracing_benchmarks.py`.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.31`; strict production freeze
perimeter `v1.4.30`; current default-branch commit `1459da95`.

The intended change is private and behavior-preserving. No public SDK import,
constructor or method signature, external configuration, persisted schema,
serialized task/status/workflow shape, RabbitMQ exchange/queue/routing/QoS
contract, acknowledgement timing contract, Studio API/type, route response, or
wire representation may change. Existing ordering guarantees, maximum in-flight
work, context isolation, tracing identity, one handler execution per accepted
delivery, and error classification remain the acceptance boundary.

The user explicitly authorized a narrow production-perimeter change if
genuinely required, but an internal solution is preferred. Freeze manifests
must never be edited merely to silence tests; any intentional change must be
documented here and in the PR.

## Plan of Work

First, enumerate the benchmark registry and run its validation tests. Confirm
that the consumer benchmark reports both the real per-message CPU path and
public consumer-loop path, with enough 1 KB messages and prefetch variation to
expose scheduler costs without broker or network noise. If profiling shows the
current matrix cannot isolate scheduling overhead, add only benchmark/tooling
configuration, verify and commit it, then run the untouched-runtime baseline.

Capture the canonical three-mode, all-benchmark baseline into a unique run
directory. Retain the raw embedded data and standalone HTML through the
repository retention tool, then independently validate expected/actual
benchmark counts, qualified case uniqueness, checksums, HTML completeness,
source state, dependency lock digest, Python/uv/package versions, OS,
architecture, CPU, event-loop implementation/policy, tracing controls,
warmups/repetitions, message cardinalities, and prefetch values.

Before production edits, profile the actual `TaskConsumer.run_forever()` path
using deterministic no-network messages. Measure and trace callback-to-task
handoff, context copying, task and callback allocation, semaphore operations,
set bookkeeping, exception retrieval, queue iteration, cleanup/drain, redundant
lookups/checkpoints, and retained references. Compare small private prototypes
only where profiling shows material repeatable cost.

Implement the narrowest stable-Python solution in `src/relayna/_async.py` and,
only if necessary, its call site. Do not use private asyncio or aio-pika
internals. Add focused tests under `tests/` for concurrency bounds, event-loop
fairness, high-cardinality cleanup, success/error/timeout/malformed/retry and
ack/reject paths through `TaskConsumer`, cancellation before and during work,
capacity release, deterministic drain, channel/connection failure,
ContextVar/tracing isolation, exception retrieval, and absence of retained
completed tasks or message references.

After focused correctness tests pass, run exploratory benchmark candidates in
uniquely labeled non-authoritative directories. Keep only a reproducible
improvement with no material regression. Re-fetch `origin/main` before final
measurements and versioning. If source base, lockfile, dependencies, event-loop
configuration, or environment changed, integrate semantically and capture a
fresh matched baseline/candidate pair.

Run every benchmark with identical controls for the final candidate. Retain it
beside the final baseline and generate checksum-bound JSON and standalone HTML
comparison artifacts covering every case once. Report absolute and percentage
latency/throughput deltas, variance, message counts, per-message cost,
consumer-loop results by prefetch/body size/profile, tracing controls,
unchanged-suite drift, and a conservative merge-worth assessment.

If the measured implementation is retained, select the next unused patch after
the final fetch and update every repository-authoritative SDK, Studio,
frontend, lock, generated, changelog, release, and freeze-version reference
required by convention. Document reproducible commands, reports, limitations,
operational implications, and compatibility.

Finish with `code-change-verification`, frontend/version checks if those
surfaces change, applicable RabbitMQ smoke/integration coverage, artifact
regeneration/integrity and standalone-HTML validation, then audit the complete
diff. Use `pr-draft-summary`, commit intentional files only, push
`codex/optimize-consumer-loop-scheduling`, open a ready PR, wait for all CI,
request the first Codex review, and resolve all actionable findings without
merging.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/2247/relayna`:

    git status --short --branch
    git fetch origin main --tags --prune
    make benchmark-list
    uv run --extra benchmark pytest -q \
      tests/test_benchmark_cli.py \
      tests/test_consumer_processing_benchmark.py \
      tests/test_tracing_benchmark_suite.py \
      tests/test_tracing_benchmark_artifacts.py

Capture one canonical suite in a new, never-reused temporary directory:

    env PYTHONHASHSEED=0 LC_ALL=C LANG=C TZ=UTC \
      uv run --extra benchmark python -m benchmarks.tracing_suite \
      --output-root <unique-suite-dir>

Retain it with:

    uv run --extra benchmark python scripts/retain_tracing_benchmark_run.py \
      --source-root . \
      --suite-dir <unique-suite-dir> \
      --output-dir reports/optimize-consumer-loop-scheduling/<run-id>/baseline \
      --run-id <run-id> \
      --run-kind baseline \
      --branch-under-test codex/optimize-consumer-loop-scheduling \
      --runtime-base-commit 1459da95ddcbb2819de87eefc991711a51c24338 \
      --started-at <UTC timestamp> \
      --finished-at <UTC timestamp> \
      --source-clean-before-run

Focused test and profile commands will be recorded here once the measured
scheduler boundary is selected. Final SDK verification is:

    bash .codex/skills/code-change-verification/scripts/run.sh

## Validation and Acceptance

Acceptance requires all of the following:

- The exact fetched base and final branch/source states are recorded and no
  unrelated earlier report, plan, generated file, or user change is included.
- Baseline and candidate each contain all five registered benchmark families
  under all three tracing modes, with the expected case count and each qualified
  case ID exactly once.
- Both consumer measurement families invoke the same real
  `_handle_message()` operation exactly once per delivery; per-message CPU
  outputs and counts remain equivalent while consumer-loop scheduling shows a
  repeatable improvement above matched control drift.
- Prefetch remains the hard maximum in-flight bound. Every accepted delivery
  invokes one handler and receives exactly one terminal acknowledgement or
  rejection decision at the same lifecycle point.
- Timers, I/O callbacks, cancellation, shutdown, status work, other consumers,
  and unrelated coroutines remain responsive under sustained traffic.
- Success, malformed input, handler failure, retry/requeue, ack/reject failure,
  channel loss, rapid stop/start, and partial startup failure leave no leaked
  tasks, semaphore capacity, message references, double decisions, swallowed
  cancellation, or unobserved exceptions.
- ContextVars and trace/task/correlation metadata remain isolated across
  concurrent messages and all instrumentation configurations produce equivalent
  counts and identities.
- The complete mandatory verification stack passes from the beginning after
  the final fix, retained HTML is standalone and complete, checksums validate,
  and the final PR reaches terminal green CI with no unresolved actionable
  findings from the first Codex review.

## Idempotence and Recovery

Every benchmark suite and retained directory uses a unique timestamp/base
identifier, and the runner/retention tools refuse to overwrite existing output.
Interrupted or drifted runs stay labeled exploratory or invalid and are never
promoted by editing data. Retry by choosing a new run identifier and capturing
both sides again under matching conditions.

Git integration uses a clean status check before fetch/rebase. Unrelated user
work is never reset, reformatted, or committed. If `origin/main` changes, rebase
only this branch's intentional commits, resolve consumer/tracing/version
conflicts semantically, rerun verification, and generate a new matched pair.
Runtime prototypes remain private and are removed or committed as clearly
reviewable alternatives before finalization.

## Artifacts and Notes

The authoritative run root will be:

    reports/optimize-consumer-loop-scheduling/<timestamp>-<base-sha>/

It will contain separate `baseline/`, `candidate/`, and `comparison/`
directories. Preliminary profiles and exploratory candidate runs use unique
labels and are excluded explicitly from final claims.

## Interfaces and Dependencies

The final implementation must use only supported Python 3.13+ `asyncio`
semantics and the public async-iterator contract already consumed by
`run_bounded_iterator`. Its existing signature remains:

    async def run_bounded_iterator(
        iterator: Any,
        *,
        concurrency: int,
        handler: Callable[[Any], Awaitable[None]],
        stop_event: asyncio.Event | None = None,
    ) -> None

`TaskConsumer` remains publicly unchanged. The benchmark registry, five
benchmark names, real consumer operation, three tracing modes, output schemas,
and no-op application boundary remain stable across the matched pair unless a
benchmark-only enhancement is deliberately committed before both sides.
