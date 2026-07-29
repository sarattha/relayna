# Relayna Redis-Facing CPU Microbenchmarks

This ExecPlan is a living document. The sections Progress, Surprises &
Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as
work proceeds.

This document is maintained in accordance with
`/Users/jobz/.codex/worktrees/98c7/relayna/PLANS.md`.

## Purpose / Big Picture

Relayna contributors need reproducible measurements for the CPU work immediately
around Redis persistence without mixing in Redis server, network, socket, or
event-loop variability. After this work, the repository benchmark CLI will
generate a self-contained HTML report covering current JSON storage,
Pydantic-record storage, and canonical hashing at exact 1 KB, 16 KB, and 128 KB
input sizes for both ASCII and Unicode/numeric payload profiles.

## Progress

- [x] (2026-07-29 01:05Z) Inspected the benchmark registry, report conventions,
  status and observation stores, DLQ and task-lease stores, service-event feed,
  and workflow dedup signature implementation.
- [x] (2026-07-29 01:05Z) Established an additive benchmark-only compatibility
  boundary with no production runtime, public API, Redis schema, or freeze
  manifest changes.
- [x] (2026-07-29 01:18Z) Implemented exact-size fixtures and the complete
  72-case operation matrix with ASCII-relative comparisons.
- [x] (2026-07-29 01:20Z) Added 48 focused fixture, matrix, hash, result, CLI,
  and HTML-generation tests; 55 focused benchmark and CLI tests pass together.
- [x] (2026-07-29 01:21Z) Generated the canonical 72-row report and confirmed
  both benchmark types complete through `run-all`.
- [x] (2026-07-29 01:25Z) Ran the mandatory full Relayna verification stack:
  format, lint, and type checking pass; 485 SDK tests pass with one existing
  skip; and all 244 Studio backend tests pass.
- [x] (2026-07-29 01:26Z) Validated the documented Make alias and benchmark
  help, regenerated the canonical 72-case report, and prepared the PR update.

## Surprises & Discoveries

- Observation: Relayna's Redis-facing storage paths use two distinct CPU
  representations. Status and observation stores use standard-library JSON,
  while DLQ, lease, and feed stores use Pydantic JSON serialization and
  validation.
  Evidence: `src/relayna/status/store.py`,
  `src/relayna/observability/store.py`, `src/relayna/dlq/store.py`,
  `src/relayna/storage/task_lease_store.py`, and
  `src/relayna/observability/feed.py`.

- Observation: The smallest production-shaped `DLQRecord` JSON is close to 1 KB
  because the current serializer includes default and null fields.
  Evidence: A minimal fixed-time record serializes to 910 UTF-8 bytes, so the
  1 KB fixture must keep identifiers and profile metadata compact.

## Decision Log

- Decision: Measure seven representations: status JSON, observation JSON, DLQ
  record, task-lease record, service-feed record, canonical event hash, and
  workflow dedup hash.
  Rationale: Together they cover every storage family and operation requested
  without timing Redis I/O or unrelated application behavior.
  Date/Author: 2026-07-29 / Codex.

- Decision: Define target size as the exact UTF-8 byte length produced or
  consumed by the current operation. For hashes, size is the canonical encoded
  material passed to SHA-256 rather than the fixed 64-character digest.
  Rationale: This keeps throughput comparable across encode, decode, and hash
  cases and makes Unicode byte cost explicit.
  Date/Author: 2026-07-29 / Codex.

- Decision: Compare Unicode/numeric results with the matching ASCII case for
  the same representation, operation, and target size.
  Rationale: There is only one production implementation per requested
  operation; profile-relative results provide a fair comparison without
  inventing an alternative implementation or changing runtime behavior.
  Date/Author: 2026-07-29 / Codex.

## Outcomes & Retrospective

The repository now exposes `redis-storage-cpu` through the scalable benchmark
CLI and a `make benchmark-redis-storage` alias. It produces a deterministic
72-case, self-contained HTML report covering all requested representations,
operations, sizes, and profiles. Exact-size fixture calibration handles both
raw UTF-8 JSON and the workflow dedup path's escaped canonical JSON without
changing production code.

Focused formatting, linting, type checking, 55 combined benchmark/CLI tests,
the single benchmark command, the Make alias, and `run-all` pass. Mandatory
repository verification also passes with 485 SDK tests and one existing skip,
plus 244 Studio backend tests. The generated report is
`/Users/jobz/.codex/worktrees/98c7/relayna/reports/redis-storage-cpu-microbenchmarks.html`.

## Context and Orientation

The reusable benchmark CLI lives under `benchmarks/`. Definitions are registered
in `benchmarks/registry.py`; benchmark-specific fixtures, timing, and HTML
rendering live in separate modules; common environment and atomic artifact
helpers live in `benchmarks/reporting.py`. Focused tests live under `tests/`,
stable generated reports under `reports/`, and usage guidance in
`benchmarks/README.md`.

The production paths being mirrored are:

- `RedisStatusStore` standard-library JSON storage and event deduplication.
- `RedisObservationStore` standard-library JSON storage with datetime support.
- `RedisDLQStore` Pydantic `DLQRecord` storage.
- `RedisTaskLeaseStore` Pydantic `TaskLease` storage.
- `RedisServiceEventFeedStore` Pydantic `RelaynaServiceEvent` storage.
- Service-feed canonical event cursor hashing.
- `build_dedup_signature` workflow-contract hashing.

## Compatibility Boundary

This is additive development tooling outside `src/relayna/`. It must not change
released SDK imports, production Redis keys or values, task/status/workflow
contracts, Studio behavior, configuration, persisted schemas, public APIs, or
production-freeze manifests. The benchmark mirrors current CPU operations and
does not require `$implementation-strategy` or `$production-freeze-guard`.

## Plan of Work

Add `benchmarks/redis_storage_cpu.py` with deterministic builders, exact-byte
calibration, a complete typed matrix, fixed-iteration repeated timing, semantic
preflight checks, environment capture, and a self-contained HTML renderer.
Register it in `benchmarks/registry.py`, document it in
`benchmarks/README.md`, add a convenience Make target, and add focused tests
under `tests/`.

## Concrete Steps

From the repository root:

    uv run python -m benchmarks list
    uv run pytest tests/test_redis_storage_cpu_microbenchmarks.py tests/test_benchmark_cli.py
    uv run python -m benchmarks run redis-storage-cpu
    bash .codex/skills/code-change-verification/scripts/run.sh

## Validation and Acceptance

Acceptance requires exact 1,024, 16,384, and 131,072-byte fixtures for all seven
representations and both profiles; encode/decode coverage for all five storage
representations; canonical-hash coverage for both hash representations; a
complete unique matrix; deterministic hash outputs; a self-contained HTML
report with methodology, results, environment metadata, iterations, timing,
throughput, and ASCII-relative comparisons; CLI discovery and `run-all`
participation; and the mandatory Relayna verification stack passing.

Tests must not assert timing thresholds.

## Idempotence and Recovery

Fixture and matrix construction are pure and deterministic. Benchmark runs
replace the stable report atomically only after successful measurement and
rendering. Interrupted or failed runs can be retried with the same command
without cleanup; temporary report files are removed by the shared writer.

## Artifacts and Notes

The canonical report path will be
`reports/redis-storage-cpu-microbenchmarks.html`.

## Interfaces and Dependencies

The benchmark uses only Relayna's existing development environment: Python
standard-library JSON, SHA-256, Pydantic models, current Relayna modules, and
the shared benchmark registry/reporting contracts. It introduces no production
dependency or runtime export.
