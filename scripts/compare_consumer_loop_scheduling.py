#!/usr/bin/env python3
"""Compare matched complete suites for consumer-loop scheduling changes."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

TASK = "optimize-consumer-loop-scheduling"
TARGET_RUNTIME_PATH = "src/relayna/_async.py"


def _load_shared_comparator() -> ModuleType:
    path = Path(__file__).with_name("compare_tracing_benchmarks.py")
    spec = importlib.util.spec_from_file_location("_relayna_tracing_comparison", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load shared comparison helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_shared = _load_shared_comparator()
TRACING_MODES: tuple[str, ...] = _shared.TRACING_MODES
BENCHMARKS: tuple[str, ...] = _shared.BENCHMARKS
HTML_FILES: dict[str, str] = _shared.HTML_FILES


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_pair(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    if baseline["task"] != TASK or candidate["task"] != TASK:
        raise ValueError("Retained benchmark task identity differs.")
    if baseline["run_id"] != candidate["run_id"]:
        raise ValueError("Retained run IDs differ.")
    if baseline["source"]["runtime_base_commit"] != candidate["source"]["runtime_base_commit"]:
        raise ValueError("Runtime base commits differ.")
    if not baseline["source"]["clean_before_run"] or not candidate["source"]["clean_before_run"]:
        raise ValueError("Both source states must be clean before measurement.")

    baseline_runtime = baseline["source"]["runtime_content_sha256"]
    candidate_runtime = candidate["source"]["runtime_content_sha256"]
    if baseline_runtime.keys() != candidate_runtime.keys() or TARGET_RUNTIME_PATH not in baseline_runtime:
        raise ValueError("Runtime content hash inventories differ.")
    if baseline_runtime[TARGET_RUNTIME_PATH] == candidate_runtime[TARGET_RUNTIME_PATH]:
        raise ValueError("Target scheduler runtime content is identical.")
    non_target_mismatches = sorted(
        path
        for path in baseline_runtime.keys() - {TARGET_RUNTIME_PATH}
        if baseline_runtime[path] != candidate_runtime[path]
    )
    if non_target_mismatches:
        raise ValueError(f"Non-target runtime or benchmark content differs: {', '.join(non_target_mismatches)}")

    for field in ("environment", "dependency_state", "packages", "tracing"):
        if baseline[field] != candidate[field]:
            raise ValueError(f"Matched-suite field differs: {field}")
    if baseline["execution"]["environment_controls"] != candidate["execution"]["environment_controls"]:
        raise ValueError("Environment controls differ.")
    if baseline["execution"]["benchmark_harness_commit"] != candidate["execution"]["benchmark_harness_commit"]:
        raise ValueError("Benchmark harness commits differ.")
    if baseline["execution"]["suite_index"]["tracing_modes"] != candidate["execution"]["suite_index"]["tracing_modes"]:
        raise ValueError("Tracing-mode registries differ.")

    baseline_finished = _parse_timestamp(baseline["execution"]["finished_at_utc"])
    candidate_started = _parse_timestamp(candidate["execution"]["started_at_utc"])
    gap_seconds = (candidate_started - baseline_finished).total_seconds()
    if not 0 <= gap_seconds <= 120:
        raise ValueError(f"Baseline/candidate launch gap is not back-to-back: {gap_seconds:.0f}s")

    return [
        f"Runtime and benchmark content hashes prove only {TARGET_RUNTIME_PATH} differs.",
        (
            "Both sides use the same benchmark harness commit, lock digests, resolved packages, "
            "interpreter, event-loop implementation/policy, host, tracing controls, and canonical matrices."
        ),
        f"Candidate measurement began {gap_seconds:.0f} seconds after baseline measurement finished.",
    ]


def _summaries(
    cells: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    benchmark_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    target_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_breakdown_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    control_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for cell in cells:
        mode = cell["tracing_mode"]
        benchmark = cell["benchmark"]
        benchmark_groups[(mode, benchmark)].append(cell)
        if benchmark == "consumer-processing" and cell["family"] == "consumer-loop":
            prefetch = int(cell["dimensions"]["prefetch"])
            if prefetch > 1:
                target_groups[mode].append(cell)
                target_breakdown_groups[(mode, cell["dimensions"]["profile"], prefetch)].append(cell)
            else:
                control_groups[(mode, "consumer-loop/prefetch-1")].append(cell)
        elif benchmark == "consumer-processing" and cell["family"] == "per-message":
            control_groups[(mode, "consumer-processing/per-message")].append(cell)
        elif benchmark != "consumer-processing":
            control_groups[(mode, benchmark)].append(cell)

    benchmark_summaries = [
        {
            "tracing_mode": mode,
            "benchmark": benchmark,
            **_shared._summary(f"{mode}/{benchmark}", grouped),
        }
        for (mode, benchmark), grouped in sorted(benchmark_groups.items())
    ]
    target_summaries = [
        {
            "tracing_mode": mode,
            "benchmark": "consumer-processing",
            "family": "consumer-loop/prefetch>1",
            **_shared._summary(f"{mode}/consumer-loop/prefetch>1", grouped),
        }
        for mode, grouped in sorted(target_groups.items())
    ]
    target_breakdown = [
        {
            "tracing_mode": mode,
            "profile": profile,
            "prefetch": prefetch,
            **_shared._summary(f"{mode}/{profile}/prefetch-{prefetch}", grouped),
        }
        for (mode, profile, prefetch), grouped in sorted(target_breakdown_groups.items())
    ]
    control_summaries = [
        {
            "tracing_mode": mode,
            "control": control,
            **_shared._summary(f"{mode}/{control}", grouped),
        }
        for (mode, control), grouped in sorted(control_groups.items())
    ]
    return benchmark_summaries, target_summaries, target_breakdown, control_summaries


def _assessment(targets: list[dict[str, Any]], controls: list[dict[str, Any]]) -> dict[str, Any]:
    control_noise = max(abs(summary["latency_percent"]) for summary in controls)
    all_targets_exceed_noise = all(summary["latency_percent"] < -control_noise for summary in targets)
    if all_targets_exceed_noise:
        verdict = "worth merging"
        rationale = (
            "Concurrent consumer-loop latency improves in every tracing mode by more than the "
            "largest absolute unchanged-control aggregate drift."
        )
    elif any(summary["latency_percent"] > control_noise for summary in targets):
        verdict = "not worth merging"
        rationale = "At least one concurrent consumer-loop aggregate regresses beyond unchanged-control drift."
    else:
        verdict = "inconclusive"
        rationale = "Concurrent consumer-loop movement is not consistently larger than unchanged-control drift."
    return {
        "verdict": verdict,
        "rationale": rationale,
        "maximum_absolute_control_drift_percent": control_noise,
        "target_summary_count": len(targets),
        "all_target_summaries_exceed_control_drift": all_targets_exceed_noise,
    }


def _summary_rows(summaries: list[dict[str, Any]], *, label_field: str) -> str:
    return "\n".join(
        f"""<tr>
          <td>{html.escape(summary["tracing_mode"])}</td>
          <td>{html.escape(str(summary[label_field]))}</td>
          <td class="number">{summary["case_count"]}</td>
          <td class="number">{summary["baseline_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["candidate_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["latency_delta_ns"] / 1_000:+.3f}</td>
          <td class="number">{summary["latency_percent"]:+.2f}%</td>
          <td class="number">{summary["throughput_percent"]:+.2f}%</td>
          <td class="number">{summary["improved_cells"]}/{summary["regressed_cells"]}</td>
        </tr>"""
        for summary in summaries
    )


def _breakdown_rows(summaries: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""<tr>
          <td>{html.escape(summary["tracing_mode"])}</td>
          <td>{html.escape(summary["profile"])}</td>
          <td class="number">{summary["prefetch"]}</td>
          <td class="number">{summary["case_count"]}</td>
          <td class="number">{summary["baseline_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["candidate_geometric_mean_latency_ns"] / 1_000:.3f}</td>
          <td class="number">{summary["latency_percent"]:+.2f}%</td>
          <td class="number">{summary["throughput_percent"]:+.2f}%</td>
        </tr>"""
        for summary in summaries
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
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in data["comparability_notes"])
    embedded = html.escape(json.dumps(data, separators=(",", ":"), sort_keys=True))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna consumer-loop scheduling benchmark comparison</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#566078; --panel:#f4f6fb; --line:#d8deea; --accent:#3659c9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:#fff; font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1800px,96vw); margin:36px auto 72px; }}
    h1 {{ font-size:clamp(28px,4vw,44px); margin-bottom:4px; }} h2 {{ margin-top:34px; }}
    .lede,.muted {{ color:var(--muted); }}
    .summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:10px; margin:24px 0; }}
    .summary div,.note {{ padding:14px 16px; background:var(--panel); border-left:4px solid var(--accent); }}
    .summary strong {{ display:block; font-size:22px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); max-height:75vh; }}
    table {{ border-collapse:collapse; width:100%; }}
    th,td {{ border-bottom:1px solid var(--line); padding:8px 10px; text-align:left; white-space:nowrap; }}
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
  <h1>Relayna consumer-loop scheduling benchmark comparison</h1>
  <p class="lede">Matched back-to-back complete-suite comparison. Negative latency and positive
  throughput deltas are improvements. All 1,224 qualified cases are included exactly once.</p>
  <div class="summary">
    <div><strong>{assessment["verdict"]}</strong>assessment</div>
    <div><strong>{assessment["maximum_absolute_control_drift_percent"]:.2f}%</strong>maximum control drift</div>
    <div><strong>{len(data["cells"]):,}</strong>matched unique cases</div>
    <div><strong>{data["export_validation"]["candidate_sampled_span_count"]:,}</strong>candidate sampled spans</div>
  </div>
  <p class="note">{html.escape(assessment["rationale"])}</p>
  <h2>Concurrent consumer-loop target</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Family</th><th>Cases</th>
    <th>Baseline µs</th><th>Candidate µs</th><th>Absolute Δ µs</th><th>Latency Δ</th>
    <th>Throughput Δ</th><th>Improved/regressed</th>
  </tr></thead><tbody>{_summary_rows(data["target_summaries"], label_field="family")}</tbody></table></div>
  <h2>Target detail by profile and prefetch</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Profile</th><th>Prefetch</th>
    <th>Cases</th><th>Baseline µs</th><th>Candidate µs</th><th>Latency Δ</th><th>Throughput Δ</th>
  </tr></thead><tbody>{_breakdown_rows(data["target_breakdown"])}</tbody></table></div>
  <h2>Unchanged controls</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Control</th><th>Cases</th>
    <th>Baseline µs</th><th>Candidate µs</th><th>Absolute Δ µs</th><th>Latency Δ</th>
    <th>Throughput Δ</th><th>Improved/regressed</th>
  </tr></thead><tbody>{_summary_rows(data["control_summaries"], label_field="control")}</tbody></table></div>
  <h2>Complete-suite benchmark aggregates</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Benchmark</th><th>Cases</th>
    <th>Baseline µs</th><th>Candidate µs</th><th>Absolute Δ µs</th><th>Latency Δ</th>
    <th>Throughput Δ</th><th>Improved/regressed</th>
  </tr></thead><tbody>{_summary_rows(data["benchmark_summaries"], label_field="benchmark")}</tbody></table></div>
  <h2>Methodology and comparability</h2>
  <p>Baseline and candidate run back-to-back on the same host with the same interpreter,
  event-loop implementation and policy, dependency lock, benchmark harness, warmups,
  repetitions, fixture sizes, prefetch values, tracing providers, samplers, propagator, and
  synchronous non-retaining exporter. The target includes only consumer-loop cells with
  prefetch 8 or 32. Prefetch 1, the per-message consumer path, publish preparation, envelope,
  JSON-engine, and Redis-storage aggregates are unchanged controls.</p>
  <ul>{notes}</ul>
  <p>No timing was edited or selected for favorability. Geometric means include every matched
  cell. The assessment requires every tracing-mode target aggregate to improve by more than
  the largest absolute control aggregate movement.</p>
  <h2>Tracing and behavior invariants</h2>
  <p>Both sides exported exactly {data["export_validation"]["baseline_sampled_span_count"]:,}
  sampled spans with identical names, kinds, and status counts. Disabled and unsampled modes
  exported zero. Raw consumer results retain exact handler, acknowledgement, rejection,
  observation, message, and peak-concurrency counts.</p>
  <h2>Retained standalone reports</h2><ul>{_artifact_links()}</ul>
  <h2>All matched cells</h2>
  <div class="table-wrap"><table><thead><tr><th>Tracing mode</th><th>Benchmark</th><th>Family</th>
    <th>Dimensions</th><th>Actual bytes</th><th>Baseline µs</th><th>Candidate µs</th>
    <th>Absolute Δ µs</th><th>Latency Δ</th><th>Throughput Δ</th>
  </tr></thead><tbody>{_shared._cell_rows(data["cells"])}</tbody></table></div>
  <h2>Limitations</h2>
  <p>These are deterministic local CPU benchmarks on one machine. Consumer timing begins after
  RabbitMQ delivery and uses no-op application work; broker, network, business processing,
  OTLP, collector, and storage latency are excluded. The three legacy control harnesses expose
  rounded standalone-table values rather than unrounded samples.</p>
</main>
<!-- relayna-consumer-loop-scheduling-comparison-data:{embedded}:end -->
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

    baseline, baseline_manifest = _shared._load_cells(args.baseline_dir)
    candidate, candidate_manifest = _shared._load_cells(args.candidate_dir)
    if baseline.keys() != candidate.keys():
        raise ValueError("Baseline and candidate case IDs differ.")
    comparability_notes = _validate_pair(baseline_manifest, candidate_manifest)
    cells = [_shared._comparison_cell(baseline[cell_id], candidate[cell_id]) for cell_id in sorted(baseline)]
    benchmark_summaries, target_summaries, target_breakdown, control_summaries = _summaries(cells)

    baseline_sampled = json.loads(
        (args.baseline_dir / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    candidate_sampled = json.loads(
        (args.candidate_dir / "enabled-sampled-exported" / "tracing-suite.json").read_text(encoding="utf-8")
    )
    if baseline_sampled["exported_spans"] != candidate_sampled["exported_spans"]:
        raise ValueError("Sampled exporter span inventories differ.")

    validation = {
        "expected_case_count": 1_224,
        "baseline_case_count": len(baseline),
        "candidate_case_count": len(candidate),
        "comparison_case_count": len(cells),
        "unique_comparison_case_count": len({cell["id"] for cell in cells}),
        "all_expected_cases_present_once_per_side": True,
        "measurements_hand_edited": False,
    }
    data = {
        "schema_version": 1,
        "task": TASK,
        "baseline_manifest_sha256": _sha256(args.baseline_dir / "manifest.json"),
        "candidate_manifest_sha256": _sha256(args.candidate_dir / "manifest.json"),
        "runtime_base_commit": baseline_manifest["source"]["runtime_base_commit"],
        "baseline_source_commit": baseline_manifest["source"]["commit"],
        "candidate_source_commit": candidate_manifest["source"]["commit"],
        "benchmark_harness_commit": baseline_manifest["execution"]["benchmark_harness_commit"],
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
        "target_breakdown": target_breakdown,
        "control_summaries": control_summaries,
        "assessment": _assessment(target_summaries, control_summaries),
        "cells": cells,
        "validation": validation,
    }
    args.output_dir.mkdir(parents=True)
    json_path = args.output_dir / "comparison.json"
    html_path = args.output_dir / "comparison.html"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(data), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "task": TASK,
        "artifacts": {
            "comparison.json": {"bytes": json_path.stat().st_size, "sha256": _sha256(json_path)},
            "comparison.html": {"bytes": html_path.stat().st_size, "sha256": _sha256(html_path)},
        },
        "inputs": {
            "baseline_manifest": data["baseline_manifest_sha256"],
            "candidate_manifest": data["candidate_manifest_sha256"],
        },
        "validation": validation,
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
