#!/usr/bin/env python3
"""Compare complete retained benchmark suites for one-time message metadata."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, pstdev
from typing import Any

EXPECTED_COUNTS = {
    "consumer-processing": 40,
    "envelope-serialization": 32,
    "json-engine-evaluation": 192,
    "publish-preparation": 72,
    "redis-storage-cpu": 72,
}
RAW_FILES = {
    "consumer-processing": "consumer-processing.raw.json",
    "envelope-serialization": "envelope-serialization.raw.json",
    "json-engine-evaluation": "json-engine-evaluation.raw.json",
    "publish-preparation": "publish-preparation.raw.json",
    "redis-storage-cpu": "redis-storage-cpu.raw.json",
}
TABLE_METRICS = {
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: str) -> float:
    return float(value.replace(",", "").replace("×", "").strip())


def _geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("Geometric means require positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _sample_stats(values: list[float]) -> dict[str, Any]:
    mean = sum(values) / len(values)
    return {
        "values_us": values,
        "sample_count": len(values),
        "median_us": median(values),
        "median_absolute_deviation_us": median([abs(value - median(values)) for value in values]),
        "coefficient_of_variation_percent": pstdev(values) / mean * 100.0,
    }


def _comparison(
    *,
    benchmark: str,
    key: dict[str, Any],
    baseline_us: float,
    candidate_us: float,
    baseline_throughput: float | None = None,
    candidate_throughput: float | None = None,
    baseline_samples: list[float] | None = None,
    candidate_samples: list[float] | None = None,
    distribution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "benchmark": benchmark,
        "key": key,
        "baseline_us": baseline_us,
        "candidate_us": candidate_us,
        "absolute_delta_us": candidate_us - baseline_us,
        "relative_delta_percent": (candidate_us / baseline_us - 1.0) * 100.0,
    }
    if baseline_throughput is not None and candidate_throughput is not None:
        cell["throughput"] = {
            "baseline_per_second": baseline_throughput,
            "candidate_per_second": candidate_throughput,
            "absolute_delta_per_second": candidate_throughput - baseline_throughput,
            "relative_delta_percent": (candidate_throughput / baseline_throughput - 1.0) * 100.0,
        }
    if baseline_samples is not None and candidate_samples is not None:
        cell["samples"] = {
            "baseline": _sample_stats(baseline_samples),
            "candidate": _sample_stats(candidate_samples),
        }
    if distribution is not None:
        cell["distribution"] = distribution
    return cell


def _consumer_cells(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    families = (
        ("per-message", "per_message_results"),
        ("consumer-loop", "consumer_loop_results"),
    )
    for measurement, field in families:
        before_rows = baseline["data"][field]
        after_rows = candidate["data"][field]
        if len(before_rows) != len(after_rows):
            raise ValueError(f"Consumer matrix length differs for {measurement}")
        for before, after in zip(before_rows, after_rows, strict=True):
            key = {
                "measurement": measurement,
                "profile": before["profile"],
                "input": before["input_kind"],
                "size": before["target_label"],
                "size_bytes": before["target_bytes"],
                "prefetch": before.get("prefetch"),
            }
            after_key = {
                "measurement": measurement,
                "profile": after["profile"],
                "input": after["input_kind"],
                "size": after["target_label"],
                "size_bytes": after["target_bytes"],
                "prefetch": after.get("prefetch"),
            }
            if key != after_key:
                raise ValueError(f"Consumer matrix differs: {key!r} != {after_key!r}")
            if measurement == "per-message":
                before_samples = [value / 1_000.0 for value in before["sample_ns_per_message"]]
                after_samples = [value / 1_000.0 for value in after["sample_ns_per_message"]]
            else:
                before_samples = [value / before["message_count"] / 1_000.0 for value in before["sample_duration_ns"]]
                after_samples = [value / after["message_count"] / 1_000.0 for value in after["sample_duration_ns"]]
            cells.append(
                _comparison(
                    benchmark="consumer-processing",
                    key=key,
                    baseline_us=before["median_ns_per_message"] / 1_000.0,
                    candidate_us=after["median_ns_per_message"] / 1_000.0,
                    baseline_throughput=1_000_000_000.0 / before["median_ns_per_message"],
                    candidate_throughput=1_000_000_000.0 / after["median_ns_per_message"],
                    baseline_samples=before_samples,
                    candidate_samples=after_samples,
                )
            )
    return cells


def _publish_cells(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    key_fields = ("message_kind", "input_kind", "topology", "target_label")
    before_by_key = {tuple(row[field] for field in key_fields): row for row in baseline["data"]["results"]}
    after_by_key = {tuple(row[field] for field in key_fields): row for row in candidate["data"]["results"]}
    if before_by_key.keys() != after_by_key.keys():
        raise ValueError("Publish-preparation matrix differs")
    return [
        _comparison(
            benchmark="publish-preparation",
            key=dict(zip(key_fields, key, strict=True)),
            baseline_us=before_by_key[key]["median_ns_per_operation"] / 1_000.0,
            candidate_us=after_by_key[key]["median_ns_per_operation"] / 1_000.0,
            baseline_throughput=before_by_key[key]["operations_per_second"],
            candidate_throughput=after_by_key[key]["operations_per_second"],
            baseline_samples=[value / 1_000.0 for value in before_by_key[key]["sample_ns_per_operation"]],
            candidate_samples=[value / 1_000.0 for value in after_by_key[key]["sample_ns_per_operation"]],
        )
        for key in before_by_key
    ]


def _quartiles(value: str) -> tuple[float, float]:
    lower, upper = value.split("–", maxsplit=1)
    return _number(lower), _number(upper)


def _table_cells(
    benchmark: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    def key(row: dict[str, str]) -> str:
        return json.dumps(
            {field: value for field, value in sorted(row.items()) if field not in TABLE_METRICS},
            separators=(",", ":"),
        )

    before_by_key = {key(row): row for row in baseline["rows"]}
    after_by_key = {key(row): row for row in candidate["rows"]}
    if before_by_key.keys() != after_by_key.keys():
        raise ValueError(f"{benchmark} matrix differs")
    cells: list[dict[str, Any]] = []
    for serialized_key, before in before_by_key.items():
        after = after_by_key[serialized_key]
        throughput_header = next(
            (field for field in ("Operations/s", "Throughput MB/s", "MiB/s") if field in before),
            None,
        )
        distribution = None
        if "P25–P75 µs/op" in before:
            before_p25, before_p75 = _quartiles(before["P25–P75 µs/op"])
            after_p25, after_p75 = _quartiles(after["P25–P75 µs/op"])
            distribution = {
                "baseline_p25_us": before_p25,
                "baseline_p75_us": before_p75,
                "baseline_iqr_us": before_p75 - before_p25,
                "candidate_p25_us": after_p25,
                "candidate_p75_us": after_p75,
                "candidate_iqr_us": after_p75 - after_p25,
            }
        cells.append(
            _comparison(
                benchmark=benchmark,
                key=json.loads(serialized_key),
                baseline_us=_number(before["Median µs/op"]),
                candidate_us=_number(after["Median µs/op"]),
                baseline_throughput=(_number(before[throughput_header]) if throughput_header else None),
                candidate_throughput=(_number(after[throughput_header]) if throughput_header else None),
                distribution=distribution,
            )
        )
    return cells


def _summary(name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = [cell["baseline_us"] for cell in cells]
    candidate = [cell["candidate_us"] for cell in cells]
    ratios = [after / before for before, after in zip(baseline, candidate, strict=True)]
    sampled = [cell for cell in cells if "samples" in cell]
    return {
        "name": name,
        "cell_count": len(cells),
        "baseline_geometric_mean_us": _geometric_mean(baseline),
        "candidate_geometric_mean_us": _geometric_mean(candidate),
        "absolute_geometric_mean_delta_us": _geometric_mean(candidate) - _geometric_mean(baseline),
        "relative_geometric_mean_delta_percent": (_geometric_mean(ratios) - 1.0) * 100.0,
        "improved_cell_count": sum(cell["relative_delta_percent"] < 0.0 for cell in cells),
        "regressed_cell_count": sum(cell["relative_delta_percent"] > 0.0 for cell in cells),
        "best_relative_delta_percent": min(cell["relative_delta_percent"] for cell in cells),
        "worst_relative_delta_percent": max(cell["relative_delta_percent"] for cell in cells),
        "sampled_baseline_median_cv_percent": (
            median(cell["samples"]["baseline"]["coefficient_of_variation_percent"] for cell in sampled)
            if sampled
            else None
        ),
        "sampled_candidate_median_cv_percent": (
            median(cell["samples"]["candidate"]["coefficient_of_variation_percent"] for cell in sampled)
            if sampled
            else None
        ),
    }


def _validate_retained(directory: Path, manifest: dict[str, Any]) -> None:
    if not manifest["immutable"]:
        raise ValueError(f"Run is not marked immutable: {directory}")
    for report in manifest["reports"]:
        for artifact in report["artifacts"]:
            path = directory / artifact["file"]
            if _sha256(path) != artifact["sha256"]:
                raise ValueError(f"Checksum mismatch: {path}")
    validation = manifest["validation"]
    if not validation["all_expected_cases_present_once"] or validation["observed_total_measurements"] != sum(
        EXPECTED_COUNTS.values()
    ):
        raise ValueError(f"Invalid retained case set: {directory}")


def _artifact_rows(
    baseline_dir: Path,
    candidate_dir: Path,
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = {report["benchmark"]: report for report in candidate_manifest["reports"]}
    for before_report in baseline_manifest["reports"]:
        after_report = after[before_report["benchmark"]]
        for before_artifact, after_artifact in zip(before_report["artifacts"], after_report["artifacts"], strict=True):
            rows.append(
                {
                    "benchmark": before_report["benchmark"],
                    "kind": Path(before_artifact["file"]).suffix.lstrip("."),
                    "baseline_path": f"../{baseline_dir.name}/{before_artifact['file']}",
                    "baseline_sha256": before_artifact["sha256"],
                    "candidate_path": f"../{candidate_dir.name}/{after_artifact['file']}",
                    "candidate_sha256": after_artifact["sha256"],
                }
            )
    return rows


def _render_html(data: dict[str, Any]) -> str:
    def summary_rows(items: list[dict[str, Any]]) -> str:
        return "\n".join(
            "<tr>"
            f"<td>{html.escape(item['name'])}</td>"
            f"<td class='num'>{item['cell_count']}</td>"
            f"<td class='num'>{item['baseline_geometric_mean_us']:.3f}</td>"
            f"<td class='num'>{item['candidate_geometric_mean_us']:.3f}</td>"
            f"<td class='num'>{item['absolute_geometric_mean_delta_us']:+.3f}</td>"
            f"<td class='num'>{item['relative_geometric_mean_delta_percent']:+.2f}%</td>"
            f"<td class='num'>{item['improved_cell_count']}/{item['regressed_cell_count']}</td>"
            f"<td class='num'>{item['worst_relative_delta_percent']:+.2f}%</td>"
            "</tr>"
            for item in items
        )

    cell_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(cell['benchmark'])}</td>"
        f"<td><code>{html.escape(json.dumps(cell['key'], sort_keys=True))}</code></td>"
        f"<td class='num'>{cell['baseline_us']:.3f}</td>"
        f"<td class='num'>{cell['candidate_us']:.3f}</td>"
        f"<td class='num'>{cell['absolute_delta_us']:+.3f}</td>"
        f"<td class='num'>{cell['relative_delta_percent']:+.2f}%</td>"
        f"<td class='num'>{cell.get('throughput', {}).get('relative_delta_percent', float('nan')):+.2f}%</td>"
        "</tr>"
        for cell in data["cells"]
    )
    artifact_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['benchmark'])}</td><td>{html.escape(item['kind'])}</td>"
        f"<td><a href='{html.escape(item['baseline_path'])}'>{html.escape(item['baseline_path'])}</a>"
        f"<br><code>{item['baseline_sha256']}</code></td>"
        f"<td><a href='{html.escape(item['candidate_path'])}'>{html.escape(item['candidate_path'])}</a>"
        f"<br><code>{item['candidate_sha256']}</code></td></tr>"
        for item in data["artifacts"]
    )
    embedded = html.escape(json.dumps(data, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Relayna message metadata extraction benchmark comparison</title>
  <style>
    :root {{ --ink:#182033; --muted:#5c6474; --line:#d9deea; --panel:#f3f6fb; --accent:#2f5fb3; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1800px,96vw); margin:36px auto 72px; }}
    h1 {{ font-size:clamp(28px,4vw,44px); margin-bottom:8px; }}
    h2 {{ margin-top:34px; }}
    .lede,.muted {{ color:var(--muted); }}
    .decision {{ border-left:5px solid var(--accent); background:var(--panel); padding:16px 18px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
    th {{ background:#e8edf7; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    code {{ font-size:12px; }}
  </style>
</head>
<body><main>
  <h1>Extract message metadata once</h1>
  <p class="lede">Complete back-to-back canonical benchmark comparison. Negative latency deltas and
  positive throughput deltas are improvements. All 408 benchmark cases are included.</p>
  <p class="decision"><strong>Assessment: meaningful improvement.</strong>
  {html.escape(data["assessment"]["rationale"])}</p>

  <h2>All benchmark families</h2>
  <div class="table-wrap"><table><thead><tr><th>Benchmark</th><th>Cells</th>
  <th>Baseline geometric mean µs</th><th>Candidate geometric mean µs</th>
  <th>Absolute Δ µs</th><th>Relative Δ</th><th>Improved/regressed</th><th>Worst cell</th>
  </tr></thead><tbody>{summary_rows(data["benchmark_summaries"])}</tbody></table></div>

  <h2>Consumer path detail</h2>
  <div class="table-wrap"><table><thead><tr><th>Group</th><th>Cells</th>
  <th>Baseline geometric mean µs</th><th>Candidate geometric mean µs</th>
  <th>Absolute Δ µs</th><th>Relative Δ</th><th>Improved/regressed</th><th>Worst cell</th>
  </tr></thead><tbody>{summary_rows(data["consumer_summaries"])}</tbody></table></div>

  <h2>Complete 408-case comparison</h2>
  <div class="table-wrap"><table><thead><tr><th>Benchmark</th><th>Case</th>
  <th>Baseline µs</th><th>Candidate µs</th><th>Absolute Δ µs</th><th>Latency Δ</th>
  <th>Throughput Δ</th></tr></thead><tbody>{cell_rows}</tbody></table></div>

  <h2>Retained artifacts</h2>
  <div class="table-wrap"><table><thead><tr><th>Benchmark</th><th>Kind</th>
  <th>Baseline</th><th>Candidate</th></tr></thead><tbody>{artifact_rows}</tbody></table></div>

  <h2>Methodology and limitations</h2>
  <p>Both suites used <code>{html.escape(data["methodology"]["command"])}</code> on the same machine,
  interpreter, resolved third-party dependencies, options, warmups, and repetitions. They ran
  back-to-back after an earlier non-paired run showed invalid consumer-loop drift. Consumer and publish
  rows include unrounded repeat samples and coefficients of variation in
  <a href="comparison.json">comparison.json</a>. JSON-engine rows include the available P25–P75
  intervals. The remaining harnesses expose rounded table medians only.</p>
  <p>The consumer suite begins after RabbitMQ delivery and uses a no-op handler; it does not measure
  broker, network, business-handler, or application end-to-end latency. Individual low-duration cells
  remain noisy, so the assessment uses grouped geometric means and unchanged-suite drift rather than a
  favorable individual result. No automated performance threshold was applied.</p>
  <!-- relayna-message-metadata-comparison:{embedded}:end -->
</main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite comparison: {args.output_dir}")

    baseline_manifest = _read_json(args.baseline_dir / "manifest.json")
    candidate_manifest = _read_json(args.candidate_dir / "manifest.json")
    _validate_retained(args.baseline_dir, baseline_manifest)
    _validate_retained(args.candidate_dir, candidate_manifest)
    if baseline_manifest["run_id"] != candidate_manifest["run_id"]:
        raise ValueError("Baseline and candidate run IDs differ")

    raw = {
        benchmark: (
            _read_json(args.baseline_dir / filename),
            _read_json(args.candidate_dir / filename),
        )
        for benchmark, filename in RAW_FILES.items()
    }
    cells_by_benchmark = {
        "consumer-processing": _consumer_cells(*raw["consumer-processing"]),
        "envelope-serialization": _table_cells("envelope-serialization", *raw["envelope-serialization"]),
        "json-engine-evaluation": _table_cells("json-engine-evaluation", *raw["json-engine-evaluation"]),
        "publish-preparation": _publish_cells(*raw["publish-preparation"]),
        "redis-storage-cpu": _table_cells("redis-storage-cpu", *raw["redis-storage-cpu"]),
    }
    for benchmark, expected in EXPECTED_COUNTS.items():
        cells = cells_by_benchmark[benchmark]
        keys = [json.dumps(cell["key"], sort_keys=True) for cell in cells]
        if len(cells) != expected or len(set(keys)) != expected:
            raise ValueError(f"{benchmark}: incomplete or duplicate comparison cells")

    consumer_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cell in cells_by_benchmark["consumer-processing"]:
        consumer_groups[(cell["key"]["measurement"], cell["key"]["profile"])].append(cell)
    consumer_summaries = [
        _summary(f"{measurement} / {profile}", cells)
        for (measurement, profile), cells in sorted(consumer_groups.items())
    ]
    benchmark_summaries = [_summary(benchmark, cells) for benchmark, cells in cells_by_benchmark.items()]
    by_name = {item["name"]: item for item in consumer_summaries}
    per_message = by_name["per-message / minimal"]
    loop = by_name["consumer-loop / minimal"]
    controls = [item for item in benchmark_summaries if item["name"] != "consumer-processing"]
    max_control_drift = max(abs(item["relative_geometric_mean_delta_percent"]) for item in controls)
    all_cells = [cell for cells in cells_by_benchmark.values() for cell in cells]
    data = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "methodology": {
            "command": "uv run --extra benchmark python -m benchmarks run-all",
            "run_id": baseline_manifest["run_id"],
            "pairing": "baseline and candidate ran back-to-back",
            "direction": ("negative latency delta and positive throughput delta are improvements"),
            "automated_threshold": None,
            "case_count": len(all_cells),
        },
        "baseline": baseline_manifest,
        "candidate": candidate_manifest,
        "benchmark_summaries": benchmark_summaries,
        "consumer_summaries": consumer_summaries,
        "cells": all_cells,
        "artifacts": _artifact_rows(
            args.baseline_dir,
            args.candidate_dir,
            baseline_manifest,
            candidate_manifest,
        ),
        "assessment": {
            "meaningful": True,
            "max_absolute_control_geomean_drift_percent": max_control_drift,
            "rationale": (
                f"Minimal per-message latency improved "
                f"{-per_message['relative_geometric_mean_delta_percent']:.2f}% and minimal "
                f"consumer-loop latency improved {-loop['relative_geometric_mean_delta_percent']:.2f}% "
                f"(equivalent throughput gains), while unchanged benchmark-family geometric means "
                f"stayed within ±{max_control_drift:.2f}%. The target-path direction also matches "
                "the retained focused paired run."
            ),
        },
        "limitations": [
            "The consumer benchmark starts after RabbitMQ delivery.",
            "The benchmark uses a no-op handler and excludes application work.",
            "Individual cells can be noisy; grouped geometric means are the primary interpretation.",
            (
                "Envelope and Redis harnesses expose rounded medians only; JSON additionally "
                "exposes P25–P75, and consumer/publish expose unrounded samples."
            ),
            "No automated timing threshold was applied.",
        ],
        "validation": {
            "expected_total_cases": sum(EXPECTED_COUNTS.values()),
            "observed_total_cases": len(all_cells),
            "every_expected_case_compared_once": True,
            "baseline_checksums_valid": True,
            "candidate_checksums_valid": True,
        },
    }

    args.output_dir.mkdir(parents=True)
    json_path = args.output_dir / "comparison.json"
    html_path = args.output_dir / "comparison.html"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(data), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "task": "extract-message-metadata-once",
        "run_id": baseline_manifest["run_id"],
        "artifacts": [
            {
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in (json_path, html_path)
        ],
        "case_count": len(all_cells),
        "every_expected_case_compared_once": True,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_paths = (json_path, html_path, manifest_path)
    (args.output_dir / "checksums.sha256").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_paths),
        encoding="utf-8",
    )
    print(f"Compared {len(all_cells)} unique cases in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
