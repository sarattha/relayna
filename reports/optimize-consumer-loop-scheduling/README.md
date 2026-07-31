# Consumer-Loop Scheduling Investigation

This report set records Relayna's performance item 4 investigation. No runtime
optimization was retained: the strongest candidate improved focused
high-cardinality trials but remained inconclusive in the complete benchmark
suite and produced material target regressions in some cells. The final
`src/relayna/_async.py` is byte-for-byte identical to `origin/main`.

## Authoritative Result

The stabilized complete pair is:

    reports/optimize-consumer-loop-scheduling/
      20260731T085401Z-1459da95-stable-paired/
        baseline/
        candidate/
        comparison/

Each side contains all five registered benchmark families under disabled,
enabled-unsampled, and enabled-sampled-exported tracing. Each has 15 standalone
HTML reports, 15 raw JSON sidecars, suite indexes, a manifest, and SHA-256
checksums. All 1,224 qualified case IDs occur exactly once per side. Baseline
finished at `2026-07-31T08:56:43Z`, and candidate started in the same second on
the same Apple M1 Pro, CPython 3.13.2, default Unix selector event loop, uv
0.11.26, dependency lock, and tracing configuration.

The stabilized consumer harness uses five repeats. Consumer-loop samples use
8,192 messages at 1 KB, 2,048 at 16 KB, 256 at 128 KB, and 64 at 1 MB for both
minimal and observability-enabled profiles at prefetch 1, 8, and 32. The
handler performs the same real `TaskConsumer._handle_message()` operation once
per accepted delivery with one explicit fairness yield and no application
business work.

The complete comparison reports:

| Tracing mode | Concurrent-loop latency | Throughput |
| --- | ---: | ---: |
| disabled | +0.39% | -0.38% |
| enabled-unsampled | -1.33% | +1.35% |
| enabled-sampled-exported | -1.86% | +1.90% |

The maximum absolute unchanged-control drift is 5.39%. Target breakdown also
contains regressions of +3.46% disabled and +1.76% sampled/exported. The
derived verdict is therefore `inconclusive`, not worth claiming as a runtime
gain. Baseline and candidate each exported 811,880 sampled spans with identical
names, kinds, and statuses; disabled and unsampled modes exported zero.

## Investigation Trail

- `20260731T081626Z-1459da95/` contains the original untouched-runtime baseline
  and a later complete candidate. It is non-authoritative because sequential
  and per-message controls moved with the target over the 20-minute gap.
- `20260731T084332Z-1459da95-paired/` is the first same-second back-to-back
  complete pair. Its comparison is also inconclusive because short
  32/64/256-message loop cells produced 12.94% maximum control drift and target
  changes from -25.95% to +35.86%.
- `20260731T085401Z-1459da95-stable-paired/` is authoritative for the decision
  because it uses the stabilized high-cardinality harness.

In separate alternating focused trials, a specialized capacity counter
improved all 12 tracing/profile/prefetch 1 KB groups by 1.50–6.17%. The broader
complete-suite regressions show why those favorable focused results are not
sufficient for a merge claim.

## Reproduction

Prepare the exact dependencies and list the complete registry:

    uv sync --extra benchmark --extra dev --frozen
    make benchmark-list

Run the complete three-mode suite:

    env PYTHONHASHSEED=0 LC_ALL=C LANG=C TZ=UTC \
      uv run --no-sync python -m benchmarks.tracing_suite \
      --output-root /tmp/consumer-loop-scheduling-suite

The retained manifests record the exact intermediate branch commits used for
the original measurement: `6988fc61a803f77207f88054d87084dcfc875c2d`
for the archived baseline source and
`a9e8c305869bac89561a508889778480c05c0336` for the shared benchmark harness.
Those identifiers are historical provenance and may no longer be reachable
after a squash merge. To reproduce from durable merged history, archive
`src/relayna` from runtime base
`1459da95ddcbb2819de87eefc991711a51c24338`, whose runtime tree is byte-identical
to the measured baseline, and use the benchmark modules from the final merged
tree containing this report. The candidate used that same harness directly.
`scripts/retain_tracing_benchmark_run.py --help` documents the source and
runtime provenance arguments.

Regenerate the derived comparison without changing raw data:

    uv run --no-sync python scripts/compare_consumer_loop_scheduling.py \
      --baseline-dir \
        reports/optimize-consumer-loop-scheduling/20260731T085401Z-1459da95-stable-paired/baseline \
      --candidate-dir \
        reports/optimize-consumer-loop-scheduling/20260731T085401Z-1459da95-stable-paired/candidate \
      --output-dir /tmp/consumer-loop-scheduling-comparison

The comparator refuses overwrite; validates task/run identity, clean source
states, runtime and benchmark hashes, harness commit, lock and package versions,
host/interpreter/event-loop/tracing parity, same-second launch, sampled span
inventory, expected counts, and unique IDs; and derives its verdict from all
concurrent-loop tracing-mode aggregates and profile/prefetch subgroups against
every unchanged control.

## Compatibility and Limitations

No SDK export, signature, configuration, RabbitMQ QoS/prefetch or
ack/reject/requeue behavior, task/status/workflow contract, persisted value,
route, Studio type, wire representation, version, changelog heading, or freeze
manifest changed. Operators receive no runtime behavior change from this
investigation.

These are local deterministic CPU benchmarks after RabbitMQ delivery. They
exclude broker, network, real business-handler, OTLP, collector, and storage
latency. The synchronous counting exporter isolates SDK/exporter-facing work.
The envelope, JSON-engine, and Redis control reports expose rounded table data,
while consumer and publish reports retain unrounded repeat samples.
