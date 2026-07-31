# Extract Message Metadata Once

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

Maintain this document in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Reduce Relayna consumer CPU overhead by extracting and normalizing delivered
AMQP message metadata once at the narrowest correct per-message boundary, then
passing a typed immutable internal value through the handler, retry,
idempotency, status, workflow, tracing, metrics, and observation paths that need
it. Released task behavior, header precedence and defaults, malformed and
missing metadata handling, middleware and handler context values, RabbitMQ
acknowledgement/rejection/requeue behavior, retry and idempotency keys,
workflow/status semantics, tracing and observation identifiers, and exception
behavior must remain unchanged.

The result is observable through deterministic behavioral and call-count tests
plus an immutable before/after run of all five registered benchmark suites. The
comparison will cover every benchmark cell, including delivered-message
per-message latency and consumer-loop throughput, and will report control drift
without selecting favorable runs.

## Progress

- [x] (2026-07-31 06:09Z) Inspected the initially clean detached worktree,
  fetched `origin`, confirmed the previous disabled-instrumentation optimization
  merged as PR #116, and created `codex/extract-message-metadata-once` from
  exact `origin/main` base `44adab85adbd7e8355e66742748c5b75178b0656`.
- [x] (2026-07-31 06:09Z) Read root `AGENTS.md`, `PLANS.md`, and the complete
  `$implementation-strategy`, `$production-freeze-guard`,
  `$code-change-verification`, and `$pr-draft-summary` skill instructions.
- [x] (2026-07-31 06:09Z) Identified the canonical command as
  `uv run --extra benchmark python -m benchmarks run-all` (or
  `make benchmark-all`) and enumerated the five registered suites:
  `consumer-processing`, `envelope-serialization`,
  `json-engine-evaluation`, `publish-preparation`, and `redis-storage-cpu`.
- [x] (2026-07-31 06:14Z) Captured a complete immutable baseline from the exact base commit with
  reproducibility metadata, checksums, report/case counts, and uniqueness
  validation.
- [x] (2026-07-31 06:17Z) Profiled and documented repeated metadata extraction in the real consumer
  processing path.
- [x] (2026-07-31 06:24Z) Implemented private typed immutable one-time
  extraction across task, workflow, and aggregation consumers and added focused
  behavioral/call-count tests.
- [x] (2026-07-31 06:29Z) Passed 129 focused consumer/benchmark tests and ran
  two explicitly exploratory consumer benchmark candidates plus a nearby
  focused baseline/candidate pair.
- [x] (2026-07-31 06:43Z) Re-ran all five canonical benchmark suites, rejected
  the first comparison because consumer-loop drift invalidated it, completed a
  fresh back-to-back full baseline/candidate pair, and generated checksum-bound
  machine-readable and standalone HTML comparison reports for all 408 cases.
- [x] (2026-07-31 06:47Z) Re-fetched unchanged `origin/main`, selected next
  available patch version `1.4.31`, updated all established SDK/Studio/frontend
  version, lock, and freeze surfaces, and updated changelog, release, runtime,
  and benchmark documentation.
- [x] (2026-07-31 06:51Z) Ran the complete mandatory Relayna verification stack
  through a clean final pass, plus frontend tests/build, package builds,
  benchmark artifact regeneration/integrity, standalone HTML parsing, runtime
  source-hash, freeze version-only, script lint/type, secret/path, and final-diff
  validation.
- [ ] Use `$pr-draft-summary`, commit and push intentional changes, open a
  ready-for-review PR, and babysit CI and the first Codex review until green
  with no unresolved actionable findings.

## Surprises & Discoveries

- Observation: the prior “eliminate disabled instrumentation work” branch was
  deleted from the remote because PR #116 is already merged into the exact
  starting commit.
  Evidence: fetched `origin/main` is `44adab85adbd` with subject
  `[codex] Improve disabled consumer instrumentation (#116)`.

- Observation: the latest Git release tag is `v1.4.29`, while the merged
  production branch already declares package version `1.4.30` and repository
  policy treats `v1.4.30` as the strict freeze perimeter.
  Evidence: `git tag -l 'v*' --sort=-v:refname` and version fields in
  `pyproject.toml`, `uv.lock`, and `apps/studio/package*.json`.

- Observation: the canonical suite currently has five registered benchmark
  types and `run-all` dispatches them in sorted order with canonical defaults.
  Evidence: `benchmarks/registry.py`, `benchmarks/cli.py`, `Makefile`, and
  `benchmarks/README.md`.

- Observation: one successful canonical 1 KB delivery performs four header
  extraction/copy operations in the minimal profile and five when observation
  delivery is enabled.
  Evidence: a deterministic patch-based call counter around the real
  `TaskConsumer._handle_message()` benchmark fixture reported
  `{"minimal": 4, "observability-enabled": 5}`. Static inspection locates the
  copies in tracing, retry-attempt normalization, received-event construction,
  and context construction.

- Observation: the authoritative baseline completed 408 unique measurements
  across all five suites, and every expected case appears exactly once.
  Evidence:
  `reports/extract-message-metadata-once/20260731T061143Z-44adab85/baseline/manifest.json`
  and `checksums.sha256` validate 40 consumer, 32 envelope, 192 JSON-engine, 72
  publish, and 72 Redis-storage cases.

- Observation: a frozen slotted dataclass that eagerly derived additional
  batch/manual-retry values and wrapped headers in a mapping proxy made focused
  consumer measurements roughly 5–11% slower.
  Evidence: the first exploratory candidate at
  `/private/tmp/extract-message-metadata-once-exploratory-1.html`.

- Observation: the lean private `NamedTuple` snapshot needs only headers,
  correlation ID, delivery tag, redelivery, content type, and normalized retry
  attempt. A deterministic call counter reports exactly one extraction in both
  minimal and observation-enabled successful paths.
  Evidence: focused patched `_extract_message_metadata` runs after the final
  implementation; static search finds no repeated message header/property
  extraction in downstream task/workflow/aggregation methods.

- Observation: the first full baseline/candidate comparison was invalidated by
  environmental drift: consumer-loop grouped deltas moved about `+8%`, with an
  individual cell at `+42%`, while the nearby focused pair was consistently
  faster.
  Evidence: immutable non-authoritative run
  `20260731T061143Z-44adab85` and focused pair outputs under `/private/tmp/`.

- Observation: the fresh back-to-back full pair kept unchanged benchmark-family
  geometric means within `-2.03%` to `+1.24%` while minimal per-message and loop
  latency improved `4.05%` and `4.19%`.
  Evidence: `reports/extract-message-metadata-once/20260731T063554Z-44adab85-paired/comparison/`.

- Observation: a final fetch showed `origin/main` still at exact starting SHA
  `44adab85`, with package version `1.4.30`; no other branch or changelog entry
  had reserved `1.4.31`.
  Evidence: fetched ref, repository version surfaces, and changelog immediately
  before the version update.

- Observation: the first Codex review found three reusable-tooling weaknesses:
  comparison wording forced a successful outcome, pair validation did not prove
  matching environments, and host detection hard-coded the original macOS
  machine.
  Evidence: PR #117 review threads `r3688630340`, `r3688630345`, and
  `r3688630350`.

## Decision Log

- Decision: use exact base `44adab85adbd7e8355e66742748c5b75178b0656`
  rather than stacking on any unmerged performance branch.
  Rationale: it is both the worktree's default-branch starting commit and
  fetched `origin/main`; it includes PR #116 while keeping this optimization
  independently reviewable.
  Date/Author: 2026-07-31 / Codex.

- Decision: treat the implementation as an internal, behavior-preserving
  refactor at the current `v1.4.30` production perimeter, compared against the
  latest released tag `v1.4.29`.
  Rationale: the requested optimization should not require a public export,
  signature, schema, configuration, persisted-data, or wire-format change.
  The user's explicit freeze authorization remains available if evidence shows
  a perimeter change is unavoidable, but freeze manifests will not be edited
  merely to satisfy tests.
  Date/Author: 2026-07-31 / Codex.

- Decision: retain this task's benchmark evidence under a unique timestamped
  `reports/extract-message-metadata-once/<run-id>/` directory.
  Rationale: baseline, candidate, and comparison artifacts must be immutable,
  unambiguous, checksum-bound, and impossible to append or overwrite during
  reruns.
  Date/Author: 2026-07-31 / Codex.

- Decision: use a lean private `_MessageMetadata` `NamedTuple` and normalize
  only fields that were actually duplicated.
  Rationale: it keeps the internal carrier typed and assignment-immutable while
  avoiding the allocation and eager-normalization regression measured in the
  first prototype. Headers are a delivery-scoped copied snapshot exposed
  internally as `Mapping`; every downstream mutable context or retry header set
  receives its own `dict` copy.
  Date/Author: 2026-07-31 / Codex.

- Decision: retain but exclude the first full candidate from release claims,
  and use run `20260731T063554Z-44adab85-paired` as authoritative.
  Rationale: the requested drift rule forbids presenting the first misleading
  comparison. The replacement ran exact baseline and candidate source states
  back-to-back and shows bounded unchanged-suite aggregate drift.
  Date/Author: 2026-07-31 / Codex.

- Decision: release the retained implementation as `1.4.31` and advance all
  freeze manifests by version only.
  Rationale: freshly fetched main already owns `1.4.30`; `1.4.31` is the next
  unused patch. The user explicitly approved crossing the production perimeter,
  while the implementation itself changes no public surface, route, schema,
  configuration, persisted data, or wire protocol.
  Date/Author: 2026-07-31 / Codex.

- Decision: preserve the original derived `comparison/` directory and publish
  `comparison-reviewed/` as the final derived report.
  Rationale: benchmark measurements and retained evidence are immutable.
  Review hardening belongs in a new checksum-bound derived artifact. The final
  generator rejects host/interpreter/control/package/timestamp mismatches and
  reports meaningful improvement, meaningful regression, or inconclusive based
  on both minimal target groups relative to maximum unchanged-suite aggregate
  drift.
  Date/Author: 2026-07-31 / Codex.

## Outcomes & Retrospective

The implementation now extracts a delivery-scoped metadata snapshot exactly
once at the start of task, workflow, and aggregation processing and passes it
through tracing, context, retry/DLQ, metrics, and observation paths. Focused
behavior and call-count tests pass. Version `1.4.31`, version-only freeze
advances, changelog, release guidance, benchmark methodology, and retained
reports are complete.

The authoritative 408-case pair measures `-4.05%` minimal per-message latency,
`-4.19%` minimal consumer-loop latency, and `-5.38%` over all 40 consumer cells;
35 cells improved. Four unchanged families moved between `-2.03%` and `+1.24%`.
The earlier drifted complete run remains immutable but explicitly
non-authoritative.

The first Codex review produced three actionable benchmark-tooling findings.
All three are fixed with six focused regression tests. The mandatory stack then
passed again with 667 SDK tests passed and 1 skipped plus 244 Studio backend
tests. `comparison-reviewed/` regenerated all 408 cases and retained the same
meaningful-improvement assessment through derived, neutral-capable logic.

Remaining work is commit/push of the review fixes, thread replies/resolution,
and the resulting CI terminal state. The PR stays open and unmerged.

## Context and Orientation

Relayna's public SDK lives under `src/relayna/`. The target path begins after
RabbitMQ delivery in `src/relayna/consumer/task_consumer.py`. That consumer
decodes a task or batch envelope, resolves AMQP properties and headers,
constructs `TaskHandlerContext`, invokes middleware and the handler, applies
retry/DLQ/status policies, emits optional observations and metrics, and
acknowledges or rejects the message. Related internal context structures live
under `src/relayna/consumer/context.py`; workflow consumption is under
`src/relayna/consumer/workflow_consumer.py`.

SDK tests live under `tests/`. The benchmark framework under `benchmarks/`
registers five suites and writes self-contained HTML to `reports/`.
`consumer-processing` specifically measures per-message CPU latency and a
consumer-loop throughput path after AMQP delivery with a no-op handler. It is
not broker, network, business-handler, or end-to-end application latency.

The retained task evidence will use one unique run root with `baseline/`,
`candidate/`, and `comparison/` siblings. Each execution manifest will record
source commit, branch, dirty state, UTC timestamps, exact command and options,
Python/uv/platform/CPU details, dependency and lock checksums, environment
controls, report filenames, byte sizes, checksums, expected and observed case
counts, and uniqueness validation.

## Compatibility Boundary

Compatibility boundary: latest release tag `v1.4.29`; repository production
freeze perimeter and starting package version `v1.4.30`. Preserve all released
public APIs and signatures, RabbitMQ message and header precedence/default
semantics, malformed/missing metadata behavior, retry/idempotency identity,
middleware and handler context contents, acknowledgements/rejections/requeues,
status/workflow/observability/tracing semantics, exception behavior, persisted
data, route shapes, and wire formats. Prefer a private immutable internal type
and direct refactor with no compatibility shim. No cross-message cache may be
introduced, and the metadata value must not retain payload bodies.

## Plan of Work

First capture a clean baseline from the exact base revision before modifying
runtime behavior. Synchronize only if required, preserve lockfiles, run the
canonical full suite once in a temporary detached worktree, and copy only
completed reports into the unique retained baseline directory. Produce a
machine-readable manifest/checksum index from unedited report data and validate
that all expected benchmark cases occur exactly once.

Then inspect and profile the real consumer-processing call graph. Inventory
every properties/header lookup and conversion for task, correlation, causation,
workflow, routing, attempt, trace, and observation identifiers. Establish
duplicate work using static call-path evidence and deterministic call-count or
profiling evidence before editing the runtime.

Add the smallest private immutable representation at the earliest boundary
shared by downstream consumers. Extract and normalize once per delivered
message, pass the value through internal CPU-path methods, and delete redundant
lookups/conversions. Reuse existing types and package ownership. Add focused
tests for one extraction, success, missing/malformed metadata, retry/error,
middleware, metrics/observation/tracing combinations, and individual and
consumer-loop or batch paths as applicable.

Run focused tests and clearly labeled exploratory consumer benchmarks while
iterating. After the implementation is final and focused tests pass, run all
five benchmarks with the same interpreter, dependency state, commands,
repetitions, warmups, and environmental controls as the baseline. Validate a
candidate manifest identically, then generate machine-readable JSON and
standalone HTML comparison reports for every cell and aggregate, including
absolute/percentage deltas, available distributions, throughput/latency,
variance/noise, regressions, metadata, and interpretation. If drift makes the
comparison invalid, retain the invalidated evidence and run a fresh sequential
paired baseline/candidate.

Immediately before finalization, fetch `origin/main`, verify whether its version
or relevant runtime changed, update/rebase cleanly if necessary, and select the
next unused patch version. Update every established SDK, Studio backend,
frontend, lock/generated, changelog, release, consumer, and benchmark
documentation surface without unsupported performance claims.

Finally execute `$code-change-verification` from the beginning until clean,
plus relevant frontend, benchmark, artifact, HTML, diff, secret, path, and
freeze checks. Use `$pr-draft-summary`, stage only intentional files, commit
with a concise conventional message, push, and open a ready PR from the
repository template. Monitor required CI and request the first Codex review
using the repository mechanism. Address, verify, reply to, and resolve every
actionable thread; leave user-only approvals to the user; do not merge.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/bca4/relayna` unless a temporary detached
benchmark worktree is explicitly named:

    git status --short --branch
    git fetch --prune origin
    uv sync --extra benchmark
    uv run --extra benchmark python -m benchmarks list
    uv run --extra benchmark python -m benchmarks run-all

Focused implementation commands will include:

    uv run pytest -q <focused consumer and benchmark tests>
    uv run --extra benchmark python -m benchmarks run consumer-processing \
      --output <unique exploratory path>

Final validation will include:

    bash .codex/skills/code-change-verification/scripts/run.sh
    make -C apps/studio test
    make -C apps/studio build
    uv run pytest -q tests/test_*benchmark*.py
    uv run --extra benchmark python -m benchmarks run-all
    git diff --check

GitHub publication and monitoring will use authenticated `gh`, the repository
PR template, GitHub Actions check inspection, the established Codex review
request mechanism, GraphQL review-thread inspection/replies, and no merge
operation.

## Validation and Acceptance

Deterministic tests must prove exactly one extraction per delivered message and
equivalent externally meaningful behavior for success, missing/malformed
metadata, retry/error, middleware, metrics/observations/tracing, and relevant
batch/loop paths. Public API/freeze manifests and serialized/wire values remain
unchanged unless an explicitly documented, authorized perimeter update proves
unavoidable.

Baseline and candidate each contain five independently generated self-contained
HTML reports, a machine-readable provenance/checksum manifest, a complete
expected case inventory, and a uniqueness proof. The comparison JSON and HTML
cover every benchmark cell and clearly distinguish meaningful target-path
changes from local noise and control drift. HTML validation proves embedded
complete datasets and no machine-specific absolute paths or secrets.

All mandatory SDK and Studio backend formatting, linting, type checking, and
full tests pass in sequence after final changes; relevant frontend and benchmark
checks pass; the final diff contains only intended artifacts and source/docs.
The ready PR has green CI and its first Codex review has no unresolved
actionable findings. The PR remains open and unmerged.

## Idempotence and Recovery

Each benchmark attempt uses a fresh run ID and first writes to a temporary
directory. Completed reports are copied into a previously nonexistent retained
directory and checksummed; reruns never append to or overwrite older evidence.
Partial attempts remain clearly labeled or are excluded from the retained
manifest without editing measurements.

Dependency sync, tests, builds, formatters, and comparison generation are safe
to rerun. Formatting changes require diff inspection. If `origin/main` moves,
fetch and rebase only after preserving immutable benchmark evidence; rerun a
paired baseline/candidate whenever source or environment drift would invalidate
the quantitative comparison. Never reset or overwrite unrelated user work.

## Artifacts and Notes

Expected retained structure:

    reports/extract-message-metadata-once/<run-id>/
      baseline/manifest.json
      baseline/*.html
      candidate/manifest.json
      candidate/*.html
      comparison/comparison.json
      comparison/comparison.html

Exact run IDs, case counts, checksums, commands, and results will be recorded
after execution.

The first run ID `20260731T061143Z-44adab85` is retained as
non-authoritative drift evidence. The authoritative back-to-back run is
`20260731T063554Z-44adab85-paired`; each baseline/candidate side has five exact
standalone HTML reports, five machine-readable raw-result sidecars,
`manifest.json`, and `checksums.sha256`. Its comparison directory has complete
JSON and standalone HTML covering all 408 unique cases, plus its own manifest
and checksum index.

## Interfaces and Dependencies

No new public interface is planned. Runtime code may add a private frozen
dataclass, named tuple, or equivalent internal value under the existing
consumer package. It must exclude payload/body ownership and be instantiated
once per delivered message. Internal method signatures may change together
within the package, while exported constructors and handler/middleware contracts
remain stable. Benchmark and comparison tooling stays outside `src/relayna/`
and must not provide an alternate production implementation.
