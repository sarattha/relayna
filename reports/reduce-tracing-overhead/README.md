# Reduce Tracing Overhead Reports

This directory retains the immutable evidence for Relayna performance item 3,
"Reduce tracing overhead without disabling tracing."

## Authoritative Run

Run `20260731T072226Z-283782ec` compares the untouched `v1.4.31` runtime at
`283782ec95955f50e187e5fde82d12f03691834a` with the tracing optimization on
the same base. It contains:

- `baseline/`: 15 standalone HTML reports, 15 raw sidecars, three tracing-mode
  exporter inventories, provenance, unique case IDs, and checksums;
- `candidate/`: the matching candidate artifacts;
- `comparison/comparison.json`: all 1,224 matched cells with absolute and
  percentage latency and throughput deltas, sample/dispersion data where the
  source harness exposes it, bytes and cardinality, and aggregate summaries;
- `comparison/comparison.html`: a standalone reader-facing version with the
  complete case table and links to every retained report;
- `comparison/manifest.json` and `checksums.sha256`: input/output integrity and
  uniqueness validation.

Every expected qualified case occurs exactly once in baseline, once in
candidate, and once in the comparison.

## Tracing Configurations

All five registered benchmark suites run under each isolated mode:

- `disabled`: OpenTelemetry API no-op provider; Relayna instrumentation calls
  remain in place;
- `enabled-unsampled`: OpenTelemetry SDK `TracerProvider` with `ALWAYS_OFF` and
  `SimpleSpanProcessor`;
- `enabled-sampled-exported`: SDK `TracerProvider` with `ALWAYS_ON`,
  `SimpleSpanProcessor`, and a synchronous non-retaining counting exporter.

The process-default composite W3C TraceContext and baggage propagator is used in
all modes. Baseline and candidate each exported 504,104 sampled spans with
identical names, kinds, and status counts. Disabled and unsampled modes exported
zero as configured.

## Result

Complete consumer-processing latency improved `16.47%` unsampled and `16.29%`
sampled/exported. Complete publish-preparation latency improved `13.86%` and
`12.14%`, respectively. Tracing-disabled consumer and publish aggregates
improved `7.94%` and `5.71%`.

The maximum absolute aggregate drift among the unchanged envelope,
JSON-engine, and Redis-storage controls was `1.53%`. Every enabled
consumer/publish family aggregate improved by more than that bound, so the
derived assessment is "worth merging." Individual noisy cells remain visible;
no measurement was edited or selected for favorability.

## Reproduction

From the repository root:

    uv sync --extra benchmark --frozen
    env PYTHONHASHSEED=0 LC_ALL=C LANG=C TZ=UTC \
      uv run --extra benchmark python -m benchmarks.tracing_suite \
      --output-root /tmp/reduce-tracing-overhead-suite

Retain a completed canonical run with
`scripts/retain_tracing_benchmark_run.py --help`. Regenerate only the derived
comparison with:

    uv run python scripts/compare_tracing_benchmarks.py \
      --baseline-dir reports/reduce-tracing-overhead/20260731T072226Z-283782ec/baseline \
      --candidate-dir reports/reduce-tracing-overhead/20260731T072226Z-283782ec/candidate \
      --output-dir /tmp/reduce-tracing-overhead-comparison

The comparison generator refuses to overwrite output, rejects missing or
duplicate cases, validates matching host/interpreter/packages/controls/tracing
configuration, and verifies identical sampled exporter inventories.

## Limitations and Compatibility

These are local deterministic CPU microbenchmarks on one machine. Consumer
timing begins after RabbitMQ delivery, and publish timing ends at a no-op
exchange. Results exclude broker, network, business-handler, batch-exporter
serialization, OTLP, collector, and storage latency. The three legacy control
harnesses expose rounded standalone table values rather than unrounded samples.

The runtime change is private and behavior-preserving. It changes no public
signature/export, task/status/workflow contract, configuration, persisted
value, route, or wire representation. The `v1.4.32` freeze-manifest changes are
version-only and use the task's explicit production-perimeter authorization.
