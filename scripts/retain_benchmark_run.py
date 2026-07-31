#!/usr/bin/env python3
"""Retain one complete Relayna benchmark run with raw data and checksums."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.metadata
import json
import platform
import shutil
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

CONSUMER_PREFIX = "<!-- relayna-consumer-processing-data:"
PUBLISH_PREFIX = "<!-- relayna-publish-preparation-data:"
EMBEDDED_SUFFIX = ":end -->"
EXPECTED_COUNTS = {
    "consumer-processing": 40,
    "envelope-serialization": 32,
    "json-engine-evaluation": 192,
    "publish-preparation": 72,
    "redis-storage-cpu": 72,
}
REPORTS = {
    "consumer-processing": ("consumer-processing.html", "consumer-processing.raw.json"),
    "envelope-serialization": ("envelope-microbenchmarks.html", "envelope-serialization.raw.json"),
    "json-engine-evaluation": ("json-engine-evaluation.html", "json-engine-evaluation.raw.json"),
    "publish-preparation": ("publish-preparation.html", "publish-preparation.raw.json"),
    "redis-storage-cpu": ("redis-storage-cpu-microbenchmarks.html", "redis-storage-cpu.raw.json"),
}


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _embedded(path: Path, prefix: str) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    start = content.find(prefix)
    end = content.find(EMBEDDED_SUFFIX, start)
    if start < 0 or end < 0:
        raise ValueError(f"Missing embedded benchmark data in {path}")
    return json.loads(html.unescape(content[start + len(prefix) : end].strip()))


def _primary_table(path: Path) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    for table in parser.tables:
        if table and "Median µs/op" in table[0]:
            headers = table[0]
            rows = table[1:]
            if any(len(row) != len(headers) for row in rows):
                raise ValueError(f"Malformed primary result table in {path}")
            return [dict(zip(headers, row, strict=True)) for row in rows]
    raise ValueError(f"Missing primary result table in {path}")


def _case_keys(benchmark: str, raw: dict[str, Any]) -> list[str]:
    if benchmark == "consumer-processing":
        keys: list[str] = []
        for family, field in (
            ("per-message", "per_message_results"),
            ("consumer-loop", "consumer_loop_results"),
        ):
            for row in raw["data"][field]:
                key = (
                    family,
                    row["profile"],
                    row["input_kind"],
                    row["target_label"],
                    row.get("prefetch"),
                )
                keys.append(json.dumps(key, separators=(",", ":")))
        return keys
    if benchmark == "publish-preparation":
        fields = ("message_kind", "input_kind", "topology", "target_label")
        return [
            json.dumps(tuple(row[field] for field in fields), separators=(",", ":")) for row in raw["data"]["results"]
        ]

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
    return [
        json.dumps(
            {key: value for key, value in sorted(row.items()) if key not in ignored},
            separators=(",", ":"),
        )
        for row in raw["rows"]
    ]


def _git(source_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ("git", "-C", str(source_root), *args),
        text=True,
    ).strip()


def _artifact(path: Path) -> dict[str, Any]:
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _package_version(source_root: Path) -> str:
    with (source_root / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-kind", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--branch-under-test", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--source-subject")
    parser.add_argument("--source-state-note")
    parser.add_argument("--source-content-file", action="append", default=[])
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--finished-at", required=True)
    parser.add_argument("--source-clean-before-run", action="store_true")
    args = parser.parse_args()

    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite retained run: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    source_reports = args.source_root / "reports"
    report_entries: list[dict[str, Any]] = []

    for benchmark, (html_name, raw_name) in REPORTS.items():
        html_path = args.output_dir / html_name
        shutil.copy2(source_reports / html_name, html_path)
        if benchmark == "consumer-processing":
            raw = {
                "benchmark": benchmark,
                "data": _embedded(html_path, CONSUMER_PREFIX),
                "schema_version": 1,
                "source": "embedded unrounded report data",
            }
        elif benchmark == "publish-preparation":
            raw = {
                "benchmark": benchmark,
                "data": _embedded(html_path, PUBLISH_PREFIX),
                "schema_version": 1,
                "source": "embedded unrounded report data",
            }
        else:
            raw = {
                "benchmark": benchmark,
                "rows": _primary_table(html_path),
                "schema_version": 1,
                "source": ("standalone HTML primary result table (harness does not embed unrounded samples)"),
            }
        raw_path = args.output_dir / raw_name
        raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        keys = _case_keys(benchmark, raw)
        expected = EXPECTED_COUNTS[benchmark]
        if len(keys) != expected or len(set(keys)) != expected:
            raise ValueError(
                f"{benchmark}: expected {expected} unique cases once, "
                f"found {len(keys)} rows and {len(set(keys))} unique keys"
            )
        report_entries.append(
            {
                "artifacts": [_artifact(html_path), _artifact(raw_path)],
                "benchmark": benchmark,
                "html_file": html_name,
                "measurement_count": len(keys),
                "raw_file": raw_name,
                "unique_case_count": len(set(keys)),
            }
        )

    lock_files = (
        "pyproject.toml",
        "uv.lock",
        "studio/backend/uv.lock",
        "apps/studio/package-lock.json",
    )
    commit = args.source_commit or _git(args.source_root, "rev-parse", "HEAD")
    commit_subject = args.source_subject or _git(args.source_root, "show", "-s", "--format=%s", commit)
    manifest = {
        "schema_version": 1,
        "task": "extract-message-metadata-once",
        "run_id": args.run_id,
        "run_kind": args.run_kind,
        "immutable": True,
        "source": {
            "repository": "sarattha/relayna",
            "commit": commit,
            "commit_subject": commit_subject,
            "ref": args.source_ref,
            "branch_under_test": args.branch_under_test,
            "clean_before_run": args.source_clean_before_run,
            "state_note": args.source_state_note,
            "content_sha256": {name: _sha256(args.source_root / name) for name in args.source_content_file},
        },
        "execution": {
            "canonical_command": "uv run --extra benchmark python -m benchmarks run-all",
            "make_equivalent": "make benchmark-all",
            "environment_controls": {
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C",
                "LANG": "C",
                "TZ": "UTC",
            },
            "started_at_utc": args.started_at,
            "finished_at_utc": args.finished_at,
            "repetitions": "harness canonical defaults recorded per raw result/table",
            "warmups": (
                "harness canonical defaults; consumer-loop one untimed warm-up per cell; "
                "JSON-engine one warm-up call per operation"
            ),
        },
        "environment": {
            "python": f"{platform.python_implementation()} {platform.python_version()}",
            "uv": subprocess.check_output(("uv", "--version"), text=True).strip().removeprefix("uv "),
            "os": "macOS 26.5.2 (25F84)",
            "kernel": platform.platform(),
            "architecture": platform.machine(),
            "cpu": subprocess.check_output(("sysctl", "-n", "machdep.cpu.brand_string"), text=True).strip(),
        },
        "dependency_state": {
            "uv_sync_command": "uv sync --extra benchmark --frozen",
            "lock_sha256": {
                name: _sha256(args.source_root / name) for name in lock_files if (args.source_root / name).exists()
            },
        },
        "packages": {
            name: importlib.metadata.version(name) for name in ("aio-pika", "pydantic", "pydantic-core", "orjson")
        }
        | {"relayna": _package_version(args.source_root)},
        "reports": report_entries,
        "validation": {
            "expected_benchmarks": list(REPORTS),
            "benchmark_count": len(REPORTS),
            "expected_total_measurements": sum(EXPECTED_COUNTS.values()),
            "observed_total_measurements": sum(item["measurement_count"] for item in report_entries),
            "all_expected_cases_present_once": True,
            "raw_measurements_hand_edited": False,
        },
        "notes": [
            "HTML reports are exact harness outputs.",
            (
                "Consumer and publish raw sidecars preserve embedded unrounded samples; "
                "the other three harnesses expose rounded standalone result tables and "
                "those tables are preserved losslessly as machine-readable JSON."
            ),
        ],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = [path for path in sorted(args.output_dir.iterdir()) if path.name != "checksums.sha256"]
    checksum_path = args.output_dir / "checksums.sha256"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in artifacts),
        encoding="utf-8",
    )
    print(f"Retained {manifest['validation']['observed_total_measurements']} unique measurements in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
