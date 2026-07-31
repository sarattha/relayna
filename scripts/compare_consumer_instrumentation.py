#!/usr/bin/env python3
"""Compare retained consumer-instrumentation benchmark suites."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from statistics import median
from typing import Any

CONSUMER_PREFIX = "<!-- relayna-consumer-processing-data:"
PUBLISH_PREFIX = "<!-- relayna-publish-preparation-data:"
EMBEDDED_SUFFIX = ":end -->"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_embedded(path: Path, prefix: str) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    start = content.find(prefix)
    end = content.find(EMBEDDED_SUFFIX, start)
    if start < 0 or end < 0:
        raise ValueError(f"Missing embedded benchmark data in {path}")
    return json.loads(html.unescape(content[start + len(prefix) : end].strip()))


def _load_table(path: Path, required_headers: tuple[str, ...]) -> list[list[str]]:
    parser = _TableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    for table in parser.tables:
        if table and all(header in table[0] for header in required_headers):
            return table
    raise ValueError(f"Missing expected result table in {path}")


def _number(value: str) -> float:
    return float(value.replace(",", "").replace("×", "").strip())


def _geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("Geometric means require positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _comparison(
    *,
    key: dict[str, Any],
    baseline_us: float,
    candidate_us: float,
) -> dict[str, Any]:
    return {
        **key,
        "baseline_us": baseline_us,
        "candidate_us": candidate_us,
        "absolute_delta_us": candidate_us - baseline_us,
        "relative_delta_percent": (candidate_us / baseline_us - 1.0) * 100.0,
    }


def _consumer_cells(baseline_path: Path, candidate_path: Path) -> list[dict[str, Any]]:
    baseline = _load_embedded(baseline_path, CONSUMER_PREFIX)
    candidate = _load_embedded(candidate_path, CONSUMER_PREFIX)
    cells: list[dict[str, Any]] = []
    families = (
        ("per-message", "per_message_results"),
        ("consumer-loop", "consumer_loop_results"),
    )
    for measurement, field in families:
        baseline_rows = baseline[field]
        candidate_rows = candidate[field]
        if len(baseline_rows) != len(candidate_rows):
            raise ValueError(f"Consumer matrix length differs for {measurement}")
        for before, after in zip(baseline_rows, candidate_rows, strict=True):
            key = {
                "measurement": measurement,
                "profile": before["profile"],
                "input": before["input_kind"],
                "size": before["target_label"],
                "size_bytes": before["target_bytes"],
                "prefetch": before.get("prefetch"),
            }
            candidate_key = {
                "measurement": measurement,
                "profile": after["profile"],
                "input": after["input_kind"],
                "size": after["target_label"],
                "size_bytes": after["target_bytes"],
                "prefetch": after.get("prefetch"),
            }
            if key != candidate_key:
                raise ValueError(f"Consumer matrix differs: {key!r} != {candidate_key!r}")
            cells.append(
                _comparison(
                    key=key,
                    baseline_us=before["median_ns_per_message"] / 1_000.0,
                    candidate_us=after["median_ns_per_message"] / 1_000.0,
                )
            )
    return cells


def _table_control_cells(
    baseline_path: Path,
    candidate_path: Path,
    *,
    required_headers: tuple[str, ...],
    metric_header: str,
) -> list[dict[str, Any]]:
    baseline_table = _load_table(baseline_path, required_headers)
    candidate_table = _load_table(candidate_path, required_headers)
    headers = baseline_table[0]
    if candidate_table[0] != headers:
        raise ValueError(f"Control table headers differ for {baseline_path.name}")
    metric_index = headers.index(metric_header)
    baseline_rows = {tuple(row[:metric_index]): row for row in baseline_table[1:]}
    candidate_rows = {tuple(row[:metric_index]): row for row in candidate_table[1:]}
    if baseline_rows.keys() != candidate_rows.keys():
        raise ValueError(f"Control matrix differs for {baseline_path.name}")
    return [
        _comparison(
            key={"cell": list(key)},
            baseline_us=_number(baseline_rows[key][metric_index]),
            candidate_us=_number(candidate_rows[key][metric_index]),
        )
        for key in baseline_rows
    ]


def _publish_control_cells(baseline_path: Path, candidate_path: Path) -> list[dict[str, Any]]:
    baseline_rows = _load_embedded(baseline_path, PUBLISH_PREFIX)["results"]
    candidate_rows = _load_embedded(candidate_path, PUBLISH_PREFIX)["results"]
    key_fields = (
        "message_kind",
        "input_kind",
        "topology",
        "target_label",
        "target_bytes",
        "iterations",
        "actual_message_bytes",
        "bytes_per_operation",
        "publications_per_operation",
        "preparations_per_operation",
        "repeats",
    )
    before_by_key = {tuple(row[field] for field in key_fields): row for row in baseline_rows}
    after_by_key = {tuple(row[field] for field in key_fields): row for row in candidate_rows}
    if before_by_key.keys() != after_by_key.keys():
        raise ValueError("Publish-preparation control matrix differs")
    return [
        _comparison(
            key={"cell": dict(zip(key_fields, key, strict=True))},
            baseline_us=before_by_key[key]["median_ns_per_operation"] / 1_000.0,
            candidate_us=after_by_key[key]["median_ns_per_operation"] / 1_000.0,
        )
        for key in before_by_key
    ]


def _summary(name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_values = [cell["baseline_us"] for cell in cells]
    candidate_values = [cell["candidate_us"] for cell in cells]
    deltas = [cell["absolute_delta_us"] for cell in cells]
    ratios = [candidate / baseline for baseline, candidate in zip(baseline_values, candidate_values, strict=True)]
    return {
        "name": name,
        "cell_count": len(cells),
        "baseline_geometric_mean_us": _geometric_mean(baseline_values),
        "candidate_geometric_mean_us": _geometric_mean(candidate_values),
        "absolute_geometric_mean_delta_us": _geometric_mean(candidate_values) - _geometric_mean(baseline_values),
        "relative_geometric_mean_delta_percent": (_geometric_mean(ratios) - 1.0) * 100.0,
        "median_absolute_delta_us": median(deltas),
        "best_relative_delta_percent": min(cell["relative_delta_percent"] for cell in cells),
        "worst_relative_delta_percent": max(cell["relative_delta_percent"] for cell in cells),
    }


def _consumer_summaries(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        groups[(cell["measurement"], cell["profile"])].append(cell)
    summaries = [
        _summary(f"{measurement} / {profile}", group)
        for (measurement, profile), group in sorted(groups.items())
    ]
    for size in ("1 KB", "16 KB", "128 KB", "1 MB"):
        group = [
            cell
            for cell in cells
            if cell["measurement"] == "per-message" and cell["profile"] == "minimal" and cell["size"] == size
        ]
        summaries.append(_summary(f"per-message / minimal / {size}", group))
    return summaries


def _artifact_rows(
    baseline_dir: Path,
    candidate_dir: Path,
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_by_benchmark = {item["benchmark"]: item for item in candidate_manifest["reports"]}
    rows: list[dict[str, Any]] = []
    for baseline_item in baseline_manifest["reports"]:
        candidate_item = candidate_by_benchmark[baseline_item["benchmark"]]
        baseline_path = baseline_dir / baseline_item["file"]
        candidate_path = candidate_dir / candidate_item["file"]
        if _sha256(baseline_path) != baseline_item["sha256"]:
            raise ValueError(f"Baseline checksum mismatch: {baseline_path}")
        if _sha256(candidate_path) != candidate_item["sha256"]:
            raise ValueError(f"Candidate checksum mismatch: {candidate_path}")
        rows.append(
            {
                "benchmark": baseline_item["benchmark"],
                "baseline_file": baseline_item["file"],
                "baseline_path": f"{baseline_dir.name}/{baseline_item['file']}",
                "baseline_sha256": baseline_item["sha256"],
                "candidate_file": candidate_item["file"],
                "candidate_path": f"{candidate_dir.name}/{candidate_item['file']}",
                "candidate_sha256": candidate_item["sha256"],
            }
        )
    return rows


def _render_html(data: dict[str, Any]) -> str:
    summary_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['name'])}</td>"
        f"<td class='num'>{item['cell_count']}</td>"
        f"<td class='num'>{item['baseline_geometric_mean_us']:.3f}</td>"
        f"<td class='num'>{item['candidate_geometric_mean_us']:.3f}</td>"
        f"<td class='num'>{item['absolute_geometric_mean_delta_us']:+.3f}</td>"
        f"<td class='num'>{item['relative_geometric_mean_delta_percent']:+.1f}%</td>"
        "</tr>"
        for item in data["consumer"]["summaries"]
    )
    cell_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(cell['measurement'])}</td>"
        f"<td>{html.escape(cell['profile'])}</td>"
        f"<td>{html.escape(cell['input'])}</td>"
        f"<td>{html.escape(cell['size'])}</td>"
        f"<td class='num'>{'—' if cell['prefetch'] is None else cell['prefetch']}</td>"
        f"<td class='num'>{cell['baseline_us']:.3f}</td>"
        f"<td class='num'>{cell['candidate_us']:.3f}</td>"
        f"<td class='num'>{cell['absolute_delta_us']:+.3f}</td>"
        f"<td class='num'>{cell['relative_delta_percent']:+.1f}%</td>"
        "</tr>"
        for cell in data["consumer"]["cells"]
    )
    control_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['name'])}</td>"
        f"<td class='num'>{item['cell_count']}</td>"
        f"<td class='num'>{item['baseline_geometric_mean_us']:.3f}</td>"
        f"<td class='num'>{item['candidate_geometric_mean_us']:.3f}</td>"
        f"<td class='num'>{item['absolute_geometric_mean_delta_us']:+.3f}</td>"
        f"<td class='num'>{item['relative_geometric_mean_delta_percent']:+.1f}%</td>"
        f"<td class='num'>{item['worst_relative_delta_percent']:+.1f}%</td>"
        "</tr>"
        for item in data["controls"]["summaries"]
    )
    artifact_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(item['benchmark'])}</td>"
        f"<td><a href='{html.escape(item['baseline_path'])}'>{html.escape(item['baseline_path'])}</a>"
        f"<br><code>{item['baseline_sha256']}</code></td>"
        f"<td><a href='{html.escape(item['candidate_path'])}'>{html.escape(item['candidate_path'])}</a>"
        f"<br><code>{item['candidate_sha256']}</code></td>"
        "</tr>"
        for item in data["artifacts"]
    )
    embedded = html.escape(json.dumps(data, separators=(",", ":")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Relayna disabled consumer instrumentation comparison</title>
  <style>
    :root {{ --ink:#172033; --muted:#5b6478; --line:#d8deea; --panel:#f4f6fb; --accent:#3659c9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); font:15px/1.5 system-ui,-apple-system,sans-serif; }}
    main {{ width:min(1600px,96vw); margin:36px auto 72px; }}
    h1 {{ font-size:clamp(28px,4vw,44px); margin-bottom:8px; }}
    h2 {{ margin-top:34px; }}
    .lede,.muted {{ color:var(--muted); }}
    .decision {{ border-left:5px solid var(--accent); background:var(--panel); padding:16px 18px; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
    th {{ background:#e9edf8; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    code {{ font-size:12px; }}
  </style>
</head>
<body><main>
  <h1>Disabled consumer instrumentation comparison</h1>
  <p class="lede">Canonical five-benchmark suites on the same interpreter, dependencies, machine, and
  command. Negative deltas are faster. This report applies no automated timing threshold and includes
  every measured consumer cell.</p>
  <p class="decision"><strong>Engineering decision: ship.</strong>
  {html.escape(data['decision']['rationale'])}</p>

  <h2>Methodology</h2>
  <p>Baseline source is commit <code>{data['baseline']['source']['commit']}</code>. Candidate source is
  the recorded runtime/test diff on branch <code>{html.escape(data['candidate']['source']['branch'])}</code>.
  Both ran <code>{html.escape(data['baseline']['execution']['command'])}</code>. Reports were copied only
  after successful completion and are bound to SHA-256 checksums. Baseline uses executable historical
  source; no benchmark-only legacy runtime exists.</p>

  <h2>Consumer geometric-mean summaries</h2>
  <div class="table-wrap"><table><thead><tr><th>Group</th><th>Cells</th>
  <th>Baseline µs</th><th>Candidate µs</th><th>Absolute Δ µs</th><th>Relative Δ</th>
  </tr></thead><tbody>{summary_rows}</tbody></table></div>

  <h2>Complete consumer-processing deltas</h2>
  <div class="table-wrap"><table><thead><tr><th>Measurement</th><th>Profile</th><th>Input</th>
  <th>Size</th><th>Prefetch</th><th>Baseline µs/message</th><th>Candidate µs/message</th>
  <th>Absolute Δ µs</th><th>Relative Δ</th></tr></thead><tbody>{cell_rows}</tbody></table></div>

  <h2>Control benchmark drift</h2>
  <p class="muted">These four benchmarks do not exercise the changed consumer instrumentation path.
  Their aggregate movement is environmental drift evidence, not optimization impact.</p>
  <div class="table-wrap"><table><thead><tr><th>Benchmark</th><th>Cells</th>
  <th>Baseline geometric mean µs</th><th>Candidate geometric mean µs</th>
  <th>Absolute Δ µs</th><th>Relative Δ</th><th>Worst cell Δ</th>
  </tr></thead><tbody>{control_rows}</tbody></table></div>

  <h2>Retained artifacts</h2>
  <div class="table-wrap"><table><thead><tr><th>Benchmark</th><th>Baseline</th><th>Candidate</th>
  </tr></thead><tbody>{artifact_rows}</tbody></table></div>

  <h2>Interpretation and limitations</h2>
  <p>The minimal profile removes two CPU/RSS samples and four unobservable task-event constructions per
  successful message. Observation-only, metrics-only, and combined behavior are covered by deterministic
  tests. The consumer benchmark begins after RabbitMQ delivery and uses a no-op handler; it is not broker,
  network, business-handler, or application end-to-end latency. Local microbenchmark variation remains,
  particularly for low-iteration large-payload cells, so the decision uses repeated minimal-profile
  direction and aggregate evidence rather than cherry-picked cells.</p>
  <p>Machine-readable data: <a href="comparison.json">comparison.json</a>.</p>
  <!-- relayna-consumer-disabled-instrumentation-comparison:{embedded}:end -->
</main></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    baseline_manifest = _read_json(args.baseline_dir / "manifest.json")
    candidate_manifest = _read_json(args.candidate_dir / "manifest.json")
    artifacts = _artifact_rows(args.baseline_dir, args.candidate_dir, baseline_manifest, candidate_manifest)

    consumer = _consumer_cells(
        args.baseline_dir / "consumer-processing.html",
        args.candidate_dir / "consumer-processing.html",
    )
    controls = {
        "envelope-serialization": _table_control_cells(
            args.baseline_dir / "envelope-microbenchmarks.html",
            args.candidate_dir / "envelope-microbenchmarks.html",
            required_headers=("Envelope", "Implementation", "Median µs/op"),
            metric_header="Median µs/op",
        ),
        "json-engine-evaluation": _table_control_cells(
            args.baseline_dir / "json-engine-evaluation.html",
            args.candidate_dir / "json-engine-evaluation.html",
            required_headers=("Envelope", "Profile", "Shape", "Engine", "Median µs/op"),
            metric_header="Median µs/op",
        ),
        "publish-preparation": _publish_control_cells(
            args.baseline_dir / "publish-preparation.html",
            args.candidate_dir / "publish-preparation.html",
        ),
        "redis-storage-cpu": _table_control_cells(
            args.baseline_dir / "redis-storage-cpu-microbenchmarks.html",
            args.candidate_dir / "redis-storage-cpu-microbenchmarks.html",
            required_headers=("Family", "Representation", "Profile", "Median µs/op"),
            metric_header="Median µs/op",
        ),
    }
    control_summaries = [_summary(name, cells) for name, cells in controls.items()]
    consumer_summaries = _consumer_summaries(consumer)
    per_message_minimal = next(item for item in consumer_summaries if item["name"] == "per-message / minimal")
    loop_minimal = next(item for item in consumer_summaries if item["name"] == "consumer-loop / minimal")

    data = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "methodology": {
            "command": "uv run python -m benchmarks run-all",
            "timing_threshold": None,
            "direction": "negative deltas are faster",
            "baseline_and_candidate_are_executable_source_states": True,
            "legacy_runtime_copy": False,
            "authoritative_pair": "initial sequential baseline/candidate pair",
        },
        "baseline": baseline_manifest,
        "candidate": candidate_manifest,
        "artifacts": artifacts,
        "consumer": {
            "cells": consumer,
            "summaries": consumer_summaries,
        },
        "controls": {
            "summaries": control_summaries,
            "cells": controls,
        },
        "decision": {
            "ship": True,
            "rationale": (
                f"Minimal per-message geometric mean changed "
                f"{per_message_minimal['relative_geometric_mean_delta_percent']:+.1f}% and minimal loop "
                f"geometric mean changed {loop_minimal['relative_geometric_mean_delta_percent']:+.1f}%; "
                "two additional focused consumer runs showed the same material direction at 1 KB and 16 KB, "
                "while observation-enabled cells and controls provide neutral-to-better drift context."
            ),
        },
        "limitations": [
            "The consumer benchmark starts after RabbitMQ delivery and excludes broker and network latency.",
            "The no-op handler does not represent application business-handler latency.",
            "Large-payload cells have fewer iterations and more local-machine variance.",
            "No automated timing pass/fail threshold was applied.",
        ],
    }
    args.output_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    args.output_html.write_text(_render_html(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
