# Eliminate Disabled Consumer Instrumentation Work

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Improve Relayna's successful `TaskConsumer` hot path when both consumer
observation delivery and resource metrics are disabled. After this change, a
consumer with no observation sink and no metrics recorder will not sample task
CPU/RSS or construct successful-path observation events that cannot be
delivered. Configurations with an observation sink, metrics recorder, or both
must retain their current event, resource-sampling, metric, tracing, handler,
status, retry, DLQ, cancellation, and AMQP acknowledgement behavior.

The improvement is observable through deterministic regression tests and a
retained before/after run of all five registered benchmarks. The comparison
will treat envelope serialization, JSON-engine evaluation, Redis storage CPU,
and publish preparation as controls and will report every consumer-processing
cell without a timing pass/fail gate or favorable-cell selection.

## Progress

- [x] (2026-07-31 03:48Z) Read `AGENTS.md`, `PLANS.md`, and the complete
  `$implementation-strategy`, `$production-freeze-guard`,
  `$code-change-verification`, `$pr-draft-summary`, `$yeet`, `$gh-fix-ci`, and
  `$gh-address-comments` skill instructions.
- [x] (2026-07-31 03:48Z) Fetched `origin/main`, created
  `codex/consumer-disabled-instrumentation` with `git switch -c`, and confirmed
  clean base `1491705c2031` (`Add consumer processing benchmark (#115)`).
- [x] (2026-07-31 03:50Z) Synced SDK, Studio backend, Studio frontend, and
  optional benchmark dependencies without changing the lockfiles.
- [x] (2026-07-31 03:51Z) Ran the canonical five-benchmark suite from an
  isolated clean checkout of `1491705c2031`, retained every HTML report under
  the stable baseline directory, and wrote a checksum-bound provenance
  manifest.
- [x] (2026-07-31 03:54Z) Inspected the consumer instrumentation path and added
  the smallest internal fast paths plus deterministic regression coverage.
- [x] (2026-07-31 03:55Z) Passed 104 focused consumer tests and ran two focused
  candidate benchmarks showing repeatable minimal per-message and loop gains.
- [x] (2026-07-31 04:02Z) Ran two complete baseline/candidate five-benchmark
  pairs, retained all reports and provenance, promoted the sequential rerun to
  authoritative evidence, and generated the self-contained comparison HTML
  and machine-readable manifest.
- [x] (2026-07-31 04:06Z) Bumped every established release-owned
  version surface from `1.4.29` to `1.4.30`, updated locks, changelog, benchmark
  and consumer documentation.
- [x] (2026-07-31 04:09Z) Ran the complete final validation matrix, including the full
  `$code-change-verification` script from the beginning, frontend tests/build,
  benchmark smoke tests and `run-all`, builds, `git diff --check`, and freeze
  manifest comparison.
- [ ] Prepare the `$pr-draft-summary`, intentionally stage in-scope files,
  commit, push, and open a ready-for-review pull request using the authorized
  `$yeet` flow.
- [ ] Monitor required checks and the first Codex code review, fix and verify
  actionable findings, reply to exact threads, resolve addressed threads, and
  leave the pull request unmerged.

## Surprises & Discoveries

- Observation: the requested base commit is the fetched `origin/main` commit,
  while the worktree initially had a detached `HEAD` already at that commit.
  Evidence: both `git rev-parse origin/main` and the new branch `HEAD` resolve
  to `1491705c2031`.

- Observation: the canonical baseline suite completed all five registered
  benchmarks in 40 seconds and wrote 408 total measurements.
  Evidence: `reports/consumer-disabled-instrumentation/baseline/manifest.json`
  binds the five retained HTML reports to source commit, environment, exact
  filenames, byte sizes, and SHA-256 checksums.

- Observation: frontend dependency installation reports two existing
  high-severity audit findings.
  Evidence: `npm ci` completed successfully and printed the audit count; no
  dependency update is in scope before release metadata changes.

- Observation: two focused candidate runs improved the minimal per-message
  geometric mean by `20.6%` and `22.0%`, including `27.5%`/`29.3%` at 1 KB
  and `24.4%`/`27.0%` at 16 KB.
  Evidence: `/tmp/consumer-disabled-instrumentation-iteration-1.html` and
  `/tmp/consumer-disabled-instrumentation-iteration-2.html`.

- Observation: the initial full candidate pair improved minimal per-message by
  `22.8%` and minimal loop by `12.7%`, but an unchanged observation-enabled
  loop aggregate moved `+2.4%` with one `+18.6%` cell.
  Evidence: the immutable initial `baseline/` and `candidate/` reports and
  manifests remain retained under
  `reports/consumer-disabled-instrumentation/`.

- Observation: the authoritative sequential rerun improved minimal per-message
  by `22.1%`, minimal loop by `18.3%`, the 1 KB minimal group by `26.4%`, and
  the 16 KB minimal group by `25.3%`. Observation-enabled per-message and loop
  aggregates changed `+1.9%` and `-3.3%`; control aggregates ranged from
  `-1.7%` to `+1.0%`.
  Evidence: `reports/consumer-disabled-instrumentation/comparison.html` and
  `comparison.json` include every consumer and control cell.

## Decision Log

- Decision: treat this as a private, behavior-preserving fast path at the
  frozen `v1.4.29` compatibility boundary.
  Rationale: instrumentation work may be skipped only when no configured
  observer or metrics consumer could receive it. Public exports/signatures,
  task/status/workflow contracts, persisted or wire bytes, routing, AMQP
  acknowledgement/rejection, tracing, errors, and freeze manifests remain
  unchanged.
  Date/Author: 2026-07-31 / Codex.

- Decision: capture the baseline from a separate clean checkout of exact commit
  `1491705c2031`, then copy only completed reports into the stable retained
  artifact directory.
  Rationale: the living plan and later implementation can evolve in the main
  worktree without contaminating historical execution or overwriting canonical
  reports.
  Date/Author: 2026-07-31 / Codex.

- Decision: accept the user's explicit authority to cross the frozen perimeter
  only where the established `1.4.30` version/release convention requires it.
  Rationale: the runtime optimization remains private and behavior-preserving;
  any intentional version-only freeze manifest update will be named separately
  and will not be used to conceal API, schema, wire, or behavior changes.
  Date/Author: 2026-07-31 / Codex.

- Decision: ship the private fast path based on the authoritative sequential
  rerun.
  Rationale: all four minimal per-message size groups and all 12 minimal loop
  cells improved, including stable material 1 KB and 16 KB gains across
  focused and full repetitions. Instrumented profiles were neutral-to-better in
  aggregate relative to observed local drift, and deterministic tests prove
  behavior rather than timing.
  Date/Author: 2026-07-31 / Codex.

- Decision: retain the initial pair and make the complete sequential rerun the
  only authoritative comparison.
  Rationale: this follows the protocol for unchanged-profile drift without
  deleting evidence or choosing favorable cells.
  Date/Author: 2026-07-31 / Codex.

## Outcomes & Retrospective

Work is in progress. This section will record the full benchmark evidence,
verification results, compatibility outcome, release publication, CI state,
and first Codex review outcome.

## Context and Orientation

Relayna's SDK runtime is under `src/relayna/`. The target is the real
successful-message path of `TaskConsumer` in
`src/relayna/consumer/task_consumer.py`, which parses an already-delivered AMQP
message, constructs a task context, invokes the handler, applies status/retry
policies, acknowledges or rejects the message, emits observation events, and
records optional resource metrics. An observation sink receives typed
observability events. A metrics recorder consumes resource samples. OpenTelemetry
tracing is a separate supported concern and remains enabled exactly as before.

Deterministic SDK tests live under `tests/`. The benchmark framework lives under
`benchmarks/`; `uv run python -m benchmarks run-all` runs envelope
serialization, JSON-engine evaluation, Redis storage CPU, publish preparation,
and consumer processing and writes five canonical HTML reports under
`reports/`.

The retained evidence for this optimization will live under
`reports/consumer-disabled-instrumentation/`, with `baseline/` and `candidate/`
siblings plus a self-contained comparison report and JSON manifest. Each run
manifest will name the source commit, clean-state check, canonical command,
timestamps, interpreter/package/lock/environment metadata, exact report
filenames, and SHA-256 checksums.

## Compatibility Boundary

Compatibility boundary: strict released production perimeter `v1.4.29`.
Implementation is internal to the existing `TaskConsumer` configuration and
does not add or change public imports, exported signatures, external
configuration, task/status/workflow contracts, Redis or RabbitMQ data, route
responses, Studio APIs, or freeze manifests. The zero-sink/zero-metrics path may
omit only construction and sampling whose results are provably unobservable.
Metrics-only, observation-only, and combined configurations preserve their
existing samples, events, order, fields, timestamps, counts, and swallowed
instrumentation errors.

The `1.4.30` release metadata update will follow established version surfaces.
A version-bearing freeze manifest may change only if established release
convention requires it; any such intentional version-only update must be
documented and may not disguise a frozen API/schema change.

## Plan of Work

First synchronize all repository workspaces and optional benchmark dependencies.
Create a temporary Git worktree detached at `1491705c2031`, run the canonical
`run-all` command once, verify all five reports, copy them to the retained
baseline directory, and hash them with a provenance manifest. Remove the
temporary worktree only after copied hashes have been verified.

Inspect `TaskConsumer` successful, failure, cancellation, retry, and resource
instrumentation code. Add private conditionals at the earliest safe boundary:
do not call hot-path successful observation constructors when
`observation_sink` is absent, and do not collect start/end CPU/RSS samples or
construct `TaskResourceSampled` when both observation and metrics are absent.
Keep metrics-only sampling and recording, and keep the complete observation
path byte-for-behavior equivalent whenever a sink exists. Do not alter
OpenTelemetry, context/header construction, scheduler behavior, or benchmark
runtime copies.

Add deterministic tests that patch the sampling and relevant event constructors
to prove they are not invoked when disabled, verify the exact four successful
events and equivalent data when observation is enabled, verify metrics-only and
combined resource behavior, and cover representative handler, ack/reject,
exception, cancellation, lifecycle, and retry invariants without time
thresholds.

Run focused tests and consumer benchmarks while iterating. Once the candidate is
final, run the same canonical `run-all` command under the same dependency and
machine conditions, retain all five candidate reports and provenance, and
generate a complete comparison from embedded report data. If controls or
unchanged profiles show material drift, run a fresh baseline/candidate pair
sequentially and identify the authoritative pair. Ship only if the minimal
1 KB and 16 KB profiles show a repeatable material benefit without meaningful
regression elsewhere.

After the evidence decision, update established `1.4.29` release surfaces to
`1.4.30`, preserve the existing Unreleased JSON transport notes in the new
release section, document the optimization, compatibility, evidence,
limitations, retained paths, and reproduction commands, refresh lockfiles, and
build SDK, backend, and frontend release outputs.

Finally run every required local check from a clean full verification sequence,
prepare the PR summary and repository template body, publish the branch and a
ready PR, wait for CI and the first Codex review, and handle all actionable
feedback before handoff. Do not merge.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/3b61/relayna` unless a command explicitly
uses the temporary baseline worktree:

    make sync
    make -C studio/backend sync
    make -C apps/studio sync
    uv sync --extra benchmark
    uv run python -m benchmarks run-all

Focused iteration will use:

    uv run pytest -q <focused consumer test paths>
    uv run python -m benchmarks run consumer-processing --measurement all

Final validation will include:

    make format
    make lint
    make typecheck
    make test
    make -C studio/backend format
    make -C studio/backend lint
    make -C studio/backend typecheck
    make -C studio/backend test
    make -C apps/studio test
    make -C apps/studio build
    make build
    make -C studio/backend build
    uv run pytest -q <benchmark smoke test paths>
    uv run python -m benchmarks run-all
    bash .codex/skills/code-change-verification/scripts/run.sh
    git diff --check

GitHub publication and monitoring will use authenticated `gh`, the repository
PR template, `gh pr checks`, GitHub Actions log inspection for failures, and
review-thread inspection/replies through the repository's established scripts
or GraphQL API.

## Validation and Acceptance

A passing disabled-instrumentation test proves resource samplers and relevant
successful observation constructors receive zero calls with no sink/metrics.
Observation-only processing produces the same four successful events in the
same order and with equivalent task, context, timestamp, and sample data.
Metrics-only and combined processing retain two resource samples and expected
metrics. Representative success, rejection, exception, cancellation, lifecycle,
and retry tests preserve handler, ack, reject, and error counts.

The retained baseline and candidate directories each contain five independently
generated self-contained HTML reports whose hashes match their manifests. The
comparison HTML and JSON name every artifact and show every consumer-processing
cell, geometric means, absolute deltas, and all four control-benchmark drift
summaries. The comparison explicitly states that delivered-message/no-op
benchmark time is not broker or application end-to-end latency.

All local commands above pass after final edits. Release artifacts report
`1.4.30`; SDK, backend, frontend, dependency floor, and lock metadata agree.
Freeze API/schema manifests have no behavioral perimeter changes. Required
GitHub checks are green, the first Codex review is observed and fully handled,
all addressed actionable threads are replied to and resolved, and the PR
remains open and unmerged.

## Idempotence and Recovery

Dependency sync, focused tests, builds, and benchmark runs are safe to rerun.
Each historical benchmark executes from a clean source checkout and copies only
complete artifacts into a new retained run directory. Manifests bind files by
SHA-256, so a partial or overwritten artifact is detectable. If drift requires
a rerun, retain the earlier pair for auditability and mark the later sequential
pair authoritative rather than deleting or cherry-picking results.

Formatting commands may edit in-scope files; inspect the diff afterward and
rerun the full verification script from the beginning after every fix. Preserve
unrelated files and never update freeze manifests merely to satisfy a test. Git
commits and pushes are additive and recoverable; the PR must not be merged.

## Artifacts and Notes

Expected retained structure:

    reports/consumer-disabled-instrumentation/
      baseline/manifest.json
      baseline/*.html
      candidate/manifest.json
      candidate/*.html
      comparison.html
      comparison.json

The exact filenames and hashes will be recorded after execution.

## Interfaces and Dependencies

No public interface is added. The implementation may add private helpers or
private conditionals inside `TaskConsumer`, but must preserve all constructor
and method signatures. Tests may use monkeypatching and existing fakes; runtime
code must not depend on benchmark modules. Comparison generation should use
repository benchmark report data and standard-library parsing/rendering so no
duplicate historical `TaskConsumer` implementation remains.
