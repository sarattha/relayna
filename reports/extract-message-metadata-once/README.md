# Extract message metadata once benchmark evidence

All directories below are immutable benchmark evidence for Relayna performance
item 2. Do not append cases to or overwrite an existing run.

## Authoritative pair

`20260731T063554Z-44adab85-paired/` contains back-to-back full canonical
baseline and candidate suites plus the complete comparison:

- `baseline/`: exact base commit
  `44adab85adbd7e8355e66742748c5b75178b0656`;
- `candidate/`: the isolated candidate runtime snapshot, bound by per-file
  content hashes;
- `comparison-reviewed/`: final standalone HTML and machine-readable JSON
  covering every one of the 408 cases exactly once.

Every baseline and candidate HTML report has a raw JSON sidecar. Consumer and
publish sidecars retain embedded unrounded repeat samples; JSON-engine retains
the available P25–P75 interval; envelope and Redis-storage retain the exact
rounded table values exposed by their harnesses. Each directory includes a
manifest and SHA-256 index.

The original derived `comparison/` directory is also preserved unchanged.
`comparison-reviewed/` supersedes it after review hardened the reusable
generator to reject environment/package/control/timestamp mismatches and to
derive improvement, regression, or inconclusive wording from the measurements.

The grouped authoritative result is `-4.05%` minimal per-message latency,
`-4.19%` minimal consumer-loop latency, and `-5.38%` over the complete
consumer-processing matrix. Unchanged benchmark-family geometric means moved
between `-2.03%` and `+1.24%`.

## Retained non-authoritative run

`20260731T061143Z-44adab85/` is retained without modification because it was
the first complete baseline/candidate execution. Its candidate consumer-loop
aggregate drifted about `+8%`, contradicting a nearby focused paired run. It is
not used for the release performance claim; the back-to-back pair above was
required to avoid a misleading comparison.

See `benchmarks/README.md` for commands, methodology, interpretation, and
limitations.
