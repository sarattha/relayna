# Publish Preparation Benchmark and Single-Pass Task Preparation

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This plan is maintained in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Relayna publishers accept Pydantic models and canonical or configured-alias
mappings, validate them into canonical task/status/workflow contracts, resolve
topology routing, create AMQP messages, and publish them. After this change,
contributors can run `uv run python -m benchmarks run publish-preparation` to
measure that complete local CPU path without RabbitMQ or network latency. The
individual multi-task publishing path will prepare every task exactly once
rather than validating and dumping it once before delegation and again inside
`publish_task()`, while every released AMQP byte, header, routing, metric,
ordering, error, and concurrency behavior remains unchanged.

## Progress

- [x] (2026-07-29 14:57Z) Created and checked out
  `perf/publish-preparation-benchmark`; confirmed a clean worktree.
- [x] (2026-07-29 14:57Z) Read `AGENTS.md`, `PLANS.md`, and the mandatory
  implementation, freeze, verification, and PR-summary skill instructions.
- [x] (2026-07-29 14:57Z) Established the `v1.4.29` compatibility boundary and
  chose a private prepared-task handoff with no public or wire change.
- [x] (2026-07-29 15:16Z) Inspected the complete publisher and benchmark/test
  patterns.
- [x] (2026-07-29 15:16Z) Implemented and tested the deterministic 72-cell
  benchmark harness and self-contained HTML report.
- [x] (2026-07-29 15:17Z) Ran the pre-optimization benchmark and retained
  `reports/publish-preparation-baseline.html`.
- [x] (2026-07-29 15:24Z) Added public-path characterization tests proving
  duplicate real validation and complete behavior equivalence.
- [x] (2026-07-29 15:26Z) Implemented private single-pass individual task
  publishing and reused validated task models in batch envelopes.
- [x] (2026-07-29 15:29Z) Ran the identical post-change benchmark and generated
  `reports/publish-preparation.html` with embedded before/after comparison.
- [x] (2026-07-29 15:40Z) Ran formatting, linting, type checking, 626 SDK
  tests, benchmark registry/command/run-all checks, and the full mandatory
  SDK + Studio backend `code-change-verification` sequence.
- [x] (2026-07-29 15:42Z) Completed benchmark conclusions and outcomes; PR
  draft handoff remains the final response step.
- [x] (2026-07-30 08:10Z) Removed executable legacy benchmark logic and replaced
  it with strict loading of an immutable, provenance-bound baseline artifact.
- [x] (2026-07-30 08:25Z) Regenerated the current-runtime comparison and reran mandatory
  verification after the retained-baseline revision.
- [x] (2026-07-30 09:25Z) Addressed first Codex review by preserving subclass
  and monkey-patched `publish_task()` dispatch while retaining the base-client
  single-pass fast path; full verification passed.

## Surprises & Discoveries

- Observation: `publish_tasks()` eagerly calls `_prepare_task_payload()` for
  every task and, in individual mode, then delegates those prepared mappings to
  public `publish_task()`, whose first operation calls `_prepare_task_payload()`
  again.
  Evidence: `src/relayna/rabbitmq/client.py` lines 166-225 on the starting
  branch.

- Observation: Batch-envelope mode also explicitly called
  `TaskEnvelope.model_validate()` on each canonical mapping immediately after
  `_prepare_task_payload()` had already performed the same validation.
  Evidence: The new public-path validation spy observed two validations per
  task on the starting implementation; candidate regression tests observe one.

- Observation: Duplicate-removal benefit is largest before JSON encoding
  dominates.
  Evidence: The final regenerated immutable-baseline comparison measured
  1.081× at 1 KB, 1.072× at 16 KB, and 1.012× at 1 MB; the full individual
  matrix geometric mean was 1.052×.

- Observation: The original one-second canonical workload was too sensitive to
  ordinary machine drift, and the generated batch timestamp made actual bodies
  seven bytes larger than the calibrated fixed-time fixture.
  Evidence: Strengthening iteration counts roughly ninefold brought unchanged
  workflow/status controls to within about 1%; freezing the contract clock in
  the benchmark now asserts every actual AMQP body is exactly its target size.

- Observation: Released individual batch publishing dispatched each prepared
  mapping through `self.publish_task`, so an unconditional private fast path
  bypassed subclass and monkey-patched extension behavior.
  Evidence: `v1.4.29` calls `map_bounded(prepared_tasks, self.publish_task, ...)`;
  the first Codex review identified the compatibility regression.

## Decision Log

- Decision: Treat `v1.4.29` as the released compatibility boundary and prohibit
  public signatures/exports, JSON semantics, wire bytes, AMQP metadata, routing,
  metrics, ordering, errors, or concurrency changes.
  Rationale: The implementation-strategy and production-freeze rules identify
  RabbitMQ publishing as a released frozen surface; the requested performance
  fix can remain entirely private.
  Date/Author: 2026-07-29 / Codex.

- Decision: A prepared task may enter a private publishing method only when the
  same public operation has just produced it through the existing real
  `_prepare_task_payload()` validation and canonical dump path.
  Rationale: This removes confirmed redundant work without trusting unvalidated
  external inputs or adding a reusable bypass around public validation.
  Date/Author: 2026-07-29 / Codex.

- Decision: Retain a baseline report produced by the completed harness before
  editing runtime publishing behavior, then rerun the identical matrix for the
  candidate report.
  Rationale: This gives a fair within-environment comparison and preserves
  evidence of the original duplicate-preparation path.
  Date/Author: 2026-07-29 / Codex.

- Decision: Represent the internal preparation boundary with the existing
  private `TaskEnvelope` plus its canonical JSON-mode dump: individual mode
  publishes the dump directly, while batch mode passes the already validated
  model instances into `BatchTaskEnvelope`.
  Rationale: This eliminates both confirmed validation paths without
  `model_construct`, a public bypass, a new export, or any wire-format change.
  Date/Author: 2026-07-29 / Codex.

- Decision: Retain the corrected baseline HTML immutably and bind it to a small
  schema-versioned provenance sidecar instead of keeping executable legacy
  publishing code.
  Rationale: The report already contains the full result matrix and preparation
  evidence. Hash, revision, fixed-clock, exact-size, matrix, environment, and
  preparation-count validation preserves trustworthy comparison without a
  second implementation of the old production algorithm.
  Date/Author: 2026-07-30 / Codex.

- Decision: Use the private single-pass publisher only when the resolved
  `publish_task` method is the original base implementation. If a subclass or
  monkey-patch replaces the method, dispatch through that public override as
  `v1.4.29` did.
  Rationale: This preserves released retries, auditing, header customization,
  and arbitrary override behavior. Falling back to public validation also
  avoids trusting a prepared mapping that an override may mutate.
  Date/Author: 2026-07-30 / Codex.

## Outcomes & Retrospective

Implementation and full verification are complete. The individual public
path now prepares two inputs twice total rather than four times, and public
`publish_task()` remains exactly once. Representative old/new individual and
batch publications match byte-for-byte and structurally across routing,
headers, correlation IDs, priorities, trace fields, metrics counters, ordering,
exceptions, and concurrency limits. The final immutable-baseline comparison
shows 1.052× geometric-mean individual total-path speedup and 1.065× batch
speedup. The
mandatory verification stack passed 631 SDK tests (3 skipped) and all 244
Studio backend tests after format, lint, and type checking in both workspaces.
Subclass and monkey-patched `publish_task()` implementations continue to receive
each canonical prepared mapping through the released public dispatch path.

## Context and Orientation

The Relayna SDK lives under `src/relayna/`. Its RabbitMQ client in
`src/relayna/rabbitmq/client.py` owns public publishing methods, validation and
canonicalization helpers, topology routing, AMQP message construction, and
publish metrics. `publish_tasks(..., mode="individual")` currently prepares a
collection and delegates each prepared mapping to `publish_task()`.
`publish_tasks(..., mode="batch_envelope")` packages canonical tasks into one
batch contract and AMQP message.

Repository benchmarks live outside the SDK in `benchmarks/`. The registry in
`benchmarks/registry.py` exposes benchmark definitions to the CLI in
`benchmarks/cli.py`; shared metadata and atomic artifact writing are in
`benchmarks/reporting.py`. Stable reports are written under `reports/`.
Benchmark tests live under `tests/` and use deterministic fixtures and
structural assertions rather than timing thresholds.

In this plan, “preparation” means conversion/alias normalization, Pydantic
validation, and canonical model dumping. “Complete local CPU publish path”
continues through routing and trace/header construction, Pydantic Core JSON
encoding, `aio_pika.Message` creation, priority handling, metrics, and an async
deterministic no-op exchange boundary. It excludes connection setup, sockets,
RabbitMQ broker work, and network latency.

## Compatibility Boundary

Compatibility boundary: release tag `v1.4.29`; released SDK RabbitMQ publishing
behavior is frozen. The change is an internal refactor only. Public imports and
signatures, contract schemas, strict JSON transport semantics, AMQP bodies,
headers, correlation IDs, priorities, routing keys, trace fields, metrics,
publish association/order, exception types, and concurrency-limit behavior must
remain identical. Freeze manifests remain unchanged.

## Plan of Work

First, inspect all publish variants, routing topologies, alias contracts,
metrics, and existing benchmark/report tests. Add a narrowly scoped
`benchmarks/publish_preparation.py` module that constructs deterministic exact
payload-size fixtures, fake exchanges, representative clients/topologies, and
one reusable event loop per benchmark run. Register it in
`benchmarks/registry.py` and add deterministic tests for registration, matrix
coverage, fixture sizing, phase/stat calculations, fake publication, CLI
dispatch, and self-contained HTML.

Run the harness against the untouched publisher and retain a named baseline
report. Add public-path characterization tests that count actual
`_prepare_task_payload()` executions for BaseModel, canonical mapping, and alias
mapping inputs. Snapshot each publication’s routing key, body, correlation ID,
priority, headers, ordering, metrics, exceptions, and bounded concurrency.

Then refactor `RelaynaRabbitClient` so public `publish_task()` prepares once and
hands the canonical mapping to a private publisher, while individual
`publish_tasks()` prepares each input once and invokes that same private
publisher directly. Preserve eager preparation before bounded publication so
invalid inputs and ordering behavior do not shift. Inspect batch-envelope mode
and remove only independently proven redundant preparation.

Finally, rerun focused and full tests, execute the identical canonical
post-change benchmark, generate a self-contained comparison report, and run the
mandatory full verification script from its first command. Update this plan
with measured deltas and conclusions.

## Concrete Steps

All commands run from
`/Users/jobz/.codex/worktrees/34f6/relayna`.

    uv run pytest <focused benchmark and RabbitMQ tests>
    uv run python -m benchmarks run publish-preparation --output <baseline>
    uv run python -m benchmarks run publish-preparation --output <candidate>
    uv run python -m benchmarks list
    uv run python -m benchmarks run-all
    make format
    make lint
    make typecheck
    make test
    bash .codex/skills/code-change-verification/scripts/run.sh

If any mandatory verification command fails, fix it and rerun the verification
script from the start.

## Validation and Acceptance

Tests must prove the benchmark is registered, exact target sizes are achieved
and reported, all bounded message/input/payload/topology cases are represented,
statistics and comparison calculations are correct, fake exchange publishing is
deterministic, CLI dispatch works, and HTML is self-contained.

Runtime characterization must show one real preparation pass per input through
public `publish_tasks(..., mode="individual")` and one through public
`publish_task()` for BaseModel, canonical mapping, and configured-alias mapping.
Representative baseline/candidate publications must compare byte-for-byte
bodies and structurally equal routing keys, headers, correlation IDs,
priorities, trace fields, metrics, ordering/association, exceptions, and
concurrency behavior. Production-freeze tests must pass unchanged.

The baseline and candidate reports must state median latency, dispersion,
operations/second, MiB/second, repeats/iterations, actual payload bytes, total
prepared/published counts, relative speedup, environment/package metadata,
methodology, duplicate evidence, and remaining bottlenecks. No acceptance test
will assert a timing threshold.

## Idempotence and Recovery

Benchmark runs are deterministic in matrix and fixture content but overwrite
only their explicit report path atomically; rerunning is safe. Fake exchanges
require no local RabbitMQ service. The runtime refactor has no migration or
durable-state step. If interrupted, `git status --short` and this plan’s
Progress section identify partial work. Unrelated user changes must not be
reverted or reformatted.

## Artifacts and Notes

Baseline:
`/Users/jobz/.codex/worktrees/34f6/relayna/reports/publish-preparation-baseline.html`.
Its immutable provenance and SHA-256 binding are stored in
`/Users/jobz/.codex/worktrees/34f6/relayna/reports/publish-preparation-baseline.json`.

Candidate:
`/Users/jobz/.codex/worktrees/34f6/relayna/reports/publish-preparation.html`.

The baseline probe records four real preparation calls per two-task individual
operation. The candidate records two. Across individual cases, final
geometric-mean speedup is 1.052×; by size it is 1.081× at 1 KB, 1.072× at
16 KB, 1.044× at 128 KB, and 1.012× at 1 MB. The diminishing large-payload
gain identifies transport JSON encoding and memory copying as the remaining
dominant work. Batch-envelope cells improved by 1.065× geometric mean after
reusing the already validated task models. The final report’s all-cell
geometric mean is 1.042×, including unchanged workflow/status controls.

## Interfaces and Dependencies

The public `RelaynaRabbitClient.publish_task()` and
`RelaynaRabbitClient.publish_tasks()` signatures remain unchanged. Any new
prepared-task publisher is a private method on `RelaynaRabbitClient`. The
benchmark uses existing Relayna contracts/topologies, `aio_pika`, Pydantic Core
JSON encoding through production code, standard-library timing/statistics, and
shared benchmark registry/reporting helpers. It adds no runtime dependency and
does not export benchmark helpers from the SDK.
