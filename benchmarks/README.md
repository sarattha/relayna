# Relayna benchmark guide

The `benchmarks` package contains reproducible development benchmarks. It is
outside `src/relayna`, is not part of the Relayna runtime package, and must not
change production behavior merely to improve a result.

## CLI usage

Run commands from the repository root. Discover the available benchmark types:

    uv run python -m benchmarks list

Run one benchmark with its canonical defaults:

    uv run python -m benchmarks run envelope-serialization
    uv run python -m benchmarks run consumer-processing
    uv run python -m benchmarks run publish-preparation
    uv run python -m benchmarks run redis-storage-cpu

Benchmarks that compare optional engines should install the benchmark extra:

    uv run --extra benchmark python -m benchmarks run json-engine-evaluation

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

## Consumer processing benchmark

`consumer-processing` measures Relayna's real inbound `TaskConsumer` operation
after RabbitMQ has already delivered a message. It excludes connections,
sockets, broker work, queue declaration latency, retry sleeps, and application
business logic.

The default `all` mode writes both measurement families to the self-contained
`reports/consumer-processing.html` report:

    uv run python -m benchmarks run consumer-processing
    uv run python -m benchmarks run consumer-processing --measurement all

Run only the real per-message `_handle_message()` path:

    uv run python -m benchmarks run consumer-processing \
      --measurement per-message

Run only the public `TaskConsumer.run_forever()` loop over deterministic
preloaded AMQP fakes:

    uv run python -m benchmarks run consumer-processing \
      --measurement consumer-loop

For a fast development run in any mode, lower the repeats and override every
size-specific count:

    uv run python -m benchmarks run consumer-processing \
      --measurement all \
      --repeats 1 \
      --iterations "1 KB=1" \
      --iterations "16 KB=1" \
      --iterations "128 KB=1" \
      --iterations "1 MB=1" \
      --loop-messages "1 KB=1" \
      --loop-messages "16 KB=1" \
      --loop-messages "128 KB=1" \
      --loop-messages "1 MB=1" \
      --output /tmp/consumer-processing.html

Per-message timing covers canonical and configured-alias input with minimal and
observability-enabled profiles at exact 1 KB, 16 KB, 128 KB, and 1 MB actual
body sizes. Loop timing keeps canonical input, uses both profiles, and tests
prefetch 1, 8, and 32 with size-aware message cardinality. The report states
all optional-feature settings and exact counts.

## Publish preparation benchmark

`publish-preparation` measures the complete local CPU-side public publish path
through a deterministic no-op exchange. It includes input conversion and alias
normalization, Pydantic validation and dumping, topology routing, trace/header
construction, Pydantic Core transport JSON encoding, `aio_pika.Message`
construction, priority handling, metrics, and async publication. It excludes
RabbitMQ connections, sockets, broker work, and network latency.

The 72-cell matrix covers individual and batch tasks, workflow messages, and
status events; model, canonical-mapping, and configured-alias inputs; exact
1 KB, 16 KB, 128 KB, and 1 MB AMQP bodies; and direct/shared, task-type, and
workflow-stage routing where applicable. One event loop is reused for the
entire run.

The canonical command writes `reports/publish-preparation.html`:

    uv run python -m benchmarks run publish-preparation

For a quick development run:

    uv run python -m benchmarks run publish-preparation \
      --repeats 1 \
      --iterations "1 KB=1" \
      --iterations "16 KB=1" \
      --iterations "128 KB=1" \
      --iterations "1 MB=1" \
      --output /tmp/publish-preparation.html

The corrected pre-optimization baseline is retained immutably as
`reports/publish-preparation-baseline.html`, with hash-bound revision,
methodology, matrix, and environment provenance in
`reports/publish-preparation-baseline.json`. The benchmark contains no
executable copy of the old publishing algorithm. Generate a current-runtime
comparison against that retained artifact with:

    uv run python -m benchmarks run publish-preparation \
      --run-label candidate \
      --baseline-report reports/publish-preparation-baseline.html \
      --output reports/publish-preparation.html

Baseline comparison requires the canonical repeats and iteration matrix. The
command fails clearly if the HTML or provenance sidecar is missing, modified,
or incompatible with the current canonical matrix.

## JSON engine decision benchmark

`json-engine-evaluation` compares Relayna's complete deterministic CPU-side JSON
paths, including the implemented private Pydantic Core production transport
codec. It covers the released v1.4.29 stdlib reference, the new production path,
direct model-aware Pydantic JSON, and orjson for:

- outbound model/mapping preparation, encoding, and AMQP-ready bytes;
- canonical inbound parsing and validated envelope construction;
- alias-compatible inbound parsing, `documentId` normalization, and validation;
- task and two-task batch envelopes at exact current-wire targets of 1 KB,
  16 KB, 128 KB, and 1 MB; and
- ASCII-heavy and Unicode/numeric payload profiles.

The canonical run writes the self-contained performance, compatibility,
packaging, and recommendation report to
`reports/json-engine-evaluation.html`:

    uv run --extra benchmark python -m benchmarks run json-engine-evaluation

or:

    make benchmark BENCHMARK=json-engine-evaluation

orjson is pinned in the optional `benchmark` extra. It is not a production
runtime dependency. A contributor intentionally testing optional-engine
handling can omit the extra and generate a clearly marked partial report:

    uv run python -m benchmarks run json-engine-evaluation \
      --allow-missing-orjson \
      --output /tmp/json-engine-evaluation-partial.html

For a quick development run, restrict the profile and iterations:

    uv run --extra benchmark python -m benchmarks run json-engine-evaluation \
      --profile ascii \
      --repeats 1 \
      --iterations "1 KB=2" \
      --iterations "16 KB=2" \
      --iterations "128 KB=1" \
      --iterations "1 MB=1" \
      --output /tmp/json-engine-evaluation.html

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
