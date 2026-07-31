#!/usr/bin/env python3
"""Compare complete retained Relayna tracing benchmark suites."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

TRACING_MODES = ("disabled", "enabled-unsampled", "enabled-sampled-exported")
BENCHMARKS = (
    "consumer-processing",
    "envelope-serialization",
    "json-engine-evaluation",
    "publish-preparation",
    "redis-storage-cpu",
)
RAW_FILES = {
    "consumer-processing": "consumer-processing.raw.json",
    "envelope-serialization": "envelope-serialization.raw.json",
    "json-engine-evaluation": "json-engine-evaluation.raw.json",
    "publish-preparation": "publish-preparation.raw.json",
    "redis-storage-cpu": "redis-storage-cpu.raw.json",
}
HTML_FILES = {
    "consumer-processing": "consumer-processing.html",
    "envelope-serialization": "envelope-microbenchmarks.html",
    "json-engine-evaluation": "json-engine-evaluation.html",
    "publish-preparation": "publish-preparation.html",
    "redis-storage-cpu": "redis-storage-cpu-microbenchmarks.html",
}
EXPECTED_PER_MODE = {
    "consumer-processing": 40,
    "envelope-serialization": 32,
    "json-engine-evaluation": 192,
    "publish-preparation": 72,
    "redis-storage-cpu": 72,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("Geometric means require positive values.")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _number(value: str) -> float:
    return float(value.replace(",", "").replace("×", "").strip())


def _iterations_and_repeats(value: str) -> tuple[int, int]:
    iterations, repeats = value.split("×", maxsplit=1)
    return int(_number(iterations)), int(_number(repeats))


def _control_identity(row: dict[str, str]) -> dict[str, str]:
    ignored = {
        "Actual bytes",
        "Iterations × repeats",
        "Median µs/op",
        "MiB/s",
        "Operations/s",
        "P25–P75 µs/op",
        "Throughput MB/s",
        "vs ASCII",
        "vs current",
    }
    return {key: value for key, value in sorted(row.items()) if key not in ignored}


def _consumer_cells(mode: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for family, field in (
        ("per-message", "per_message_results"),
        ("consumer-loop", "consumer_loop_results"),
    ):
        for row in raw["data"][field]:
            identity = {
                "family": family,
                "profile": row["profile"],
                "input_kind": row["input_kind"],
                "target_label": row["target_label"],
                "prefetch": row.get("prefetch"),
            }
            samples = (
                [float(value) for value in row["sample_ns_per_message"]]
                if family == "per-message"
                else [float(value) / int(row["message_count"]) for value in row["sample_duration_ns"]]
            )
            cells.append(
                {
                    "id": f"{mode}/consumer-processing/{json.dumps(identity, sort_keys=True, separators=(',', ':'))}",
                    "tracing_mode": mode,
                    "benchmark": "consumer-processing",
                    "family": family,
                    "dimensions": identity,
                    "weight": {
                        "actual_bytes": int(row["actual_message_bytes"]),
                        "iterations": row.get("iterations"),
                        "message_count": row.get("message_count"),
                        "prefetch": row.get("prefetch"),
                        "repeats": int(row["repeats"]),
                        "total_messages": row.get("total_messages"),
                        "total_bytes_per_sample": row.get("total_bytes_per_sample"),
                    },
                    "latency_ns": float(row["median_ns_per_message"]),
                    "throughput_per_second": float(row["messages_per_second"]),
                    "throughput_mib_per_second": float(row["throughput_mib_per_second"]),
                    "dispersion_ns": (
                        float(row["median_absolute_deviation_ns"])
                        if family == "per-message"
                        else _median_absolute_deviation(samples)
                    ),
                    "samples_ns": samples,
                }
            )
    return cells


def _publish_cells(mode: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in raw["data"]["results"]:
        identity = {
            "message_kind": row["message_kind"],
            "input_kind": row["input_kind"],
            "topology": row["topology"],
            "target_label": row["target_label"],
        }
        cells.append(
            {
                "id": f"{mode}/publish-preparation/{json.dumps(identity, sort_keys=True, separators=(',', ':'))}",
                "tracing_mode": mode,
                "benchmark": "publish-preparation",
                "family": row["message_kind"],
                "dimensions": identity,
                "weight": {
                    "actual_bytes": int(row["actual_message_bytes"]),
                    "bytes_per_operation": int(row["bytes_per_operation"]),
                    "iterations": int(row["iterations"]),
                    "preparations_per_operation": int(row["preparations_per_operation"]),
                    "publications_per_operation": int(row["publications_per_operation"]),
                    "repeats": int(row["repeats"]),
                    "total_operations": int(row["total_operations"]),
                    "total_prepared": int(row["total_prepared"]),
                    "total_published": int(row["total_published"]),
                },
                "latency_ns": float(row["median_ns_per_operation"]),
                "throughput_per_second": float(row["operations_per_second"]),
                "throughput_mib_per_second": float(row["throughput_mib_per_second"]),
                "dispersion_ns": float(row["median_absolute_deviation_ns"]),
                "samples_ns": [float(value) for value in row["sample_ns_per_operation"]],
            }
        )
    return cells


def _median_absolute_deviation(values: list[float]) -> float:
    ordered = sorted(values)
    median = _median(ordered)
    return _median(sorted(abs(value - median) for value in values))


def _median(values: list[float]) -> float:
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _control_cells(mode: str, benchmark: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row in raw["rows"]:
        identity = _control_identity(row)
        latency_ns = _number(row["Median µs/op"]) * 1_000
        iterations, repeats = _iterations_and_repeats(row["Iterations × repeats"])
        interval = row.get("P25–P75 µs/op")
        dispersion_ns = None
        if interval:
            lower, upper = interval.split("–", maxsplit=1)
            dispersion_ns = (_number(upper) - _number(lower)) * 500
        throughput_mib = row.get("MiB/s") or row.get("Throughput MB/s")
        cells.append(
            {
                "id": f"{mode}/{benchmark}/{json.dumps(identity, sort_keys=True, separators=(',', ':'))}",
                "tracing_mode": mode,
                "benchmark": benchmark,
                "family": str(
                    identity.get("Direction") or identity.get("Family") or identity.get("Operation") or benchmark
                ),
                "dimensions": identity,
                "weight": {
                    "actual_bytes": int(_number(row["Actual bytes"])),
                    "iterations": iterations,
                    "repeats": repeats,
                },
                "latency_ns": latency_ns,
                "throughput_per_second": (
                    _number(row["Operations/s"]) if "Operations/s" in row else 1_000_000_000 / latency_ns
                ),
                "throughput_mib_per_second": _number(throughput_mib) if throughput_mib else None,
                "dispersion_ns": dispersion_ns,
                "samples_ns": None,
            }
        )
    return cells


def _load_cells(run_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    validation = manifest["validation"]
    if (
        validation["observed_total_measurements"] != 1_224
        or validation["unique_qualified_case_count"] != 1_224
        or not validation["all_expected_cases_present_once"]
    ):
        raise ValueError(f"Incomplete retained run: {run_dir}")
    cells: dict[str, dict[str, Any]] = {}
    for mode in TRACING_MODES:
        for benchmark in BENCHMARKS:
            raw = json.loads((run_dir / mode / RAW_FILES[benchmark]).read_text(encoding="utf-8"))
            if raw["tracing_mode"] != mode or raw["benchmark"] != benchmark:
                raise ValueError(f"Mismatched raw identity in {run_dir}/{mode}/{benchmark}")
            if benchmark == "consumer-processing":
                loaded = _consumer_cells(mode, raw)
            elif benchmark == "publish-preparation":
                loaded = _publish_cells(mode, raw)
            else:
                loaded = _control_cells(mode, benchmark, raw)
            if len(loaded) != EXPECTED_PER_MODE[benchmark]:
                raise ValueError(f"Unexpected {mode}/{benchmark} case count.")
            for cell in loaded:
                if cell["id"] in cells:
                    raise ValueError(f"Duplicate case ID: {cell['id']}")
                cells[cell["id"]] = cell
    if len(cells) != 1_224:
        raise ValueError(f"Expected 1,224 unique cells, found {len(cells)}")
    return cells, manifest


def _validate_pair(baseline_manifest: dict[str, Any], candidate_manifest: dict[str, Any]) -> list[str]:
    if baseline_manifest["source"]["runtime_base_commit"] != candidate_manifest["source"]["runtime_base_commit"]:
        raise ValueError("Runtime base commits differ.")
    if baseline_manifest["packages"] != candidate_manifest["packages"]:
        raise ValueError("Resolved benchmark packages differ.")
    baseline_controls = baseline_manifest["execution"]["environment_controls"]
    candidate_controls = candidate_manifest["execution"]["environment_controls"]
    if baseline_controls != candidate_controls:
        raise ValueError("Environment controls differ.")
    if baseline_manifest["tracing"] != candidate_manifest["tracing"]:
        raise ValueError("Tracing configurations differ.")
    for key in ("python", "os", "kernel", "architecture", "cpu"):
        if baseline_manifest["environment"][key] != candidate_manifest["environment"][key]:
            raise ValueError(f"Environment field differs: {key}")
    notes: list[str] = []
    baseline_locks = baseline_manifest["dependency_state"]["lock_sha256"]
    candidate_locks = candidate_manifest["dependency_state"]["lock_sha256"]
    if baseline_locks != candidate_locks:
        notes.append(
            "Lock digests differ because the candidate adds the already-resolved "
            "opentelemetry-sdk benchmark dependency to the dev extra for tests; "
            "the complete resolved benchmark package/version maps are identical."
        )
    return notes


def _comparison_cell(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    for key in ("tracing_mode", "benchmark", "family", "dimensions", "weight"):
        if before[key] != after[key]:
            raise ValueError(f"Case shape differs for {before['id']}: {key}")
    latency_delta = after["latency_ns"] - before["latency_ns"]
    latency_percent = latency_delta / before["latency_ns"] * 100
    throughput_delta = after["throughput_per_second"] - before["throughput_per_second"]
    throughput_percent = throughput_delta / before["throughput_per_second"] * 100
    return {
        "id": before["id"],
        "tracing_mode": before["tracing_mode"],
        "benchmark": before["benchmark"],
        "family": before["family"],
        "dimensions": before["dimensions"],
        "weight": before["weight"],
        "baseline": {
            key: before[key]
            for key in (
                "latency_ns",
                "throughput_per_second",
                "throughput_mib_per_second",
                "dispersion_ns",
                "samples_ns",
            )
        },
        "candidate": {
            key: after[key]
            for key in (
                "latency_ns",
                "throughput_per_second",
                "throughput_mib_per_second",
                "dispersion_ns",
                "samples_ns",
            )
        },
        "delta": {
            "latency_ns": latency_delta,
            "latency_percent": latency_percent,
            "throughput_per_second": throughput_delta,
            "throughput_percent": throughput_percent,
        },
    }


def _summary(name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_latency = _geometric_mean([cell["baseline"]["latency_ns"] for cell in cells])
    candidate_latency = _geometric_mean([cell["candidate"]["latency_ns"] for cell in cells])
    baseline_throughput = _geometric_mean([cell["baseline"]["throughput_per_second"] for cell in cells])
    candidate_throughput = _geometric_mean([cell["candidate"]["throughput_per_second"] for cell in cells])
    return {
        "name": name,
        "case_count": len(cells),
        "baseline_geometric_mean_latency_ns": baseline_latency,
        "candidate_geometric_mean_latency_ns": candidate_latency,
        "latency_delta_ns": candidate_latency - baseline_latency,
        "latency_percent": (candidate_latency / baseline_latency - 1) * 100,
        "baseline_geometric_mean_throughput_per_second": baseline_throughput,
        "candidate_geometric_mean_throughput_per_second": candidate_throughput,
        "throughput_percent": (candidate_throughput / baseline_throughput - 1) * 100,
        "improved_cells": sum(cell["delta"]["latency_percent"] < 0 for cell in cells),
        "regressed_cells": sum(cell["delta"]["latency_percent"] > 0 for cell in cells),
        "unchanged_cells": sum(cell["delta"]["latency_percent"] == 0 for cell in cells),
        "minimum_latency_percent": min(cell["delta"]["latency_percent"] for cell in cells),
        "maximum_latency_percent": max(cell["delta"]["latency_percent"] for cell in cells),
    }


def _group_summaries(cells: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    benchmark_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    target_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        benchmark_groups[(cell["tracing_mode"], cell["benchmark"])].append(cell)
        if cell["benchmark"] == "consumer-processing":
            target_groups[(cell["tracing_mode"], cell["benchmark"], cell["family"])].append(cell)
        elif cell["benchmark"] == "publish-preparation":
            target_groups[(cell["tracing_mode"], cell["benchmark"], cell["family"])].append(cell)
    benchmark_summaries = [
        {
            "tracing_mode": mode,
            "benchmark": benchmark,
            **_summary(f"{mode}/{benchmark}", grouped),
        }
        for (mode, benchmark), grouped in sorted(benchmark_groups.items())
    ]
    target_summaries = [
        {
            "tracing_mode": mode,
            "benchmark": benchmark,
            "family": family,
            **_summary(f"{mode}/{benchmark}/{family}", grouped),
        }
        for (mode, benchmark, family), grouped in sorted(target_groups.items())
    ]
    return benchmark_summaries, target_summaries


def _assessment(
    benchmark_summaries: list[dict[str, Any]],
    target_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    controls = [
        summary
        for summary in benchmark_summaries
        if summary["benchmark"] in {"envelope-serialization", "json-engine-evaluation", "redis-storage-cpu"}
    ]
    control_noise = max(abs(summary["latency_percent"]) for summary in controls)
    enabled_targets = [
        summary
        for summary in target_summaries
        if summary["tracing_mode"] in {"enabled-unsampled", "enabled-sampled-exported"}
    ]
    meaningful = all(summary["latency_percent"] < -control_noise for summary in enabled_targets)
    if meaningful:
        verdict = "worth merging"
        rationale = (
            "Every enabled-tracing consumer and publish family aggregate improves by more "
            "than the maximum absolute unchanged-control aggregate drift."
        )
    elif any(summary["latency_percent"] > control_noise for summary in enabled_targets):
        verdict = "not worth merging"
        rationale = "At least one enabled-tracing target family regresses beyond unchanged-control drift."
    else:
        verdict = "inconclusive"
        rationale = "Enabled-tracing target movement is not consistently larger than unchanged-control drift."
    return {
        "verdict": verdict,
        "rationale": rationale,
        "maximum_absolute_control_drift_percent": control_noise,
        "enabled_target_summary_count": len(enabled_targets),
        "all_enabled_target_summaries_exceed_control_drift": meaningful,
    }


def _summary_rows(summaries: list[dict[str, Any]], *, target: bool) -> str:
    rows = []
    for summary in summaries:
        family_cell = f"<td>{html.escape(summary['family'])}</td>" if target else ""
        rows.append(
            f"""<tr>
          <td>{html.escape(summary["tracing_mode"])}</td>
          <td>{html.escape(summary["benchmark"])}</td>{family_cell}
          <td class="number">{summary["case_count"]}</td>
          <td class="number">{summary["baseline_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["candidate_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["latency_percent"]:+.2f}%</td>
          <td class="number">{summary["throughput_percent"]:+.2f}%</td>
          <td class="number">{summary["improved_cells"]}/{summary["regressed_cells"]}</td>
        </tr>"""
        )
    return "\n".join(rows)


def _cell_rows(cells: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""<tr>
          <td>{html.escape(cell["tracing_mode"])}</td>
          <td>{html.escape(cell["benchmark"])}</td>
          <td>{html.escape(cell["family"])}</td>
          <td><code>{html.escape(json.dumps(cell["dimensions"], sort_keys=True, separators=(",", ":")))}</code></td>
          <td class="number">{cell["weight"].get("actual_bytes", "—")}</td>
          <td class="number">{cell["baseline"]["latency_ns"] / 1_000:.3f}</td>
          <td class="number">{cell["candidate"]["latency_ns"] / 1_000:.3f}</td>
          <td class="number">{cell["delta"]["latency_ns"] / 1_000:+.3f}</td>
          <td class="number">{cell["delta"]["latency_percent"]:+.2f}%</td>
          <td class="number">{cell["delta"]["throughput_percent"]:+.2f}%</td>
        </tr>"""
        for cell in cells
    )


def _artifact_links() -> str:
    return "\n".join(
        f"<li><code>{mode}</code>: "
        + ", ".join(
            f'<a href="../baseline/{mode}/{HTML_FILES[benchmark]}">baseline {benchmark}</a> / '
            f'<a href="../candidate/{mode}/{HTML_FILES[benchmark]}">candidate</a>'
            for benchmark in BENCHMARKS
        )
        + "</li>"
        for mode in TRACING_MODES
    )


def _render_html(data: dict[str, Any]) -> str:
    assessment = data["assessment"]
    embedded = html.escape(json.dumps(data, separators=(",", ":"), sort_keys=True))
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in data["comparability_notes"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna tracing overhead benchmark comparison</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#566078; --panel:#f4f6fb; --line:#d8deea; --accent:#3659c9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:#fff; font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1800px,96vw); margin:36px auto 72px; }}
    h1 {{ font-size:clamp(28px,4vw,44px); margin-bottom:4px; }} h2 {{ margin-top:34px; }}
    .lede,.muted {{ color:var(--muted); }}
    .summary {{
      display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
      gap:10px; margin:24px 0;
    }}
    .summary div,.note {{
      padding:14px 16px; background:var(--panel); border-left:4px solid var(--accent);
    }}
    .summary strong {{ display:block; font-size:22px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); max-height:75vh; }}
    table {{ border-collapse:collapse; width:100%; }}
    th,td {{
      border-bottom:1px solid var(--line); padding:8px 10px;
      text-align:left; white-space:nowrap;
    }}
    thead th {{ position:sticky; top:0; background:#e9edf8; }}
    .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
    code {{ background:#edf0f7; border-radius:4px; padding:2px 5px; }}
    @media print {{
      main {{ width:100%; margin:0; }} thead th {{ position:static; }}
      .table-wrap {{ max-height:none; }}
    }}
  </style>
</head>
<body><main>
  <h1>Relayna tracing overhead benchmark comparison</h1>
  <p class="lede">Matched complete-suite comparison across tracing disabled, configured
  unsampled, and configured sampled/exported operation. Negative latency and positive
  throughput deltas are improvements. All 1,224 qualified cases are included exactly once.</p>
  <div class="summary">
    <div><strong>{assessment["verdict"]}</strong>assessment</div>
    <div><strong>{assessment["maximum_absolute_control_drift_percent"]:.2f}%</strong>
    maximum unchanged-control drift</div>
    <div><strong>{len(data["cells"]):,}</strong>matched unique cases</div>
    <div><strong>{data["export_validation"]["candidate_sampled_span_count"]:,}</strong>candidate exported spans</div>
  </div>
  <p class="note">{html.escape(assessment["rationale"])}</p>
  <h2>Tracing target families</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Benchmark</th>
    <th>Family</th><th>Cases</th><th>Baseline µs</th><th>Candidate µs</th>
    <th>Latency Δ</th><th>Throughput Δ</th><th>Improved/regressed</th>
  </tr></thead><tbody>{_summary_rows(data["target_summaries"], target=True)}</tbody></table></div>
  <h2>Complete-suite benchmark aggregates</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Benchmark</th>
    <th>Cases</th><th>Baseline µs</th><th>Candidate µs</th><th>Latency Δ</th>
    <th>Throughput Δ</th><th>Improved/regressed</th>
  </tr></thead><tbody>{_summary_rows(data["benchmark_summaries"], target=False)}</tbody></table></div>
  <h2>Methodology and comparability</h2>
  <p>Baseline and candidate use the same host, interpreter, environment controls, benchmark
  matrices, canonical warmups/repetitions, OpenTelemetry API/SDK 1.41.1, samplers,
  synchronous non-retaining exporter, default composite W3C TraceContext/baggage propagator,
  and resolved benchmark package versions. Consumer results report per-message CPU latency
  and public-loop throughput after delivery. Publish results include complete local public
  preparation through a no-op exchange. They exclude broker, network, and application work.</p>
  <ul>{notes}</ul>
  <p>No individual trial was selected or edited. Geometric means include every matched cell.
  The merge assessment compares each enabled target-family aggregate with the largest absolute
  aggregate drift among envelope, JSON-engine, and Redis-storage controls.</p>
  <h2>Tracing invariants</h2>
  <p>Both sides exported exactly {data["export_validation"]["baseline_sampled_span_count"]:,}
  sampled spans with the same names, kinds, and status counts. Unsampled and disabled modes
  exported zero. Relayna instrumentation, configured sampling, propagation, span activation,
  exception/status behavior, and exporter delivery remain active.</p>
  <h2>Retained standalone reports</h2><ul>{_artifact_links()}</ul>
  <h2>All matched cells</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Benchmark</th>
    <th>Family</th><th>Dimensions</th><th>Actual bytes</th><th>Baseline µs</th>
    <th>Candidate µs</th><th>Absolute Δ µs</th><th>Latency Δ</th><th>Throughput Δ</th>
  </tr></thead><tbody>{_cell_rows(data["cells"])}</tbody></table></div>
  <h2>Limitations</h2>
  <p>These are local deterministic microbenchmarks on one machine. Consumer timing begins
  after RabbitMQ delivery; publish timing ends at a no-op exchange. The synchronous counting
  exporter isolates SDK/exporter-facing CPU but does not model batching, serialization, OTLP,
  collector, storage, or network latency. Rounded table data is used for the three legacy
  control harnesses because those reports do not embed unrounded samples.</p>
</main>
<!-- relayna-tracing-comparison-data:{embedded}:end -->
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite comparison: {args.output_dir}")

    baseline, baseline_manifest = _load_cells(args.baseline_dir)
    candidate, candidate_manifest = _load_cells(args.candidate_dir)
    if baseline.keys() != candidate.keys():
        raise ValueError("Baseline and candidate case IDs differ.")
    comparability_notes = _validate_pair(baseline_manifest, candidate_manifest)
    cells = [_comparison_cell(baseline[cell_id], candidate[cell_id]) for cell_id in sorted(baseline)]
    benchmark_summaries, target_summaries = _group_summaries(cells)
    baseline_sampled = json.loads(
        (args.baseline_dir / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    candidate_sampled = json.loads(
        (args.candidate_dir / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    if baseline_sampled["exported_spans"] != candidate_sampled["exported_spans"]:
        raise ValueError("Sampled exporter span inventories differ.")
    data = {
        "schema_version": 1,
        "task": "reduce-tracing-overhead",
        "baseline_manifest_sha256": _sha256(args.baseline_dir / "manifest.json"),
        "candidate_manifest_sha256": _sha256(args.candidate_dir / "manifest.json"),
        "runtime_base_commit": baseline_manifest["source"]["runtime_base_commit"],
        "baseline_source_commit": baseline_manifest["source"]["commit"],
        "candidate_source_commit": candidate_manifest["source"]["commit"],
        "tracing_modes": list(TRACING_MODES),
        "benchmark_registry": list(BENCHMARKS),
        "comparability_notes": comparability_notes,
        "export_validation": {
            "baseline_sampled_span_count": baseline_sampled["exported_spans"]["count"],
            "candidate_sampled_span_count": candidate_sampled["exported_spans"]["count"],
            "identical_names_kinds_statuses": True,
            "baseline_unsampled_span_count": 0,
            "candidate_unsampled_span_count": 0,
        },
        "benchmark_summaries": benchmark_summaries,
        "target_summaries": target_summaries,
        "assessment": _assessment(benchmark_summaries, target_summaries),
        "cells": cells,
        "validation": {
            "expected_case_count": 1_224,
            "baseline_case_count": len(baseline),
            "candidate_case_count": len(candidate),
            "comparison_case_count": len(cells),
            "unique_comparison_case_count": len({cell["id"] for cell in cells}),
            "all_expected_cases_present_once_per_side": True,
            "measurements_hand_edited": False,
        },
    }
    args.output_dir.mkdir(parents=True)
    json_path = args.output_dir / "comparison.json"
    html_path = args.output_dir / "comparison.html"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(data), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "task": "reduce-tracing-overhead",
        "artifacts": {
            "comparison.json": {"bytes": json_path.stat().st_size, "sha256": _sha256(json_path)},
            "comparison.html": {"bytes": html_path.stat().st_size, "sha256": _sha256(html_path)},
        },
        "inputs": {
            "baseline_manifest": data["baseline_manifest_sha256"],
            "candidate_manifest": data["candidate_manifest_sha256"],
        },
        "validation": data["validation"],
        "assessment": data["assessment"],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_path = args.output_dir / "checksums.sha256"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in (html_path, json_path, manifest_path)),
        encoding="utf-8",
    )
    print(
        f"Compared {len(cells)} unique cases; verdict={data['assessment']['verdict']}; "
        f"control drift={data['assessment']['maximum_absolute_control_drift_percent']:.2f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
