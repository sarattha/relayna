# Relayna Envelope Serialization Microbenchmarks

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with
`/Users/jobz/.codex/worktrees/98c7/relayna/PLANS.md`.

## Purpose / Big Picture

Relayna contributors need a reproducible way to measure JSON serialization and
validated parsing for canonical task messages without changing the SDK runtime.
After this work, running one documented repository command will benchmark
individual `TaskEnvelope` messages and `BatchTaskEnvelope` messages at exact
baseline wire sizes of 1 KB, 16 KB, 128 KB, and 1 MB, then write a self-contained
HTML report under `reports/`. The report will explain the methodology and show
timings, throughput, actual serialized sizes, relative performance, and enough
environment metadata to reproduce the run.

## Progress

- [x] (2026-07-28 17:45Z) Scoped the RSC-only exception to the Studio lockfile
  in Trivy's structured ignore format after CI showed that the independent
  filesystem scanner does not consume the npm audit exception file.
- [x] (2026-07-28 17:29Z) Updated vulnerable Python documentation and Studio
  frontend dependencies, documented the RSC-only React Router advisory
  exception, and added an expiring fail-closed npm audit filter with Node tests.
- [x] (2026-07-28 17:20Z) Replied to and resolved the first Codex review
  thread after pushing the cross-platform mode-assertion fix.
- [x] (2026-07-28 17:19Z) Addressed the first Codex review finding by
  restricting the exact report-mode assertion to POSIX while retaining the
  cross-platform readability/content assertion.
- [x] (2026-07-28 17:07Z) Generalized the one-off envelope entry point into a
  discoverable benchmark CLI with `list`, `run`, and `run-all` commands.
- [x] (2026-07-28 17:07Z) Documented CLI usage, benchmark authoring
  conventions, the registration contract, shared reporting helpers, testing
  expectations, and artifact rules for future benchmark types.
- [x] (2026-07-28 17:07Z) Added CLI/registry tests, regenerated the envelope
  report through both `make benchmark` and `make benchmark-all`, and reran
  mandatory verification successfully.
- [x] (2026-07-28 16:49Z) Created and checked out
  `perf/envelope-microbenchmarks-html`; confirmed a clean worktree.
- [x] (2026-07-28 16:49Z) Read `AGENTS.md`, `PLANS.md`, the current envelope
  contracts and publish/consume paths, and the mandatory verification and PR
  summary skills.
- [x] (2026-07-28 16:49Z) Established an additive tooling-and-tests
  compatibility boundary with no production runtime, public API, contract, or
  freeze-manifest changes.
- [x] (2026-07-28 16:55Z) Implemented deterministic exact-size fixtures, the
  32-case benchmark matrix, timing/result models, equivalence checks,
  environment capture, atomic report writes, and self-contained HTML rendering.
- [x] (2026-07-28 16:55Z) Added `make benchmark-envelopes`, benchmark
  documentation, and 11 focused non-wall-clock tests.
- [x] (2026-07-28 16:58Z) Ran focused checks and the canonical benchmark;
  generated a mode-`0644`, 13,772-byte report containing 32 measured rows and
  exact baseline sizes.
- [x] (2026-07-28 16:58Z) Ran `$code-change-verification` successfully after
  syncing fresh-worktree development environments: SDK format/lint/typecheck,
  430 passed and one skipped test; Studio backend format/lint/typecheck and 244
  passed tests.
- [x] (2026-07-28 16:58Z) Rendered the report in a headed Chromium browser at
  1440×1000 and 390×844, confirmed readable responsive layout, and removed the
  temporary browser/server artifacts.
- [x] (2026-07-28 16:58Z) Updated the living plan, collected PR-draft inputs,
  confirmed the branch has no commits ahead of `origin/main`, and prepared the
  uncommitted handoff.

## Surprises & Discoveries

- Observation: Relayna currently serializes task and batch transport
  dictionaries with `json.dumps(payload, ensure_ascii=False).encode("utf-8")`.
  Inbound task messages are decoded as UTF-8, parsed with `json.loads`, and then
  validated with `TaskEnvelope.model_validate` or
  `BatchTaskEnvelope.model_validate`.
  Evidence: `src/relayna/rabbitmq/client.py` and
  `src/relayna/consumer/task_consumer.py`.

- Observation: The repository already tracks durable generated HTML under
  `reports/`, so `reports/envelope-microbenchmarks.html` fits the established
  artifact convention.
  Evidence: `reports/relayna-sdk-studio-analysis.html` is tracked.

- Observation: `uv run pytest` invokes the installed console script without
  adding the repository root to `sys.path`, while `uv run python -m
  benchmarks.envelope_serialization` resolves the intentionally unpackaged
  benchmark module as expected.
  Evidence: the first focused collection failed to import `benchmarks`; the
  test now adds only the repository root locally and collection passes without
  changing global pytest or package configuration.

- Observation: a `NamedTemporaryFile` starts with owner-only permissions on
  macOS, and replacing the report with it preserved mode `0600`.
  Evidence: inspection of the first generated report. The atomic writer now
  explicitly sets the temporary report to mode `0644` before replacement, with
  a focused assertion.

- Observation: the fresh Studio backend virtual environment initially lacked
  development tools, so the first mandatory verification attempt stopped
  before Studio formatting.
  Evidence: `make -C studio/backend format` could not spawn Ruff. After
  `make -C studio/backend sync`, the complete verification script passed from
  the beginning.

- Observation: headed-browser QA reported one console error caused solely by
  the temporary static server returning 404 for an unsolicited
  `/favicon.ico`; the report itself loaded with HTTP 200 and has no external
  resources.
  Evidence: Playwright desktop and narrow-viewport snapshots plus the local
  server transcript.

## Decision Log

- Decision: Introduce a small internal benchmark registry and a package-level
  CLI with `list`, `run <type>`, and `run-all`, while preserving
  `make benchmark-envelopes` as a compatibility alias.
  Rationale: Future benchmark types can supply their own arguments and runner
  without growing one central conditional or changing the Relayna runtime
  package. Discovery and canonical all-benchmark runs remain uniform.
  Date/Author: 2026-07-29 / Codex.

- Decision: Compare the current stdlib-plus-Pydantic pipelines with Pydantic's
  direct `model_dump_json` and `model_validate_json` operations.
  Rationale: These are semantically comparable current-dependency operations
  that can be measured using identical model inputs or identical baseline wire
  bytes. The benchmark informs later implementation choices without changing
  production behavior now.
  Date/Author: 2026-07-28 / Codex.

- Decision: Define each requested target size as the exact byte length emitted
  by the current Relayna outbound pipeline and tune deterministic ASCII padding
  inside the envelope payload to hit it.
  Rationale: The targets then represent concrete current wire sizes while the
  report can still disclose the actual byte length produced by every compared
  outbound implementation.
  Date/Author: 2026-07-28 / Codex.

- Decision: Use fixed per-size iteration counts and repeated `perf_counter_ns`
  samples rather than time-based adaptive loops.
  Rationale: Fixed work makes runs reproducible and allows tests to validate the
  matrix without brittle wall-clock thresholds.
  Date/Author: 2026-07-28 / Codex.

## Outcomes & Retrospective

The repository now has a deterministic, dependency-free envelope benchmark
harness, a documented `make benchmark-envelopes` command, focused regression
tests, and a real self-contained HTML report. The requested matrix is complete:
both task and two-task batch envelopes, all four exact current-wire targets,
current and Pydantic-direct implementations, and both outbound and validated
inbound directions.

All relevant SDK and Studio backend verification passed, the generated artifact
passed structural and rendered-layout inspection, and production source,
contracts, APIs, behavior, and freeze manifests remain unchanged. The only
setup wrinkle was installing declared development dependencies in the fresh
worktree before the mandatory verification rerun.

The follow-up generalized this into a reusable repository benchmark framework.
`python -m benchmarks list`, `run <type>`, and `run-all` now provide a stable
CLI; each benchmark registers its own arguments and runner through a validated
definition. Shared helpers capture environment metadata and atomically write
artifacts, while benchmark-specific fixtures, timing, matrices, and rendering
remain isolated. The authoring guide defines how to add and verify future
types. Final verification passed with 437 SDK tests, one existing skip, and 244
Studio backend tests.

PR follow-up resolved the first Codex review finding and repaired newly failing
dependency audits. The Python documentation dependency now resolves to a
non-vulnerable version. Studio uses the newest compatible React Router and
PostCSS releases; a single expiring exception covers an advisory that upstream
explicitly limits to unused unstable RSC APIs. A tested audit wrapper continues
to fail on every other high or critical npm finding. The same exception is
represented in Trivy's structured YAML format, scoped to the Studio lockfile,
and expires on the same date.

## Context and Orientation

The SDK package lives under
`/Users/jobz/.codex/worktrees/98c7/relayna/src/relayna`. Canonical task models
are defined in `src/relayna/contracts/task.py`. A `TaskEnvelope` carries one
task payload. A `BatchTaskEnvelope` wraps one or more task envelopes for batch
transport. The current publisher prepares JSON-compatible dictionaries through
Pydantic and encodes them with the Python standard library; the task consumer
performs the inverse JSON parse followed by Pydantic validation.

The benchmark code lives outside `src/relayna/` under `benchmarks/`. The
package-level entry point is `benchmarks/__main__.py`; `benchmarks/cli.py`
handles discovery and dispatch, `benchmarks/registry.py` defines the plugin
contract, and `benchmarks/reporting.py` contains shared report support.
Benchmark-specific logic remains in modules such as
`benchmarks/envelope_serialization.py`. Focused tests live under `tests/`, and
durable generated results live under `reports/`. The root `Makefile` exposes
generic list, single-run, all-run, and envelope-alias targets.

## Compatibility Boundary

Compatibility boundary: latest release tag and strict production freeze tag
`v1.4.29`. This task adds benchmark tooling, tests, documentation, and a
generated report only. It does not edit SDK runtime modules, public exports,
function or model signatures, external configuration, serialized contracts,
wire behavior, persisted data, routes, Studio behavior, or production-freeze
manifests. Therefore the implementation-strategy and production-freeze-guard
skills are not required.

## Plan of Work

Create an importable benchmark module under `benchmarks/` that builds stable
Relayna-shaped models with fixed identifiers and timestamps. Calibrate an ASCII
padding field so the current outbound implementation produces exactly each
requested byte target. Define named outbound and inbound implementations,
construct the complete matrix, time fixed iterations over repeated samples, and
calculate median nanoseconds per operation, throughput, and ratios to the
current implementation.

Render results through a dependency-free HTML generator. Inline all CSS and
content, escape dynamic values, include methodology and operation definitions,
and capture UTC timestamp, Python implementation/version, platform and
architecture, processor, executable, and Relayna/Pydantic package versions.
Make the command create parent directories and replace the stable report only
after a successful run.

Add a `Makefile` target and benchmark README that document the canonical command.
Add focused tests for all exact fixture sizes, the complete 32-case matrix,
relative calculations, metadata, and standalone HTML generation. Tests will
use synthetic timings and tiny iteration settings rather than assert absolute
performance.

Run formatting and linting over SDK, tests, and benchmarks; typecheck the SDK as
required by repository policy; run focused tests and the complete SDK suite;
execute the benchmark target to generate the real HTML report; inspect its key
content; and finally run the mandatory `$code-change-verification` script.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/98c7/relayna`:

    uv run pytest tests/test_envelope_microbenchmarks.py
    make format
    make lint
    make typecheck
    make test
    make benchmark-list
    make benchmark
    make benchmark-all
    bash .codex/skills/code-change-verification/scripts/run.sh

The benchmark command must finish with a success message naming
`reports/envelope-microbenchmarks.html`. The generated file must open without
external assets or network access.

## Validation and Acceptance

Acceptance requires all four target byte sizes (1,024; 16,384; 131,072; and
1,048,576) for both envelope kinds, current and comparison implementations for
both outbound and inbound directions, and exactly 32 result rows. Fixture tests
must prove that current outbound bytes exactly equal every target. Matrix tests
must prove that all Cartesian-product cases exist once. HTML tests must prove
that methodology, results, metadata, units, iterations, throughput, ratios, and
actual byte sizes are present without using timing thresholds.

The final real report must be generated successfully and contain nonzero timing
and throughput values for all cases. Root SDK format, lint, typecheck, and test
commands plus the full repository verification skill must pass.

## Idempotence and Recovery

Fixture construction and benchmark execution have no external service
dependencies and are safe to rerun. The report is rendered in memory and
written through a temporary sibling followed by `Path.replace`, so a failed
benchmark does not leave a partially written stable report. If verification
formats files, inspect the diff, fix failures, and rerun the complete required
sequence. No migration or cleanup is needed because production state is not
modified.

## Artifacts and Notes

The expected stable artifact is:

    reports/envelope-microbenchmarks.html

The report will explicitly identify the current operations as the baseline and
will not claim that a faster comparison should replace production code.

## Interfaces and Dependencies

The benchmark framework uses only Python's standard library plus Relayna's
existing dependencies. A `BenchmarkDefinition` supplies a kebab-case name,
summary, repository-relative default output, argument builder, and runner. A
runner returns a `BenchmarkOutcome` naming at least one existing artifact and a
positive measurement count. The envelope CLI accepts an output path, repeat
count, and optional per-size iteration overrides while canonical commands use
fixed documented defaults. No new runtime or development dependency is added.
