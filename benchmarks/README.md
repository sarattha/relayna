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
prefetch 1, 8, and 32 with five repeats and size-aware message cardinalities of
8,192, 2,048, 256, and 64 respectively. These counts keep the scheduling-heavy
small-body cells stable while bounding large-body parsing time. The report
states all optional-feature settings and exact counts.

### Disabled instrumentation comparison

Relayna `1.4.30` avoids successful-path consumer instrumentation work only when
no configured receiver could observe it. With neither an observation sink nor
metrics, `TaskConsumer` skips receive/resource/ack event construction, both task
CPU/RSS samples, and the metrics-only duration clock. Metrics-only consumers
still sample and record resource metrics. Observation-only and combined
configurations retain the same successful event types, fields, timestamps,
order, counts, and start/end samples. OpenTelemetry tracing remains active.

The retained evidence is under
`reports/consumer-disabled-instrumentation/`. The initial `baseline/` and
`candidate/` pair remains immutable for auditability. Because one unchanged
observation-enabled loop profile showed material single-cell drift, a second
complete pair was run sequentially and is authoritative:

- `baseline-authoritative/`: executable `1491705c2031` historical source,
  five HTML reports, and a checksum-bound manifest;
- `candidate-authoritative/`: final executable fast-path source before release
  edits, five HTML reports, and a matching manifest;
- `comparison.html`: self-contained methodology, links and checksums, geometric
  means, every consumer cell, control drift, decision, and limitations;
- `comparison.json`: the same evidence in machine-readable form.

Recreate the comparison from retained reports:

    uv run python scripts/compare_consumer_instrumentation.py \
      --baseline-dir reports/consumer-disabled-instrumentation/baseline-authoritative \
      --candidate-dir reports/consumer-disabled-instrumentation/candidate-authoritative \
      --output-html reports/consumer-disabled-instrumentation/comparison.html \
      --output-json reports/consumer-disabled-instrumentation/comparison.json

The authoritative minimal per-message geometric mean improved from 41.857 to
32.627 microseconds/message (`-22.1%`), and minimal consumer-loop improved from
58.089 to 47.452 microseconds/message (`-18.3%`). The minimal 1 KB group moved
from 25.533 to 18.781 microseconds/message (`-26.4%`); 16 KB moved from 27.491
to 20.537 (`-25.3%`). Observation-enabled per-message and loop groups changed
`+1.9%` and `-3.3%`; the four unchanged control benchmark aggregates ranged
from `-1.7%` to `+1.0%`.

Run the canonical five-benchmark suite used for both source states with:

    uv run python -m benchmarks run-all

No timing threshold is enforced. The report includes every cell and treats
unchanged benchmarks as drift evidence. The consumer benchmark starts after
RabbitMQ delivery and uses a no-op handler; it does not represent broker,
network, business-handler, or application end-to-end latency.

### One-time message metadata comparison

Relayna `1.4.31` snapshots delivered AMQP headers and message properties once
per task, workflow, or aggregation delivery. The private immutable metadata
value is reused by tracing, handler contexts, retry and DLQ publication,
metrics, and observations. It is scoped to one delivery and never retains the
message body.

The complete retained evidence is under
`reports/extract-message-metadata-once/`. Run
`20260731T063554Z-44adab85-paired` is authoritative because its baseline and
candidate suites ran back-to-back with matching interpreter, resolved
third-party dependencies, environment controls, benchmark options, warmups,
and repetitions. Each side contains five standalone HTML reports, five raw
machine-readable sidecars, a manifest, and SHA-256 checksums. The comparison
contains all 408 cases once:

- `comparison-reviewed/comparison.html`: standalone methodology, summary and complete
  case tables, sample-variance interpretation, artifact links, and checksums;
- `comparison-reviewed/comparison.json`: the same comparison with absolute and
  percentage latency deltas, throughput deltas, consumer/publish repeat samples,
  and JSON-engine P25–P75 intervals;
- `comparison-reviewed/manifest.json` and `checksums.sha256`: case-count and
  integrity validation.

The original derived `comparison/` directory remains immutable but is
superseded by `comparison-reviewed/`. The reviewed generator validates matching
hosts, interpreters, controls, warmups, repetitions, back-to-back timestamps,
and resolved third-party packages before comparison. It derives improvement,
regression, or inconclusive wording from both minimal target groups relative to
the maximum unchanged-suite aggregate drift.

### Consumer-loop scheduling investigation

Performance item 4 tested lower-overhead bounded consumer scheduling after
items 1–3 had merged. No runtime candidate was retained. Focused
high-cardinality trials were positive, but the stabilized complete five-suite,
three-tracing-mode comparison remained inconclusive and contained target
regressions. Relayna therefore keeps the released semaphore/task scheduler
unchanged.

The immutable reports, complete methodology, reproducible commands, exact
cardinalities, compatibility result, and limitations are documented in
`reports/optimize-consumer-loop-scheduling/README.md`. The authoritative
stabilized pair contains all 1,224 cases per side and records a 5.39% maximum
control drift versus target aggregate changes of +0.39%, -1.33%, and -1.86%.

The earlier `20260731T061143Z-44adab85` run is retained as non-authoritative
drift evidence. Its candidate consumer-loop aggregate moved about `+8%` while a
nearby focused pair moved in the opposite direction, so it is not used for the
release claim.

Recreate a retained run and the comparison with:

    uv run python scripts/retain_benchmark_run.py --help
    uv run python scripts/compare_message_metadata.py \
      --baseline-dir reports/extract-message-metadata-once/20260731T063554Z-44adab85-paired/baseline \
      --candidate-dir reports/extract-message-metadata-once/20260731T063554Z-44adab85-paired/candidate \
      --output-dir /tmp/extract-message-metadata-comparison

The authoritative minimal per-message geometric mean improved `4.05%`, and the
minimal consumer-loop geometric mean improved `4.19%`. The complete
consumer-processing matrix improved `5.38%`; 35 of 40 cells improved.
Unchanged benchmark-family geometric means ranged from `-2.03%` to `+1.24%`.
The comparison therefore treats the grouped target-path improvement as
meaningful, while preserving individual noisy cells and applying no automated
timing threshold.

### Tracing overhead comparison

Relayna `1.4.32` reduces tracing span setup overhead without disabling tracing.
The benchmark extension runs all five registered suites in three isolated
process configurations:

- `disabled`: Relayna instrumentation uses the OpenTelemetry API no-op provider;
- `enabled-unsampled`: SDK `TracerProvider`, `ALWAYS_OFF`, and
  `SimpleSpanProcessor`;
- `enabled-sampled-exported`: SDK `TracerProvider`, `ALWAYS_ON`,
  `SimpleSpanProcessor`, and a synchronous non-retaining counting exporter.

Isolation is required because OpenTelemetry supports setting the global tracer
provider once per process. Every standalone report records its provider,
sampler, processor, exporter, propagator, package versions, and environment.
Run the canonical 1,224-case suite with:

    uv sync --extra benchmark --frozen
    env PYTHONHASHSEED=0 LC_ALL=C LANG=C TZ=UTC \
      uv run --extra benchmark python -m benchmarks.tracing_suite \
      --output-root /tmp/reduce-tracing-overhead-suite

Use `--quick` only to validate the harness shape. Quick runs use one repeat and
one operation per size and are not performance evidence.

The retained baseline, candidate, and comparison are under
`reports/reduce-tracing-overhead/20260731T072226Z-283782ec/`. Each run contains
15 standalone HTML reports, 15 raw sidecars, per-mode exporter inventories, a
provenance manifest, and checksums. The comparison contains all 1,224 cases in
JSON and standalone HTML.

Regenerate the derived comparison without changing retained measurements:

    uv run python scripts/compare_tracing_benchmarks.py \
      --baseline-dir reports/reduce-tracing-overhead/20260731T072226Z-283782ec/baseline \
      --candidate-dir reports/reduce-tracing-overhead/20260731T072226Z-283782ec/candidate \
      --output-dir /tmp/reduce-tracing-overhead-comparison

Enabled-unsampled consumer and publish aggregates improved `16.47%` and
`13.86%`; enabled-sampled/exported aggregates improved `16.29%` and `12.14%`.
The maximum absolute unchanged-control aggregate drift was `1.53%`. Baseline
and candidate each exported the same 504,104 sampled spans with identical
names, kinds, and status counts.

These local deterministic benchmarks exclude broker, network, application
handler, batching-exporter serialization, OTLP, collector, and storage latency.
The synchronous counting exporter measures SDK/exporter-facing processing
without retaining spans.

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
