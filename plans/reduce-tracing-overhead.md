# Reduce Tracing Overhead Without Disabling Tracing

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Reduce Relayna's CPU and allocation overhead in its real RabbitMQ producer and
consumer tracing paths while configured OpenTelemetry tracing remains fully
enabled. Applications must observe the same spans, propagation, sampling,
export, errors, status, task metadata, and RabbitMQ behavior as before.

The outcome will be observable in focused behavioral and call-count tests plus
an immutable before/after run of all registered benchmark suites. The retained
comparison will explicitly distinguish tracing disabled, tracing enabled with
an unsampled SDK, and tracing enabled with sampled spans delivered to an
in-memory exporter. It will include consumer per-message and public-loop
results, relevant publish preparation results, all unchanged control suites,
complete case tables, variance data, and a merge-worth assessment grounded in
the matched evidence.

## Progress

- [x] (2026-07-31 07:12Z) Inspected the clean detached worktree, read
  `AGENTS.md`, `PLANS.md`, and all required repository skills, fetched and
  pruned `origin`, and created `codex/reduce-tracing-overhead` from exact clean
  `origin/main`/`main` base `283782ec95955f50e187e5fde82d12f03691834a`.
- [x] (2026-07-31 07:12Z) Confirmed the two preceding performance changes are
  already merged as PR #116 and PR #117 and that the exact base is tagged
  `v1.4.31`.
- [x] (2026-07-31 07:12Z) Enumerated the canonical five-suite benchmark
  registry and determined that its current 408-case shape uses only the
  OpenTelemetry API no-op provider rather than separately measuring configured
  unsampled and sampled/exported SDK operation.
- [x] (2026-07-31 07:24Z) Added and validated a benchmark-only three-process
  tracing configuration dimension without changing production runtime code;
  the final inventory is 1,224 qualified cases across three tracing modes,
  five suites, and 15 standalone reports.
- [ ] Capture the definitive complete-suite pre-runtime-change baseline with
  immutable HTML, raw JSON, provenance, checksums, and uniqueness proof.
- [ ] Profile the actual producer and consumer tracing paths and record
  repeatable evidence for every retained optimization.
- [ ] Implement the narrow behavior-preserving internal optimization and
  focused tracing equivalence/call-count tests.
- [ ] Run exploratory focused benchmarks, retain only reproducible changes, and
  refresh `origin/main` before the final matched pair.
- [ ] Generate and validate complete immutable candidate and comparison
  artifacts against a freshly matched final-base baseline if required.
- [ ] Select the next unused patch version and update every authoritative
  version, changelog, freeze, benchmark, release, tracing, and performance
  documentation surface required by repository convention.
- [ ] Run `$code-change-verification` from the beginning to a clean pass plus
  frontend, package, benchmark, artifact, HTML, and final-diff validation.
- [ ] Use `$pr-draft-summary`, intentionally stage/commit/push, open a ready PR,
  babysit CI, request the first Codex review, and resolve all actionable
  feedback without merging.

## Surprises & Discoveries

- Observation: the worktree's starting commit and freshly fetched
  `origin/main` are identical at `283782ec`, and that commit is tag `v1.4.31`.
  Evidence: `git rev-parse HEAD main origin/main` and
  `git tag --points-at HEAD`.

- Observation: both relevant prior optimizations are already merged, not merely
  in progress. PR #116 eliminated unobservable disabled consumer
  instrumentation work, and PR #117 extracts delivery metadata once.
  Evidence: `git log --oneline --decorate -8` and the fetched deletion of the
  remote metadata branch.

- Observation: the five registered benchmark suites are
  `consumer-processing`, `envelope-serialization`, `json-engine-evaluation`,
  `publish-preparation`, and `redis-storage-cpu`, with 408 cases under the
  current retention schema.
  Evidence: `benchmarks/registry.py`, `benchmarks/cli.py`,
  `scripts/retain_benchmark_run.py`, and
  `uv run --extra benchmark python -m benchmarks list`.

- Observation: the existing consumer `minimal` and `observability-enabled`
  profiles vary Relayna observation delivery, not the OpenTelemetry SDK
  provider, sampler, or exporter. With only `opentelemetry-api` installed, both
  exercise the API's default no-op tracer and cannot establish the requested
  enabled/unsampled or enabled/sampled overhead.
  Evidence: `benchmarks/consumer_processing.py`,
  `src/relayna/observability/tracing.py`, and `pyproject.toml`.

- Observation: OpenTelemetry's global tracer provider is a one-shot process
  configuration and cannot be safely reset between timing profiles.
  Evidence: the installed API/SDK behavior and isolated mode tests. The harness
  therefore launches one fresh worker process per tracing mode.

- Observation: configured unsampled and sampled spans add valid active
  `trace_id` and `span_id` fields to status-event bodies, so the original
  publish fixture was 75 bytes larger than its requested exact target.
  Evidence: the first preliminary enabled-worker run rejected an expected
  1,024-byte status message observed at 1,099 bytes. Benchmark-only calibration
  now reserves the fixed JSON size of those fields without changing runtime
  behavior.

- Observation: the complete quick validation produced 976 exported spans in
  sampled mode: 688 consumer spans and 288 producer spans. Disabled and
  enabled-unsampled modes exported zero, as intended.
  Evidence:
  `/tmp/relayna-tracing-harness.EjLmLu/suite/tracing-suite.json`.

## Decision Log

- Decision: use exact base `283782ec95955f50e187e5fde82d12f03691834a`
  rather than stacking on another performance worktree.
  Rationale: it is the refreshed default branch, contains both predecessor
  optimizations, and keeps this PR independently reviewable.
  Date/Author: 2026-07-31 / Codex.

- Decision: treat the runtime work as a private, behavior-preserving internal
  refactor at latest release `v1.4.31` and frozen perimeter `v1.4.30`, with no
  compatibility shim.
  Rationale: the goal requires preserving public signatures, exports,
  configuration timing, task/status/workflow contracts, RabbitMQ propagation,
  persisted data, and wire formats. The user explicitly authorizes a perimeter
  change if genuinely necessary, but the preferred implementation requires
  none and freeze manifests must never change merely to silence tests.
  Date/Author: 2026-07-31 / Codex.

- Decision: extend only benchmark and benchmark-test/configuration surfaces
  before the baseline so the suite uses real OpenTelemetry SDK configurations
  for disabled, always-off unsampled, and always-on sampled/exported tracing.
  Rationale: this is required to measure the stated goal. The extension will be
  shared unchanged by baseline and candidate, and a source-tree check will
  prove `src/relayna` still matches the untouched base before baseline capture.
  Date/Author: 2026-07-31 / Codex.

- Decision: retain this task's evidence only below a unique
  `reports/reduce-tracing-overhead/<UTC timestamp>-<base SHA>/` root.
  Rationale: preliminary, baseline, candidate, and comparison runs must be
  immutable, checksum-bound, non-appending, and distinguishable from every
  earlier performance task.
  Date/Author: 2026-07-31 / Codex.

- Decision: run all five suites in each tracing mode rather than adding a
  tracing dimension only to consumer rows.
  Rationale: every existing benchmark remains part of every complete run;
  consumer and publish paths become direct tracing targets, while envelope,
  JSON-engine, and Redis-storage suites provide matched controls. Qualified
  case IDs prefix mode and benchmark, giving 1,224 unique comparison cells.
  Date/Author: 2026-07-31 / Codex.

## Outcomes & Retrospective

Work is in progress. The isolated branch and compatibility boundary are
established, the complete benchmark registry is known, and the measurement gap
that must be closed before the baseline is explicit.

## Context and Orientation

Relayna's public SDK lives under `src/relayna/`. The tracing helpers are in
`src/relayna/observability/tracing.py`. They use the OpenTelemetry API to
extract and inject W3C trace context, look up a tracer, start the current span,
and expose active trace identifiers. Producer call sites live in
`src/relayna/rabbitmq/client.py`. Real inbound task and workflow span call sites
live in `src/relayna/consumer/task_consumer.py` and
`src/relayna/consumer/workflow_consumer.py`.

SDK tests live under `tests/`. The benchmark framework under `benchmarks/`
registers five suites and emits standalone HTML. `consumer-processing` measures
the real private per-delivery `TaskConsumer._handle_message()` path and the
public `TaskConsumer.run_forever()` loop after RabbitMQ delivery.
`publish-preparation` measures the public local publishing path through a
deterministic no-op exchange. Neither benchmark includes broker or network
latency.

An OpenTelemetry provider decides whether spans are sampled. A sampled span is
recording and is delivered through configured processors to an exporter. An
unsampled span preserves trace-context decisions and propagation but is not
recording or exported. The benchmark's in-memory exporter is a deterministic
application-owned sink used only to prove exporter-facing work remains active.

The retained run will have `baseline/`, `candidate/`, and `comparison/`
siblings. Each execution manifest must record source and runtime SHAs, branch
and dirty state, UTC timestamps, exact commands, registry and expected cases,
warmups and repetitions, Python/uv/package and lock data, OS/architecture/CPU,
environment controls, SDK/provider/sampler/processor/exporter/propagator
configuration, report hashes and byte sizes, case keys, and proof that every
expected case occurs exactly once.

## Compatibility Boundary

Compatibility boundary: latest release tag and base `v1.4.31`; repository
production freeze perimeter `v1.4.30`. Preserve all released public imports and
signatures, configured provider and propagator behavior, W3C and baggage
propagation, carrier/header precedence, span names/kinds/parents/links,
attributes/events/status/exceptions, sampling and exporter delivery, async
context isolation, task/correlation/workflow metadata, RabbitMQ publication and
consumer ack/nack/reject/requeue behavior, retries/idempotency, observation and
metrics behavior, persisted values, and wire representations.

The benchmark-only SDK dependency and tracing-profile dimension do not change
the production dependency set or runtime behavior. The intended runtime patch
is private and internal. No request- or message-specific state may be cached
across operations. Any cached process-level OpenTelemetry object must be proven
correct for Relayna's supported dynamic provider/propagator configuration,
threads, loops, tests, forks, reconfiguration, and shutdown; otherwise it must
not be cached.

## Plan of Work

First add the narrow benchmark configuration needed to install and configure
the OpenTelemetry SDK only through the optional benchmark dependency group.
Extend the consumer and, if profiling requires direct producer evidence, the
publish benchmark matrices with explicit tracing modes. Configure each mode
outside timed message operations, use deterministic samplers and a synchronous
in-memory exporter, drain/reset counts between warmup and timed work, and report
provider, sampler, processor, exporter, propagator, exported span, and
propagation counts. Add benchmark tests for case inventory and active sampling
and export. Validate that no production file changed.

Create a unique run identity and synchronize with the frozen lock. Run all five
suites with canonical defaults before changing `src/relayna`. Retain reports
through benchmark-aware tooling that extracts raw embedded data, verifies
expected unique case keys, computes checksums, records complete provenance, and
validates standalone HTML parsing. Preliminary harness runs remain separately
labeled and excluded from comparison.

Next inspect and profile producer and consumer tracing call graphs. Count
provider/tracer and propagator lookups, extraction/injection calls, carrier and
attribute allocations/copies, conversions, current-context operations, span
creation, status/exception paths, and exporter delivery. Use deterministic
counters and allocation/CPU profiling outside canonical timing. Record evidence
for every hotspot retained in the implementation.

Implement only narrow internal changes proven by those profiles. Likely
candidate shapes include removing redundant carrier copies around
already-extracted per-message metadata, constructing filtered span attributes
once, eliminating repeated mapping conversions and string conversions, or
using equivalent lower-allocation API calls. Do not cache dynamic global
provider or propagator state unless installed OpenTelemetry semantics and
Relayna-supported reconfiguration prove that safe.

Add focused tests across disabled, unsampled, and sampled/exported operation;
valid, missing, and malformed inbound context; producer injection and consumer
extraction; parent, nested, and linked spans where supported; success, error,
retry, cancellation, and exception status; concurrent async messages; exporter
delivery; and supported custom/dynamic providers and propagators. Add narrow
call counters only where they demonstrate the targeted duplicate work without
coupling unrelated tests to implementation details.

Use separately named exploratory focused benchmark reports during iteration.
Before the final candidate, fetch `origin/main`. If relevant paths or versioning
changed, update cleanly and generate a fresh matched baseline/candidate pair
from the final base with the exact same benchmark commit, dependencies,
controls, cases, warmups, and repetitions. Generate full-suite JSON and
standalone HTML comparison artifacts containing absolute and percentage
latency/throughput changes, samples and variance, bytes/cardinality/weights,
all tracing modes, consumer CPU/loop throughput, unchanged control drift,
regressions/noise, provenance, and a neutral worth assessment.

If retained, select the next unused patch version only after the last fetch.
Update every established SDK, Studio, frontend, package, lock, generated,
freeze, changelog, release, tracing, benchmark, and report documentation
surface required by repository convention. State compatibility explicitly and
make only claims supported by the authoritative matched reports.

Finally run `$code-change-verification` from the beginning until all required
SDK and Studio backend checks pass, plus relevant frontend test/build, package
build, benchmark-test, artifact-regeneration, raw uniqueness, checksum, HTML
parse, source-hash, secret/path, stale-version, freeze, and final-diff checks.
Use `$pr-draft-summary`, stage only intentional files, commit conventionally,
push the named branch, and open a ready PR with the repository template.
Monitor GitHub checks to terminal status, request the first Codex review using
the repository mechanism, address every actionable thread with tests and a
fresh full verification pass, reply with evidence, and resolve appropriate
threads. Do not merge.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/2320/relayna` unless a temporary
detached benchmark worktree is explicitly recorded:

    git status --short --branch
    git fetch --prune origin
    uv sync --extra benchmark --frozen
    uv run --extra benchmark python -m benchmarks list
    uv run --extra benchmark python -m benchmarks run-all

Focused implementation and tracing validation will include:

    uv run pytest -q tests/test_observability.py tests/test_consumer.py \
      tests/test_consumer_processing_benchmark.py \
      tests/test_publish_preparation_benchmark.py
    uv run --extra benchmark python -m benchmarks run consumer-processing \
      --output <unique exploratory path>
    uv run --extra benchmark python -m benchmarks run publish-preparation \
      --output <unique exploratory path>

Final validation will include:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build
    uv run pytest -q tests/test_*benchmark*.py
    git diff --check

GitHub publication and monitoring will use authenticated `gh`, the repository
PR template, `gh pr checks`, GitHub Actions log inspection, the repository's
established Codex review request, and GitHub review-thread APIs. The task must
not merge the PR.

## Validation and Acceptance

The benchmark extension is accepted only if tests prove all tracing modes
actually configure the intended sampler/exporter behavior, report the expected
span and propagation counts, produce complete matrices, and leave
`src/relayna` byte-identical to the base before baseline timing.

Runtime tests must prove tracing remains enabled and behaviorally equivalent
for sampled and unsampled spans, propagation edge cases, producer and consumer
paths, relationships and attributes, success/error/retry/cancel paths,
exporter delivery, concurrency, and supported dynamic/custom global behavior.
Focused counters must show each targeted redundant operation occurs only once
where appropriate.

Baseline and candidate must each contain all five standalone HTML reports,
machine-readable raw sidecars, complete provenance/checksum manifests, and
exactly one occurrence of every expected case ID. The comparison JSON and HTML
must cover the entire matched suite and all tracing modes without hand-edited
measurements or favorable trial selection. Standalone HTML must parse and
contain the complete embedded unique dataset.

The final repository verification, relevant frontend checks, package builds,
GitHub CI, and first Codex review must all complete successfully with no
unresolved actionable feedback. The PR remains open and unmerged.

## Idempotence and Recovery

Every benchmark run uses a new directory. Never rerun into an existing retained
directory. Generate into a temporary unique path first, validate completion,
then retain it atomically through repository tooling. An interrupted or invalid
run stays clearly labeled and excluded; recovery creates a new run identity.

Benchmark configuration setup is process-scoped and must restore the prior
global OpenTelemetry provider and propagator after each focused test or run.
Where the SDK's global provider cannot be reset through public API, execute
independent configurations in isolated child processes rather than mutating a
shared long-lived process.

If final-base reconciliation changes runtime, benchmark, dependency, version,
or case shape, discard comparability—not artifacts—and create a fresh
back-to-back baseline/candidate pair. If verification fails, fix the cause and
rerun the required fail-fast stack from its beginning.

## Artifacts and Notes

Initial branch evidence:

    branch: codex/reduce-tracing-overhead
    base: 283782ec95955f50e187e5fde82d12f03691834a
    base tag: v1.4.31
    origin/main: 283782ec95955f50e187e5fde82d12f03691834a
    initial status: clean
    predecessor PRs: #116 and #117 merged
    initial registry: five suites, 408 cases

## Interfaces and Dependencies

Production continues to depend only on `opentelemetry-api`. The optional
`benchmark` extra may add a version-compatible `opentelemetry-sdk` used solely
by benchmark configuration, tests, and retained reports. No new public Relayna
export or signature is planned.

The benchmark data schemas must include a stable tracing-mode identifier and
configuration metadata. Retention and comparison tooling must derive expected
case IDs from the registry or a single validated inventory rather than relying
on undocumented hand-maintained counts.
