# Relayna CPU-Side JSON Engine Evaluation

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with
`/Users/jobz/.codex/worktrees/4452/relayna/PLANS.md`.

## Purpose / Big Picture

Relayna contributors first needed evidence for a JSON-engine decision, then
explicitly approved implementing the best justified strategy across the
`v1.4.29` production freeze boundary. This plan therefore covers both the
completed study and its production follow-up.

The benchmark measures complete CPU-side outbound and inbound paths for
`TaskEnvelope` and `BatchTaskEnvelope` messages at exactly 1 KB, 16 KB, 128 KB,
and 1 MB. It compares the released staged standard-library path, raw Pydantic
Core JSON, model-aware Pydantic direct JSON, and orjson across ASCII-heavy and
Unicode/numeric profiles. The production follow-up replaces AMQP transport JSON
encoding and parsing with an internal Pydantic Core codec, while retaining
Relayna model preparation, alias normalization, envelope validation, rejection
classification, canonical hashing, deduplication inputs, and persisted JSON
formats.

The benchmark excludes RabbitMQ, Redis, and network latency. Success is
observable through deterministic compatibility/migration tests, updated
freeze-perimeter coverage, the full repository verification stack, and an
updated self-contained report comparing the released reference with the new
production path.

## Progress

- [x] (2026-07-29 09:29Z) Created and checked out
  `perf/json-engine-evaluation`; confirmed the worktree was clean before edits.
- [x] (2026-07-29 09:29Z) Read `AGENTS.md`, `PLANS.md`, the benchmark
  framework, current transport/consumer/alias paths, and mandatory verification
  and PR-summary skill instructions.
- [x] (2026-07-29 09:29Z) Established an additive benchmark/tooling boundary
  with no production runtime, API, wire, persistence, or freeze-manifest edits.
- [x] (2026-07-29 09:46Z) Implemented deterministic exact-size fixtures, four
  engine paths, canonical and alias-compatible inbound variants, the 192-cell
  performance matrix, P25–P75 dispersion, and explicit missing-orjson handling.
- [x] (2026-07-29 09:46Z) Implemented 53 deterministic compatibility findings,
  verified packaging/wheel evidence, path-specific recommendations, a proposed
  follow-up shape, and the self-contained HTML decision report.
- [x] (2026-07-29 09:46Z) Added the pinned opt-in `benchmark` extra, lockfile
  entry, CLI registration/documentation, and 24 deterministic benchmark tests
  without adding orjson to production dependencies.
- [x] (2026-07-29 09:46Z) Ran focused tests, SDK formatting/lint/typecheck/full
  tests, the real canonical benchmark, desktop/mobile browser QA, and the
  mandatory `$code-change-verification` stack successfully.
- [x] (2026-07-29 09:46Z) Audited the final scope, restored an incidental
  Studio lock metadata refresh, confirmed the original benchmark CLI remains
  usable, updated this plan, and prepared the `$pr-draft-summary` handoff
  without committing, pushing, or opening a pull request.
- [x] (2026-07-29 10:00Z) Received explicit user approval to cross the
  `v1.4.29` production freeze for this JSON transport optimization; read and
  applied `$implementation-strategy` and `$production-freeze-guard`.
- [x] (2026-07-29 10:00Z) Reassessed the strategy and selected combined
  Pydantic Core transport encoding/parsing over orjson: material complete-path
  gains without a new production dependency or orjson’s huge-integer and JSON
  domain breaks.
- [x] (2026-07-29 10:55Z) Added the private Pydantic Core production transport
  codec and migrated all scoped AMQP publishers/consumers while leaving
  persistence and canonical serializers unchanged.
- [x] (2026-07-29 10:55Z) Added the accepted-domain migration guide, changelog
  and docs navigation, ten transport-domain tests, real invalid-UTF-8 consumer
  classification coverage, and an intentional transport JSON entry in the SDK
  freeze feature manifest.
- [x] (2026-07-29 10:55Z) Bound benchmark Pydantic Core cases to the exact
  production helpers, expanded compatibility to 61 findings and production
  wheel evidence, reran all 192 cases, and regenerated the report.
- [x] (2026-07-29 10:55Z) Passed focused migration/freeze tests, strict docs
  build, full SDK and Studio checks, mandatory `$code-change-verification`, and
  desktop/mobile browser QA; collected final `$pr-draft-summary` inputs.

## Surprises & Discoveries

- Observation: The current outbound AMQP-body helper receives a prepared
  mapping and runs `json.dumps(payload, ensure_ascii=False).encode("utf-8")`;
  model publishers prepare `BaseModel` values with
  `model_dump(mode="json", exclude_none=True)`.
  Evidence: `src/relayna/rabbitmq/client.py`.

- Observation: The task consumer decodes bytes using UTF-8 replacement, parses
  with `json.loads`, normalizes configured aliases, then chooses and validates a
  task or batch envelope. This makes replacement-character behavior, rejection
  stage, alias fallback, and batch task alias handling part of the benchmark’s
  compatibility analysis.
  Evidence: `src/relayna/consumer/task_consumer.py` and
  `src/relayna/contracts/aliases.py`.

- Observation: The branch already contains a reusable repository-only benchmark
  registry, atomic HTML writer, environment capture, deterministic fixed-size
  envelope fixtures, and a tested `uv run python -m benchmarks` CLI.
  Evidence: `benchmarks/`, `tests/test_benchmark_cli.py`, and
  `tests/test_envelope_microbenchmarks.py`.

- Observation: Pydantic Core was the aggregate winner for both measured inbound
  paths on this machine: 7.00× current for canonical payloads and 7.14× for
  alias-compatible payloads. Direct Pydantic was also fast for canonical bytes
  but pays for a failed validation plus fallback on alias payloads.
  Evidence: the canonical 192-case run in
  `reports/json-engine-evaluation.html`.

- Observation: orjson was the measured outbound winner at 17.20× current, and
  Pydantic Core was the no-new-dependency outbound winner at 6.07×. All compact
  candidates changed current wire bytes.
  Evidence: aggregate and per-case tables plus outbound equivalence probes in
  `reports/json-engine-evaluation.html`.

- Observation: orjson rejects outbound integers beyond 64 bits and parses the
  tested inbound `2**100` value as a float; it also rejects invalid UTF-8,
  non-finite input tokens, and raw mappings with non-string keys where the
  current stdlib path accepts them. Direct Pydantic converts non-finite outbound
  model values to `null`, while raw Pydantic Core preserves the current
  non-standard tokens.
  Evidence: deterministic compatibility findings and focused assertions in
  `tests/test_json_engine_evaluation.py`.

- Observation: all requested orjson 3.11.9 platform targets have published
  wheels. macOS x86_64 is covered through universal2 wheels, while dedicated
  ARM64 wheels are also present.
  Evidence: PyPI release JSON for orjson 3.11.9 and the artifact table embedded
  in the report.

- Observation: the first mandatory verification attempt stopped when the fresh
  Studio backend environment lacked Ruff. Syncing Studio dev dependencies and
  rerunning the complete script passed. The sync temporarily refreshed two
  root-extra metadata lines in `studio/backend/uv.lock`; the final scope audit
  restored that unrelated lockfile exactly.
  Evidence: verification transcripts and final clean diff for
  `studio/backend/uv.lock`.

- Observation: the generated 95,681-byte report is mode `0644`, self-contained,
  and readable at 1440×1000 and 390×844. The first local-server load emitted
  only an unsolicited favicon 404; the reloaded report had zero console errors
  or external resource requests.
  Evidence: headed Playwright snapshots/screenshots and local server transcript.

- Observation: the exact implemented production helper measured 6.90× current
  on canonical inbound, 7.02× on alias-compatible inbound, and 5.88× on
  outbound across the canonical aggregate. orjson remained faster outbound at
  15.75× but did not win the compatibility/dependency decision.
  Evidence: final 192-case report at
  `reports/json-engine-evaluation.html`.

- Observation: Pydantic Core changes additional raw mapping-key behavior:
  `None` encodes as `"None"` instead of stdlib's `"null"`, and a tuple such as
  `("x", "y")` encodes as `"x,y"` where stdlib rejects it.
  Evidence: deterministic transport tests and expanded 61-finding
  compatibility table.

- Observation: Pydantic Core 2.41.5 publishes CPython 3.13 and 3.14 wheels for
  Linux x86_64/aarch64 and macOS x86_64/ARM64, matching Relayna's supported
  production targets without a new package.
  Evidence: PyPI release JSON and the final report's packaging table.

- Observation: Studio backend `uv run` refreshes the editable root package's
  optional-extra metadata in `studio/backend/uv.lock`, even though Studio does
  not consume the benchmark extra. Both verification runs passed, and the final
  audit restored those two unrelated generated lines exactly.
  Evidence: final clean diff for `studio/backend/uv.lock`.

## Decision Log

- Decision: Add a separate `json-engine-evaluation` benchmark rather than
  expanding or replacing `envelope-serialization`.
  Rationale: The existing benchmark remains a compact current-versus-Pydantic
  microbenchmark, while the new study can own its larger performance,
  compatibility, packaging, and recommendation model without breaking its CLI
  or report.
  Date/Author: 2026-07-29 / Codex.

- Decision: Keep all candidate imports and implementations under `benchmarks/`
  and tests, with an opt-in benchmark dependency for orjson.
  Rationale: The latest release and strict production-freeze boundary is
  `v1.4.29`. Additive repository tooling does not alter released public APIs,
  production dependencies, wire bytes, persisted data, contracts, routes, or
  Studio behavior, so the intended work does not require
  `$implementation-strategy` or `$production-freeze-guard`.
  Date/Author: 2026-07-29 / Codex.

- Decision: Treat the current staged path as the baseline and time raw
  `pydantic_core` separately from model-aware Pydantic JSON.
  Rationale: Raw parsing/encoding can expose engine potential, but it is not
  semantically equivalent to model-aware preparation or validation. The report
  must prevent raw speed from being misread as an implementation-ready result.
  Date/Author: 2026-07-29 / Codex.

- Decision: Pin `orjson==3.11.9` in a new optional `benchmark` extra and make
  benchmark Make targets opt into that extra.
  Rationale: This is the narrowest reproducible contributor mechanism in the
  current project layout. It locks the native benchmark candidate without
  adding it to production `[project].dependencies`; the CLI can still produce
  an explicitly partial report when the optional engine is absent.
  Date/Author: 2026-07-29 / Codex.

- Decision: Recommend a future inbound-only Pydantic Core parser substitution,
  preserving Relayna alias normalization and retaining the staged current path
  as a fallback for invalid UTF-8 and error-classification edge cases.
  Rationale: Pydantic Core won both inbound aggregates materially, already ships
  through Pydantic, and preserved huge integers and non-finite input behavior in
  the tested cases. Outbound candidates change wire bytes, while orjson adds a
  dependency and material semantic differences.
  Date/Author: 2026-07-29 / Codex.

- Decision: Keep outbound transport, storage, canonical hashing, and
  deduplication serializers unchanged in the recommendation.
  Rationale: No candidate produced byte-for-byte current output. Performance
  alone cannot authorize a wire, persisted, signature, or hash-input change.
  Date/Author: 2026-07-29 / Codex.

- Decision: Reopen the completed study as an explicitly approved production
  perimeter change against `v1.4.29`.
  Rationale: The user explicitly authorized JSON runtime, wire bytes/semantics,
  dependencies, and relevant freeze-manifest changes, narrowly scoped to this
  optimization. `$implementation-strategy` and `$production-freeze-guard` were
  applied before runtime edits.
  Date/Author: 2026-07-29 / Codex.

- Decision: Implement Pydantic Core for both outbound AMQP JSON encoding and
  inbound AMQP JSON parsing; do not add orjson to production.
  Rationale: The complete-path study measured Pydantic Core at approximately
  6× outbound and 7× inbound aggregate speedups. It already ships through
  Pydantic, preserves tested arbitrary-size integers, accepts the current
  non-finite tokens, and stringifies current non-string mapping keys. orjson is
  faster outbound but adds a native production dependency, rejects outbound
  integers beyond 64 bits, loses precision on inbound huge integers, rejects
  non-finite input, and rejects non-string keys. The marginal outbound gain
  does not justify those extra breaks.
  Date/Author: 2026-07-29 / Codex.

- Decision: Define the new transport domain as strict UTF-8 Pydantic Core JSON,
  while keeping aliases and malformed-versus-invalid classification stable.
  Rationale: Invalid UTF-8 will now be rejected as malformed JSON instead of
  being silently replaced with U+FFFD. Valid malformed syntax continues to fail
  in the parse stage; valid JSON with an invalid envelope continues to fail in
  model validation. `documentId` and configured aliases continue through the
  existing normalization stage.
  Date/Author: 2026-07-29 / Codex.

- Decision: Update only `tests/freeze/feature_perimeter.json` by adding an
  explicit transport JSON domain feature linked to migration tests.
  Rationale: Public exports and route declarations do not change. The feature
  manifest should intentionally record the newly approved released behavior
  boundary rather than altering unrelated public-surface or route manifests.
  Date/Author: 2026-07-29 / Codex.

## Outcomes & Retrospective

The repository now has a registered `json-engine-evaluation` benchmark with a
192-cell canonical matrix spanning four engines, two envelope kinds, two
profiles, four exact current-wire targets, complete outbound work, and canonical
plus alias-compatible inbound work. It reports median latency, P25–P75
dispersion, operations/second, MiB/second, actual bytes, iteration/repeat counts,
and current-relative speedups. Fifty-three untimed deterministic findings make
compatibility and rejection-stage differences explicit.

The real canonical run generated
`/Users/jobz/.codex/worktrees/4452/relayna/reports/json-engine-evaluation.html`.
On CPython 3.13.2 with Pydantic 2.12.5, Pydantic Core 2.41.5, and orjson 3.11.9,
Pydantic Core won canonical inbound at 7.00× and alias-compatible inbound at
7.14× aggregate current speed. orjson won outbound at 17.20×, while Pydantic
Core was the 6.07× no-new-dependency outbound winner. The decision remains
inbound-only: outbound compact bytes differ, and orjson’s huge-integer, UTF-8,
non-finite, and mapping-key behavior blocks a speed-only switch.

The optional benchmark extra makes orjson installation reproducible without
changing production dependencies. Exact CPython 3.13/3.14 wheel coverage for
the requested Linux and macOS architectures is embedded in the report. The
original `uv run python -m benchmarks` discovery and envelope benchmark both
remain usable.

All verification passed. Focused benchmark/CLI tests reported 42 passed. The
explicit SDK stack and the mandatory rerun both passed formatting, linting,
type checking, and 461 SDK tests with one existing skip. The mandatory Studio
half passed formatting, linting, type checking, and 244 tests. The report passed
desktop/mobile headed-browser QA. No production source, public API, contract,
wire/persisted runtime behavior, Studio source, Studio lockfile, or production
freeze manifest changed.

The completed benchmark-only outcome above is now the baseline for an approved
production follow-up.

The follow-up implements a private `relayna._transport_json` codec using
Pydantic Core for Relayna-owned AMQP bodies. RabbitMQ client publishers,
consumer retry/batch publishing, DLQ override replay, and task, aggregation,
workflow, status-hub, and status-history parsing now use the codec. Outbound
bytes are compact and inbound UTF-8 is strict. Model preparation, aliases,
envelope validation, malformed-versus-invalid rejection staging, huge integers,
and non-finite tokens remain covered.

The intentionally accepted breaks are documented in
`docs/json-transport-migration.md`: whitespace/raw wire bytes; invalid UTF-8
replacement becoming `malformed_json`; parser exception types/text; `None`
mapping keys changing from `"null"` to `"None"`; and additional tuple/datetime/
UUID key stringification. Rolling upgrades remain interoperable for valid JSON.
Original DLQ body replay, Redis/persisted JSON, canonical status-event hashes,
workflow signatures, deduplication inputs, HTTP/SSE JSON, public exports,
routes, and schemas remain unchanged.

Only `tests/freeze/feature_perimeter.json` changed among freeze manifests. It
intentionally records the approved transport JSON domain and points to real
migration and consumer behavior tests. Public-surface, route, Studio backend,
and Studio frontend freeze manifests remain unchanged.

The final canonical run produced 192 measurements and 61 compatibility
findings. The implemented Pydantic Core path measured 6.90× canonical inbound,
7.02× alias-compatible inbound, and 5.88× outbound aggregate speedups versus
the released v1.4.29 stdlib reference. orjson measured 15.75× outbound but
remains benchmark-only because the chosen path avoids its new dependency,
huge-integer loss/rejection, non-finite rejection, and key restrictions.

Verification is complete: 159 focused migration/benchmark/consumer/freeze
tests passed; the explicit production freeze run passed four tests; the strict
documentation build passed; SDK format, lint, and type checking passed with 472
tests and one existing skip; Studio backend format, lint, and type checking
passed with 244 tests; and the mandatory `$code-change-verification` workflow
passed end to end. The 101,962-byte mode-`0644` report passed desktop and mobile
headed-browser QA; the only first-load console event was the local server's
unsolicited favicon 404.

## Context and Orientation

The Relayna SDK lives under
`/Users/jobz/.codex/worktrees/4452/relayna/src/relayna`. `TaskEnvelope` and
`BatchTaskEnvelope` are Pydantic transport models defined in
`src/relayna/contracts/task.py`. The current AMQP outbound preparation and JSON
encoding path is in `src/relayna/rabbitmq/client.py`; the inbound parse, alias
normalization, envelope classification, and validation path is in
`src/relayna/consumer/task_consumer.py`. `documentId` is the built-in compatible
payload alias for `task_id`, implemented in
`src/relayna/contracts/aliases.py`.

Repository-only benchmark code lives under `benchmarks/`. The package entry
point and registry expose benchmark types through
`uv run python -m benchmarks`; benchmark artifacts live under `reports/`, and
focused deterministic tests live under `tests/`. “Complete outbound CPU path”
means model or mapping preparation, JSON encoding, and final AMQP-ready bytes.
“Complete inbound CPU path” means starting from message bytes and ending with a
validated envelope after any required Relayna alias normalization.

## Compatibility Boundary

Compatibility boundary: released and strict production-freeze tag `v1.4.29`.
The user explicitly approved crossing this boundary for Relayna’s JSON
transport encoding/parsing optimization. `$implementation-strategy` and
`$production-freeze-guard` establish the following narrow perimeter:

- AMQP transport JSON emitted by Relayna becomes compact Pydantic Core output.
- AMQP inbound JSON becomes strict UTF-8 Pydantic Core parsing.
- Arbitrary-size integers, non-finite tokens, and non-string mapping keys remain
  accepted as described in the migration guide.
- Built-in/configured aliases and malformed-JSON versus invalid-envelope
  rejection stages remain behaviorally stable.
- Canonical hash/dedup inputs, Redis/persisted JSON, SSE/API JSON, public
  exports, contracts, routes, topology, and Studio behavior remain unchanged.
- `tests/freeze/feature_perimeter.json` changes intentionally to record the new
  transport JSON behavior tests. Public-surface and route freeze manifests do
  not change.

## Plan of Work

Add one registered benchmark module under `benchmarks/`. Build deterministic
task and two-task batch fixtures for ASCII-heavy and Unicode/numeric profiles.
Calibrate each fixture so the current stdlib outbound path is exactly 1,024,
16,384, 131,072, or 1,048,576 bytes, and record every candidate’s actual
serialized size. Construct outbound operations for current stdlib, raw
`pydantic_core.to_json`, `BaseModel.model_dump_json`, and `orjson.dumps`.
Construct canonical and alias-compatible inbound operations for current staged
parse/normalize/validate, raw `pydantic_core.from_json` plus the required
Relayna stages, direct `model_validate_json` where aliases do not require a
fallback, and `orjson.loads` plus normalization and validation.

Use fixed per-size iterations with configurable repeats and profile selection.
Warm every operation, rotate case order between repeats, disable garbage
collection only inside timed loops, and report median latency, a useful
percentile or range-based dispersion measure, operations/second, MiB/second,
iteration/repeat counts, actual bytes, and speedup versus the current path.
Validate semantic fairness before timing.

Implement deterministic compatibility probes outside timed loops. Cover exact
outbound bytes and parsed semantics; Unicode, valid and invalid UTF-8; configured
and built-in aliases; integers beyond 64 bits; non-finite floats; non-string
keys; malformed JSON versus valid invalid envelopes; datetime, UUID, and model
values after current preparation; and canonical hashing exclusions. Record
acceptance/rejection, output, exception class, and rejection stage so semantic
differences are explicit rather than inferred from speed.

Render one stable, self-contained HTML decision report with methodology,
performance, compatibility, packaging, environment, recommendations, proposed
future implementation shape, limitations, and next-benchmark sections. Provide
separate recommendations for canonical inbound, alias-compatible inbound,
outbound transport, canonical/storage inputs, and the dependency decision.

Declare orjson through an opt-in benchmark-only dependency mechanism and update
the lockfile without adding it to production runtime dependencies. Document the
exact reproducible command, optional-engine behavior, current package versions,
and verified wheel availability for CPython 3.13/3.14 on Linux x86_64/aarch64
and macOS ARM64/x86_64.

Register the benchmark, document its quick and canonical runs, and add focused
tests for fixture sizes, matrix completeness, fairness, compatibility findings,
missing-orjson handling, calculations, CLI dispatch, and HTML content. Timing
tests will assert only positive computed values, never performance thresholds.

For the production follow-up, add a private SDK transport codec backed by
`pydantic_core.to_json` and `pydantic_core.from_json`. Route all Relayna-owned
AMQP body encoding through it: RabbitMQ client publishers, consumer retry/batch
publishing, and DLQ override replay. Route all Relayna-owned AMQP body parsing
through it: task, workflow, status hub, and status history consumers, including
task-type inspection. Do not replace storage, canonical hashing, SSE, API, or
diagnostic body-inspection JSON paths.

Add deterministic tests for the new accepted domain and migration breaks:
compact bytes, Unicode, strict invalid UTF-8 rejection, exact huge integers,
accepted `NaN`/`Infinity`/`-Infinity`, non-string-key conversion, aliases,
malformed syntax, valid invalid envelope classification, and unchanged
canonical/persisted bytes. Update only the SDK feature-perimeter freeze manifest
with an intentional transport JSON feature entry. Add a migration guide that
states every wire and acceptance change and operational rollout implication.

Extend the benchmark with an implemented-production label/path and report
section that compares the released stdlib reference to the exact private codec
used by production. Rerun the canonical benchmark and update the report’s
executive decision from proposal to implemented strategy.

## Concrete Steps

Run from `/Users/jobz/.codex/worktrees/4452/relayna`:

    uv lock
    uv sync --extra dev --extra benchmark
    uv run --extra benchmark pytest tests/test_json_engine_evaluation.py tests/test_benchmark_cli.py
    uv run --extra benchmark python -m benchmarks run json-engine-evaluation
    uv run pytest tests/test_json_transport.py tests/test_consumer.py tests/test_status_hub.py tests/test_status_history.py tests/test_production_freeze_surface.py
    make format
    make lint
    make typecheck
    make test
    bash .codex/skills/code-change-verification/scripts/run.sh

The post-implementation canonical benchmark must complete with a success line naming the absolute
path to `reports/json-engine-evaluation.html`. Verification commands are safe
to rerun. No RabbitMQ, Redis, Studio server, or external service is required.

## Validation and Acceptance

Acceptance requires both envelope kinds, all four exact target sizes, both
payload profiles, all semantically applicable engine/path combinations, and
separate canonical and alias-compatible inbound results. Every performance row
must include actual bytes, median latency, dispersion, operations/second,
MiB/second, iterations, repeats, and current-relative speedup. Tests must prove
the matrix has every intended cell once and that equivalent paths produce
equivalent validated models before timing.

The compatibility table must deterministically cover every requested edge case
and preserve exception/rejection-stage evidence. The report must clearly mark
byte differences and prevent storage/canonical hashing replacement advice
unless bytes are identical. It must state exact package versions, the
benchmark-only dependency mechanism, target wheel support, path-specific
recommendations, a proposed but unimplemented follow-up shape, and the next
benchmark matrix and rationale.

The existing `envelope-serialization` CLI and report path must remain usable.
Focused migration and freeze tests, SDK formatting/linting/type checking/full
tests, Studio backend checks, and the complete mandatory verification script
must pass. Runtime diffs must stay inside the approved AMQP transport scope.
Only `tests/freeze/feature_perimeter.json` may change among freeze manifests,
and it must point to real behavior tests.

## Idempotence and Recovery

Fixture generation, compatibility probes, and benchmark runs are deterministic
apart from measured time and environment timestamps. Report writes use the
existing atomic writer, so a failed run does not replace a valid report.
Dependency sync and lock generation are safe to rerun. If verification formats
files, inspect the diff to ensure only in-scope benchmark/test files changed,
then rerun the full required sequence. Do not use destructive Git commands to
recover; preserve any concurrent user work and edit only affected files.

## Artifacts and Notes

Planned stable artifact:

    /Users/jobz/.codex/worktrees/4452/relayna/reports/json-engine-evaluation.html

Planned canonical command:

    uv run --extra benchmark python -m benchmarks run json-engine-evaluation

## Interfaces and Dependencies

The benchmark exports a `BenchmarkDefinition` named `BENCHMARK` from its
module and register it in `benchmarks/registry.py`. It will depend on Relayna’s
existing Pydantic/Pydantic Core runtime versions and an opt-in, pinned orjson
benchmark extra. Production `[project].dependencies` must remain unchanged.
The generated report is standalone HTML with inline CSS and no scripts,
external fonts, images, or network resources. Production will add one private
module under `src/relayna/` with no `__all__` export and will continue using the
already-required `pydantic-core` transitive package. orjson remains in the
benchmark-only extra and is not added to production dependencies.
