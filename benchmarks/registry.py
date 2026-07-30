"""Shared registration contracts for Relayna benchmark types."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BenchmarkArgumentBuilder = Callable[[argparse.ArgumentParser], None]
BenchmarkArgumentPreparer = Callable[[argparse.Namespace], None]


@dataclass(frozen=True)
class BenchmarkOutcome:
    """Artifacts and measurement count produced by one benchmark run."""

    artifacts: tuple[Path, ...]
    measurement_count: int


@dataclass(frozen=True)
class BenchmarkDefinition:
    """CLI metadata and execution hooks supplied by one benchmark module."""

    name: str
    summary: str
    default_output: Path
    add_arguments: BenchmarkArgumentBuilder
    run: Callable[[argparse.Namespace], BenchmarkOutcome]
    prepare_run_all: BenchmarkArgumentPreparer | None = None


def _validate_definitions(definitions: tuple[BenchmarkDefinition, ...]) -> tuple[BenchmarkDefinition, ...]:
    names: set[str] = set()
    for definition in definitions:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", definition.name):
            raise ValueError(f"Benchmark name must be lowercase kebab-case: {definition.name!r}")
        if definition.name in names:
            raise ValueError(f"Duplicate benchmark name: {definition.name}")
        if definition.default_output.is_absolute():
            raise ValueError(f"Default benchmark output must be repository-relative: {definition.default_output}")
        names.add(definition.name)
    return tuple(sorted(definitions, key=lambda definition: definition.name))


def registered_benchmarks() -> tuple[BenchmarkDefinition, ...]:
    """Return every benchmark exposed by the repository CLI."""

    from benchmarks.envelope_serialization import BENCHMARK
    from benchmarks.json_engine_evaluation import BENCHMARK as JSON_ENGINE_BENCHMARK
    from benchmarks.publish_preparation import BENCHMARK as PUBLISH_PREPARATION_BENCHMARK
    from benchmarks.redis_storage_cpu import BENCHMARK as REDIS_STORAGE_CPU_BENCHMARK

    return _validate_definitions(
        (
            BENCHMARK,
            JSON_ENGINE_BENCHMARK,
            PUBLISH_PREPARATION_BENCHMARK,
            REDIS_STORAGE_CPU_BENCHMARK,
        )
    )
