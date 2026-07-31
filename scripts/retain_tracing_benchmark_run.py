#!/usr/bin/env python3
"""Retain one complete three-mode Relayna tracing benchmark suite."""

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
TRACING_MODES = ("disabled", "enabled-unsampled", "enabled-sampled-exported")
SUMMARY_FILE = "tracing-suite.json"
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
    content = path.read_text(encoding="utf-8")
    if "<!doctype html>" not in content.lower() or "</html>" not in content.lower():
        raise ValueError(f"Benchmark report is not standalone HTML: {path}")
    parser.feed(content)
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
                keys.append(
                    json.dumps(
                        (
                            family,
                            row["profile"],
                            row["input_kind"],
                            row["target_label"],
                            row.get("prefetch"),
                        ),
                        separators=(",", ":"),
                    )
                )
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
    return subprocess.check_output(("git", "-C", str(source_root), *args), text=True).strip()


def _artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "file": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _package_version(source_root: Path) -> str:
    with (source_root / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _optional_command(*command: str) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def _operating_system() -> str:
    if platform.system() == "Darwin":
        version = platform.mac_ver()[0]
        build = _optional_command("sw_vers", "-buildVersion")
        return f"macOS {version} ({build})".strip()
    return platform.platform()


def _cpu_name() -> str:
    if platform.system() == "Darwin":
        return _optional_command("sysctl", "-n", "machdep.cpu.brand_string") or platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")) and ":" in line:
                return line.split(":", maxsplit=1)[1].strip()
    return platform.processor() or platform.machine() or "unknown"


def _raw_report(benchmark: str, html_path: Path) -> dict[str, Any]:
    if benchmark == "consumer-processing":
        return {
            "benchmark": benchmark,
            "data": _embedded(html_path, CONSUMER_PREFIX),
            "schema_version": 1,
            "source": "embedded unrounded report data",
        }
    if benchmark == "publish-preparation":
        return {
            "benchmark": benchmark,
            "data": _embedded(html_path, PUBLISH_PREFIX),
            "schema_version": 1,
            "source": "embedded unrounded report data",
        }
    return {
        "benchmark": benchmark,
        "rows": _primary_table(html_path),
        "schema_version": 1,
        "source": "standalone HTML primary result table",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--suite-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-kind", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--branch-under-test", required=True)
    parser.add_argument("--runtime-base-commit", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--finished-at", required=True)
    parser.add_argument("--source-clean-before-run", action="store_true")
    args = parser.parse_args()

    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite retained run: {args.output_dir}")
    suite_index = json.loads((args.suite_dir / SUMMARY_FILE).read_text(encoding="utf-8"))
    if not suite_index.get("canonical"):
        raise ValueError("Refusing to retain a non-canonical tracing suite.")
    args.output_dir.mkdir(parents=True)

    report_entries: list[dict[str, Any]] = []
    all_case_ids: list[str] = []
    for mode in TRACING_MODES:
        source_mode = args.suite_dir / mode
        output_mode = args.output_dir / mode
        output_mode.mkdir()
        worker_summary = source_mode / SUMMARY_FILE
        shutil.copy2(worker_summary, output_mode / SUMMARY_FILE)
        for benchmark, (html_name, raw_name) in REPORTS.items():
            html_path = output_mode / html_name
            shutil.copy2(source_mode / html_name, html_path)
            raw = _raw_report(benchmark, html_path)
            raw["tracing_mode"] = mode
            raw_path = output_mode / raw_name
            raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            keys = _case_keys(benchmark, raw)
            expected = EXPECTED_COUNTS[benchmark]
            if len(keys) != expected or len(set(keys)) != expected:
                raise ValueError(
                    f"{mode}/{benchmark}: expected {expected} unique cases once, "
                    f"found {len(keys)} rows and {len(set(keys))} unique keys"
                )
            qualified_keys = [f"{mode}/{benchmark}/{key}" for key in keys]
            all_case_ids.extend(qualified_keys)
            report_entries.append(
                {
                    "artifacts": [
                        _artifact(html_path, args.output_dir),
                        _artifact(raw_path, args.output_dir),
                    ],
                    "benchmark": benchmark,
                    "tracing_mode": mode,
                    "measurement_count": len(keys),
                    "unique_case_count": len(set(keys)),
                    "case_ids": qualified_keys,
                }
            )

    expected_total = sum(EXPECTED_COUNTS.values()) * len(TRACING_MODES)
    if len(all_case_ids) != expected_total or len(set(all_case_ids)) != expected_total:
        raise ValueError("Complete tracing suite contains missing or duplicate qualified cases.")
    shutil.copy2(args.suite_dir / SUMMARY_FILE, args.output_dir / SUMMARY_FILE)

    lock_files = ("pyproject.toml", "uv.lock")
    source_commit = _git(args.source_root, "rev-parse", "HEAD")
    manifest = {
        "schema_version": 1,
        "task": "reduce-tracing-overhead",
        "run_id": args.run_id,
        "run_kind": args.run_kind,
        "immutable": True,
        "source": {
            "repository": "sarattha/relayna",
            "commit": source_commit,
            "commit_subject": _git(args.source_root, "show", "-s", "--format=%s", source_commit),
            "runtime_base_commit": args.runtime_base_commit,
            "branch_under_test": args.branch_under_test,
            "clean_before_run": args.source_clean_before_run,
            "status": _git(args.source_root, "status", "--short", "--branch"),
            "runtime_content_sha256": {
                name: _sha256(args.source_root / name)
                for name in (
                    "src/relayna/observability/tracing.py",
                    "src/relayna/rabbitmq/client.py",
                    "src/relayna/consumer/task_consumer.py",
                    "src/relayna/consumer/workflow_consumer.py",
                )
            },
        },
        "execution": {
            "canonical_command": (
                f"uv run --extra benchmark python -m benchmarks.tracing_suite --output-root {args.suite_dir}"
            ),
            "environment_controls": {
                "PYTHONHASHSEED": "0",
                "LC_ALL": "C",
                "LANG": "C",
                "TZ": "UTC",
            },
            "started_at_utc": args.started_at,
            "finished_at_utc": args.finished_at,
            "repetitions": "canonical defaults recorded per raw result/table",
            "warmups": "canonical harness warmups recorded in each standalone report",
            "suite_index": suite_index,
        },
        "environment": {
            "python": f"{platform.python_implementation()} {platform.python_version()}",
            "python_executable": Path(subprocess.check_output(("which", "python3"), text=True).strip()).name,
            "uv": subprocess.check_output(("uv", "--version"), text=True).strip(),
            "os": _operating_system(),
            "kernel": f"{platform.system()} {platform.release()} {platform.machine()}",
            "architecture": platform.machine(),
            "cpu": _cpu_name(),
        },
        "dependency_state": {
            "uv_sync_command": "uv sync --extra benchmark --frozen",
            "lock_sha256": {name: _sha256(args.source_root / name) for name in lock_files},
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "aio-pika",
                "pydantic",
                "pydantic-core",
                "orjson",
                "opentelemetry-api",
                "opentelemetry-sdk",
                "opentelemetry-semantic-conventions",
            )
        }
        | {"relayna": _package_version(args.source_root)},
        "tracing": {
            "modes": list(TRACING_MODES),
            "disabled": "OpenTelemetry API no-op provider; Relayna instrumentation still invoked",
            "enabled-unsampled": "TracerProvider with ALWAYS_OFF and SimpleSpanProcessor",
            "enabled-sampled-exported": (
                "TracerProvider with ALWAYS_ON, SimpleSpanProcessor, and synchronous non-retaining CountingExporter"
            ),
            "propagator": "process default composite W3C TraceContext and baggage",
        },
        "reports": report_entries,
        "validation": {
            "expected_benchmarks": list(REPORTS),
            "expected_tracing_modes": list(TRACING_MODES),
            "report_count": len(report_entries),
            "expected_total_measurements": expected_total,
            "observed_total_measurements": len(all_case_ids),
            "unique_qualified_case_count": len(set(all_case_ids)),
            "all_expected_cases_present_once": True,
            "standalone_html_validated": True,
            "raw_measurements_hand_edited": False,
        },
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = [
        path for path in sorted(args.output_dir.rglob("*")) if path.is_file() and path.name != "checksums.sha256"
    ]
    checksum_path = args.output_dir / "checksums.sha256"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(args.output_dir)}\n" for path in artifacts),
        encoding="utf-8",
    )
    print(f"Retained {len(all_case_ids)} unique tracing-suite measurements in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
