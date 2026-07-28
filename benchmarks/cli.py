"""Discover and run Relayna repository benchmarks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome, registered_benchmarks


def _build_parser(definitions: Sequence[BenchmarkDefinition]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks",
        description="Discover and run reproducible Relayna repository benchmarks.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List registered benchmark types.")

    run_parser = commands.add_parser("run", help="Run one benchmark type.")
    benchmark_parsers = run_parser.add_subparsers(dest="benchmark_name", required=True)
    for definition in definitions:
        benchmark_parser = benchmark_parsers.add_parser(
            definition.name,
            help=definition.summary,
            description=definition.summary,
        )
        definition.add_arguments(benchmark_parser)
        benchmark_parser.set_defaults(benchmark_definition=definition)

    commands.add_parser("run-all", help="Run every registered benchmark with canonical defaults.")
    return parser


def _print_benchmarks(definitions: Sequence[BenchmarkDefinition]) -> None:
    name_width = max((len(definition.name) for definition in definitions), default=4)
    print(f"{'NAME':<{name_width}}  DEFAULT REPORT  DESCRIPTION")
    for definition in definitions:
        print(f"{definition.name:<{name_width}}  {definition.default_output}  {definition.summary}")


def _print_outcome(definition: BenchmarkDefinition, outcome: BenchmarkOutcome) -> None:
    if outcome.measurement_count < 1:
        raise RuntimeError(f"Benchmark {definition.name} produced no measurements.")
    if not outcome.artifacts:
        raise RuntimeError(f"Benchmark {definition.name} produced no artifacts.")
    missing_artifacts = [artifact for artifact in outcome.artifacts if not artifact.is_file()]
    if missing_artifacts:
        raise RuntimeError(f"Benchmark {definition.name} did not write expected artifacts: {missing_artifacts}")
    artifact_list = ", ".join(str(artifact.resolve()) for artifact in outcome.artifacts)
    print(f"Completed {definition.name}: {outcome.measurement_count} measurements -> {artifact_list}")


def _default_arguments(definition: BenchmarkDefinition) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    definition.add_arguments(parser)
    return parser.parse_args([])


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the benchmark CLI."""

    definitions = registered_benchmarks()
    args = _build_parser(definitions).parse_args(argv)
    if args.command == "list":
        _print_benchmarks(definitions)
        return 0
    if args.command == "run":
        definition: BenchmarkDefinition = args.benchmark_definition
        _print_outcome(definition, definition.run(args))
        return 0
    for definition in definitions:
        _print_outcome(definition, definition.run(_default_arguments(definition)))
    return 0
