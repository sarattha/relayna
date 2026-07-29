# Relayna benchmark guide

The `benchmarks` package contains reproducible development benchmarks. It is
outside `src/relayna`, is not part of the Relayna runtime package, and must not
change production behavior merely to improve a result.

## CLI usage

Run commands from the repository root. Discover the available benchmark types:

    uv run python -m benchmarks list

Run one benchmark with its canonical defaults:

    uv run python -m benchmarks run envelope-serialization
    uv run python -m benchmarks run redis-storage-cpu

Show the options owned by one benchmark:

    uv run python -m benchmarks run envelope-serialization --help

Run every registered benchmark with canonical defaults:

    uv run python -m benchmarks run-all

Equivalent Make targets are available:

    make benchmark-list
    make benchmark
    make benchmark BENCHMARK=envelope-serialization
    make benchmark-all
    make benchmark-redis-storage

Pass benchmark-specific options through Make with `BENCHMARK_ARGS`:

    make benchmark \
      BENCHMARK=envelope-serialization \
      BENCHMARK_ARGS='--repeats 1 --iterations "1 MB=2"'

`make benchmark-envelopes` remains a convenience alias for the canonical
envelope benchmark. `make benchmark-redis-storage` is the matching convenience
alias for the Redis-facing CPU benchmark.

## Envelope serialization benchmark

`envelope-serialization` measures individual `TaskEnvelope` and
`BatchTaskEnvelope` JSON encoding and validated parsing at exact current-wire
targets of 1 KB, 16 KB, 128 KB, and 1 MB. A successful canonical run atomically
writes the self-contained report to
`reports/envelope-microbenchmarks.html`.

The canonical run uses five repeats and fixed per-repeat iteration counts:

| Target | Iterations |
| --- | ---: |
| 1 KB | 4,000 |
| 16 KB | 1,000 |
| 128 KB | 150 |
| 1 MB | 20 |

For a fast development run, override every size and write outside the stable
report path:

    uv run python -m benchmarks run envelope-serialization \
      --repeats 1 \
      --iterations "1 KB=2" \
      --iterations "16 KB=2" \
      --iterations "128 KB=1" \
      --iterations "1 MB=1" \
      --output /tmp/envelope-microbenchmarks.html

## Redis-facing CPU benchmark

`redis-storage-cpu` isolates the deterministic CPU work immediately around
Redis persistence. It does not start Redis or measure commands, sockets,
networking, or event-loop scheduling.

The matrix covers:

- generic status and observation JSON storage
- Pydantic DLQ, task-lease, and merged service-feed records
- canonical service-event and workflow dedup hashes
- encode and decode for stored records, plus canonical-hash operations
- exact 1 KB, 16 KB, and 128 KB processed payloads
- ASCII and Unicode/numeric profiles

A successful canonical run writes the self-contained report to
`reports/redis-storage-cpu-microbenchmarks.html`. Run it with:

    uv run python -m benchmarks run redis-storage-cpu

The canonical run uses five repeats and these fixed iteration counts per
repeat:

| Target | Iterations |
| --- | ---: |
| 1 KB | 3,000 |
| 16 KB | 600 |
| 128 KB | 80 |

For a fast development run:

    uv run python -m benchmarks run redis-storage-cpu \
      --repeats 1 \
      --iterations "1 KB=2" \
      --iterations "16 KB=2" \
      --iterations "128 KB=1" \
      --output /tmp/redis-storage-cpu-microbenchmarks.html

## Adding a benchmark type

Each benchmark type owns its fixtures, matrix, timing logic, CLI options,
report renderer, and focused tests. The shared CLI owns discovery and dispatch;
`benchmarks/reporting.py` provides common environment metadata and atomic text
artifact writing.

1. Add one importable module under `benchmarks/`, using a lowercase
   underscore-separated filename.
2. In that module, define a function that adds benchmark-specific arguments to
   an `argparse.ArgumentParser`. Every option must have a canonical default so
   `run-all` can execute without benchmark-specific input.
3. Define a runner that accepts the parsed `argparse.Namespace`, writes its
   artifact only after a successful run, and returns `BenchmarkOutcome`. Reuse
   `collect_environment()` and `write_text_artifact()` from
   `benchmarks.reporting` where appropriate.
4. Export one `BenchmarkDefinition` named `BENCHMARK`. Use a stable,
   lowercase kebab-case CLI name and a repository-relative default artifact
   under `reports/`.
5. Import and add that definition in `registered_benchmarks()` in
   `benchmarks/registry.py`.
6. Add focused tests for registration, deterministic fixtures, matrix
   completeness, result calculations, CLI options, and artifact generation.
7. Confirm the new type appears in `python -m benchmarks list`, runs alone,
   and participates in `python -m benchmarks run-all`.

`run-all` is fail-fast: it stops at the first benchmark error. Each completed
runner must report at least one measurement and one artifact that exists.

The module-level registration shape is:

    def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
        ...

    def run_from_cli(args: argparse.Namespace) -> BenchmarkOutcome:
        ...

    BENCHMARK = BenchmarkDefinition(
        name="example-operation",
        summary="Measure a concise Relayna operation.",
        default_output=Path("reports/example-operation.html"),
        add_arguments=add_cli_arguments,
        run=run_from_cli,
    )

## Benchmark design guidelines

- Benchmark a named Relayna operation and identify the current implementation
  as the baseline. Do not mix unrelated work into one timed operation.
- Use deterministic, Relayna-shaped fixtures. Record target sizes and actual
  serialized or processed byte sizes when payload volume affects results.
- Compare implementations with equivalent inputs and validate semantic
  equivalence before timing.
- Prefer fixed iteration counts and repeated high-resolution samples. Report
  the aggregation method, timing unit, total iterations, throughput
  calculation, and baseline-relative ratio.
- Capture a UTC timestamp, Python and platform details, architecture, relevant
  package versions, and any benchmark-specific configuration needed to rerun.
- Produce a self-contained, human-readable artifact at a stable
  repository-relative path. Use an atomic write so failed runs do not replace a
  valid report.
- Keep canonical defaults representative and reproducible. Provide explicit
  quick-run overrides for development instead of weakening the canonical run.
- Avoid flaky tests: validate fixture sizes, matrix membership, calculations,
  metadata, CLI dispatch, and report structure without asserting wall-clock
  performance thresholds.
- Keep benchmark code outside `src/relayna`. A benchmark task must not change
  public APIs, contracts, persisted formats, wire behavior, or production
  runtime behavior unless that behavior change is separately requested and
  reviewed.

Before handing off a new or changed benchmark, run:

    make format
    make lint
    make typecheck
    make test
    make benchmark BENCHMARK=<benchmark-name>
    bash .codex/skills/code-change-verification/scripts/run.sh
