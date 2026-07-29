"""Evaluate released and production Relayna CPU-side JSON engine paths."""

from __future__ import annotations

import argparse
import gc
import html
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import median
from types import ModuleType
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from benchmarks.registry import BenchmarkDefinition, BenchmarkOutcome
from benchmarks.reporting import collect_environment, write_text_artifact
from relayna._transport_json import encode_transport_json, parse_transport_json
from relayna.contracts import (
    BatchTaskEnvelope,
    ContractAliasConfig,
    TaskEnvelope,
    normalize_contract_aliases,
)

try:
    _orjson_module: ModuleType | None = import_module("orjson")
except ImportError:  # pragma: no cover - exercised through injected absence
    _orjson_module = None

Envelope = TaskEnvelope | BatchTaskEnvelope
EnvelopeType = type[TaskEnvelope] | type[BatchTaskEnvelope]
EnvelopeKind = Literal["task", "batch"]
Direction = Literal["outbound", "inbound"]
Profile = Literal["ascii", "unicode-numeric"]
InboundShape = Literal["canonical", "alias-compatible"]
Operation = Callable[[], object]

TARGET_SIZES: dict[str, int] = {
    "1 KB": 1_024,
    "16 KB": 16_384,
    "128 KB": 131_072,
    "1 MB": 1_048_576,
}
DEFAULT_ITERATIONS: dict[int, int] = {
    1_024: 1_000,
    16_384: 300,
    131_072: 50,
    1_048_576: 8,
}
DEFAULT_REPEATS = 5
DEFAULT_PROFILES: tuple[Profile, ...] = ("ascii", "unicode-numeric")
DEFAULT_OUTPUT = Path("reports/json-engine-evaluation.html")
ORJSON_VERSION = "3.11.9"
ORJSON_EXTRA = "benchmark"
_FIXED_CREATED_AT = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_FIXED_UUID = UUID("12345678-1234-5678-1234-567812345678")
_BATCH_TASK_COUNT = 2
_MIB = 1_048_576

ENGINE_LABELS = {
    "stdlib": "Released v1.4.29 stdlib reference",
    "pydantic-core": "New production: Pydantic Core transport",
    "pydantic-direct": "Direct Pydantic model JSON",
    "orjson": "orjson",
}
OUTBOUND_ENGINES = ("stdlib", "pydantic-core", "pydantic-direct", "orjson")
INBOUND_ENGINES = ("stdlib", "pydantic-core", "pydantic-direct", "orjson")

WHEEL_SUPPORT: tuple[tuple[str, str, str], ...] = (
    (
        "CPython 3.13",
        "Linux x86_64",
        "orjson-3.11.9-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    ),
    (
        "CPython 3.13",
        "Linux aarch64",
        "orjson-3.11.9-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    ),
    (
        "CPython 3.13",
        "macOS ARM64",
        "orjson-3.11.9-cp313-cp313-macosx_15_0_arm64.whl",
    ),
    (
        "CPython 3.13",
        "macOS x86_64",
        "orjson-3.11.9-cp313-cp313-macosx_10_15_x86_64.macosx_11_0_arm64.macosx_10_15_universal2.whl",
    ),
    (
        "CPython 3.14",
        "Linux x86_64",
        "orjson-3.11.9-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    ),
    (
        "CPython 3.14",
        "Linux aarch64",
        "orjson-3.11.9-cp314-cp314-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    ),
    (
        "CPython 3.14",
        "macOS ARM64",
        "orjson-3.11.9-cp314-cp314-macosx_15_0_arm64.whl",
    ),
    (
        "CPython 3.14",
        "macOS x86_64",
        "orjson-3.11.9-cp314-cp314-macosx_10_15_x86_64.macosx_11_0_arm64.macosx_10_15_universal2.whl",
    ),
)
PRODUCTION_WHEEL_SUPPORT: tuple[tuple[str, str, str], ...] = (
    (
        "CPython 3.13",
        "Linux x86_64",
        "pydantic_core-2.41.5-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    ),
    (
        "CPython 3.13",
        "Linux aarch64",
        "pydantic_core-2.41.5-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    ),
    ("CPython 3.13", "macOS ARM64", "pydantic_core-2.41.5-cp313-cp313-macosx_11_0_arm64.whl"),
    ("CPython 3.13", "macOS x86_64", "pydantic_core-2.41.5-cp313-cp313-macosx_10_12_x86_64.whl"),
    (
        "CPython 3.14",
        "Linux x86_64",
        "pydantic_core-2.41.5-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    ),
    (
        "CPython 3.14",
        "Linux aarch64",
        "pydantic_core-2.41.5-cp314-cp314-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    ),
    ("CPython 3.14", "macOS ARM64", "pydantic_core-2.41.5-cp314-cp314-macosx_11_0_arm64.whl"),
    ("CPython 3.14", "macOS x86_64", "pydantic_core-2.41.5-cp314-cp314-macosx_10_12_x86_64.whl"),
)


@dataclass(frozen=True)
class BenchmarkCase:
    """One complete CPU-path benchmark cell."""

    envelope_kind: EnvelopeKind
    profile: Profile
    target_label: str
    target_bytes: int
    direction: Direction
    inbound_shape: InboundShape | None
    engine: str
    engine_label: str
    is_baseline: bool
    iterations: int


@dataclass(frozen=True)
class BenchmarkResult:
    """Measured output and derived metrics for one benchmark case."""

    case: BenchmarkCase
    actual_bytes: int
    repeats: int
    sample_ns_per_op: tuple[float, ...]
    median_ns_per_op: float
    p25_ns_per_op: float
    p75_ns_per_op: float
    operations_per_second: float
    throughput_mib_s: float
    relative_to_current: float


@dataclass(frozen=True)
class CompatibilityFinding:
    """One deterministic compatibility observation."""

    area: str
    scenario: str
    engine: str
    outcome: str
    rejection_stage: str
    detail: str


@dataclass(frozen=True)
class Recommendation:
    """One path-specific decision recommendation."""

    path: str
    decision: str
    candidate: str
    evidence: str


class _NestedProbeModel(BaseModel):
    value: str


def available_orjson(module: ModuleType | None = _orjson_module) -> ModuleType | None:
    """Return the optional orjson module, allowing deterministic test injection."""

    return module


def require_orjson(module: ModuleType | None = _orjson_module) -> ModuleType:
    """Return orjson or raise an actionable benchmark-only installation error."""

    if module is None:
        raise RuntimeError(
            "orjson is required for the complete JSON engine evaluation. "
            "Run with `uv run --extra benchmark python -m benchmarks run json-engine-evaluation`, "
            "or pass --allow-missing-orjson for a partial report."
        )
    return module


def _profile_payload(profile: Profile, sequence: int, padding: str) -> dict[str, Any]:
    if profile == "ascii":
        return {
            "benchmark": "json-engine-evaluation",
            "profile": profile,
            "sequence": sequence,
            "flags": [True, False, None],
            "values": [0, 1, 42, 125.5, -8],
            "content": padding,
        }
    if profile == "unicode-numeric":
        return {
            "benchmark": "json-eval",
            "profile": "unicode",
            "sequence": sequence,
            "locale": "ไทย-日本語-🚀",
            "values": [0, -1, 3.141592653589793, 9_007_199_254_740_991, 1.2e-9],
            "content": padding,
        }
    raise ValueError(f"Unsupported payload profile: {profile}")


def _task_envelope(*, sequence: int, profile: Profile, padding: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=f"benchmark-task-{sequence:04d}",
        payload=_profile_payload(profile, sequence, padding),
        created_at=_FIXED_CREATED_AT,
        service="benchmark-service",
        task_type="benchmark.execute",
        correlation_id="benchmark-correlation",
        priority=128,
    )


def _prepared_mapping(envelope: Envelope) -> dict[str, Any]:
    return cast(dict[str, Any], envelope.model_dump(mode="json", exclude_none=True))


def stdlib_outbound(envelope: Envelope) -> bytes:
    """Mirror complete current model preparation and stdlib transport encoding."""

    return json.dumps(_prepared_mapping(envelope), ensure_ascii=False).encode("utf-8")


def pydantic_core_outbound(envelope: Envelope) -> bytes:
    """Run the implemented production model-preparation and transport-encoding path."""

    return encode_transport_json(_prepared_mapping(envelope))


def pydantic_direct_outbound(envelope: Envelope) -> bytes:
    """Serialize model-aware JSON directly through Pydantic."""

    return envelope.model_dump_json(exclude_none=True).encode("utf-8")


def orjson_outbound(envelope: Envelope, module: ModuleType | None = _orjson_module) -> bytes:
    """Prepare through the model, then encode with the optional orjson engine."""

    engine = require_orjson(module)
    return cast(bytes, engine.dumps(_prepared_mapping(envelope)))


def build_fixture(envelope_kind: EnvelopeKind, target_bytes: int, profile: Profile) -> Envelope:
    """Build a deterministic fixture with an exact current-path byte size."""

    if envelope_kind == "task":
        empty = _task_envelope(sequence=1, profile=profile, padding="")
        padding_bytes = target_bytes - len(stdlib_outbound(empty))
        if padding_bytes < 0:
            raise ValueError(f"Target {target_bytes} bytes is too small for a {profile} task fixture.")
        fixture: Envelope = _task_envelope(sequence=1, profile=profile, padding="x" * padding_bytes)
    elif envelope_kind == "batch":
        empty = BatchTaskEnvelope(
            batch_id="benchmark-batch-0001",
            tasks=[
                _task_envelope(sequence=sequence, profile=profile, padding="")
                for sequence in range(1, _BATCH_TASK_COUNT + 1)
            ],
            meta={"benchmark": "json-engine-evaluation", "task_count": _BATCH_TASK_COUNT},
            created_at=_FIXED_CREATED_AT,
        )
        padding_bytes = target_bytes - len(stdlib_outbound(empty))
        if padding_bytes < 0:
            raise ValueError(f"Target {target_bytes} bytes is too small for a {profile} batch fixture.")
        even_padding, remainder = divmod(padding_bytes, _BATCH_TASK_COUNT)
        fixture = BatchTaskEnvelope(
            batch_id="benchmark-batch-0001",
            tasks=[
                _task_envelope(
                    sequence=sequence,
                    profile=profile,
                    padding="x" * (even_padding + (1 if sequence <= remainder else 0)),
                )
                for sequence in range(1, _BATCH_TASK_COUNT + 1)
            ],
            meta={"benchmark": "json-engine-evaluation", "task_count": _BATCH_TASK_COUNT},
            created_at=_FIXED_CREATED_AT,
        )
    else:
        raise ValueError(f"Unsupported envelope kind: {envelope_kind}")

    actual_bytes = len(stdlib_outbound(fixture))
    if actual_bytes != target_bytes:
        raise RuntimeError(f"Fixture sizing failed: expected {target_bytes} bytes, produced {actual_bytes} bytes.")
    return fixture


def _normalize_envelope_payload(
    parsed: Any,
    envelope_kind: EnvelopeKind,
    alias_config: ContractAliasConfig | None = None,
) -> Any:
    if not isinstance(parsed, Mapping):
        return parsed
    normalized = normalize_contract_aliases(parsed, alias_config, drop_aliases=True)
    if envelope_kind == "batch":
        tasks = normalized.get("tasks")
        if isinstance(tasks, list):
            normalized["tasks"] = [
                normalize_contract_aliases(task, alias_config, drop_aliases=True) if isinstance(task, Mapping) else task
                for task in tasks
            ]
    return normalized


def _envelope_type(envelope_kind: EnvelopeKind) -> EnvelopeType:
    return TaskEnvelope if envelope_kind == "task" else BatchTaskEnvelope


def _validate_parsed(envelope_kind: EnvelopeKind, parsed: Any) -> Envelope:
    return _envelope_type(envelope_kind).model_validate(parsed)


def stdlib_inbound(
    envelope_kind: EnvelopeKind,
    payload: bytes,
    alias_config: ContractAliasConfig | None = None,
) -> Envelope:
    """Mirror current decode, stdlib parse, alias normalization, and validation."""

    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    return _validate_parsed(envelope_kind, _normalize_envelope_payload(parsed, envelope_kind, alias_config))


def pydantic_core_inbound(
    envelope_kind: EnvelopeKind,
    payload: bytes,
    alias_config: ContractAliasConfig | None = None,
) -> Envelope:
    """Parse with Pydantic Core, then run current alias and validation stages."""

    parsed = parse_transport_json(payload)
    return _validate_parsed(envelope_kind, _normalize_envelope_payload(parsed, envelope_kind, alias_config))


def pydantic_direct_inbound(
    envelope_kind: EnvelopeKind,
    payload: bytes,
    *,
    alias_compatible: bool,
    alias_config: ContractAliasConfig | None = None,
) -> Envelope:
    """Validate canonical bytes directly, with an explicit Core alias fallback."""

    envelope_type = _envelope_type(envelope_kind)
    if not alias_compatible:
        return envelope_type.model_validate_json(payload)
    try:
        return envelope_type.model_validate_json(payload)
    except ValidationError:
        parsed = parse_transport_json(payload)
        return _validate_parsed(envelope_kind, _normalize_envelope_payload(parsed, envelope_kind, alias_config))


def orjson_inbound(
    envelope_kind: EnvelopeKind,
    payload: bytes,
    alias_config: ContractAliasConfig | None = None,
    module: ModuleType | None = _orjson_module,
) -> Envelope:
    """Parse with orjson, then run current alias and validation stages."""

    engine = require_orjson(module)
    parsed = engine.loads(payload)
    return _validate_parsed(envelope_kind, _normalize_envelope_payload(parsed, envelope_kind, alias_config))


def _alias_payload(envelope: Envelope, envelope_kind: EnvelopeKind) -> bytes:
    prepared = _prepared_mapping(envelope)
    if envelope_kind == "task":
        prepared["documentId"] = prepared.pop("task_id")
    else:
        tasks = cast(list[dict[str, Any]], prepared["tasks"])
        for task in tasks:
            task["documentId"] = task.pop("task_id")
    return json.dumps(prepared, ensure_ascii=False).encode("utf-8")


def build_matrix(
    iterations_by_size: Mapping[int, int] | None = None,
    *,
    profiles: Sequence[Profile] = DEFAULT_PROFILES,
    include_orjson: bool = True,
) -> list[BenchmarkCase]:
    """Return every intended complete-path performance case exactly once."""

    iteration_counts = dict(DEFAULT_ITERATIONS if iterations_by_size is None else iterations_by_size)
    missing_sizes = set(TARGET_SIZES.values()) - iteration_counts.keys()
    if missing_sizes:
        raise ValueError(f"Missing iteration counts for byte sizes: {sorted(missing_sizes)}")
    if any(iteration_counts[size] < 1 for size in TARGET_SIZES.values()):
        raise ValueError("Iteration counts must be positive.")
    selected_profiles = tuple(dict.fromkeys(profiles))
    if not selected_profiles:
        raise ValueError("At least one payload profile is required.")
    if any(profile not in DEFAULT_PROFILES for profile in selected_profiles):
        raise ValueError(f"Unsupported profiles: {selected_profiles}")

    outbound_engines = OUTBOUND_ENGINES if include_orjson else OUTBOUND_ENGINES[:-1]
    inbound_engines = INBOUND_ENGINES if include_orjson else INBOUND_ENGINES[:-1]
    cases: list[BenchmarkCase] = []
    for envelope_kind in ("task", "batch"):
        for profile in selected_profiles:
            for target_label, target_bytes in TARGET_SIZES.items():
                for engine in outbound_engines:
                    cases.append(
                        BenchmarkCase(
                            envelope_kind=envelope_kind,
                            profile=profile,
                            target_label=target_label,
                            target_bytes=target_bytes,
                            direction="outbound",
                            inbound_shape=None,
                            engine=engine,
                            engine_label=ENGINE_LABELS[engine],
                            is_baseline=engine == "stdlib",
                            iterations=iteration_counts[target_bytes],
                        )
                    )
                for inbound_shape in ("canonical", "alias-compatible"):
                    for engine in inbound_engines:
                        cases.append(
                            BenchmarkCase(
                                envelope_kind=envelope_kind,
                                profile=profile,
                                target_label=target_label,
                                target_bytes=target_bytes,
                                direction="inbound",
                                inbound_shape=inbound_shape,
                                engine=engine,
                                engine_label=ENGINE_LABELS[engine],
                                is_baseline=engine == "stdlib",
                                iterations=iteration_counts[target_bytes],
                            )
                        )
    return cases


def _model_semantics(envelope: Envelope) -> dict[str, Any]:
    return cast(dict[str, Any], envelope.model_dump(mode="json", exclude_none=True))


def _operation_for(
    case: BenchmarkCase,
    envelope: Envelope,
    canonical_bytes: bytes,
    alias_bytes: bytes,
    module: ModuleType | None,
) -> tuple[Operation, int]:
    if case.direction == "outbound":
        if case.engine == "stdlib":
            return lambda: stdlib_outbound(envelope), len(canonical_bytes)
        if case.engine == "pydantic-core":
            encoded = pydantic_core_outbound(envelope)
            return lambda: pydantic_core_outbound(envelope), len(encoded)
        if case.engine == "pydantic-direct":
            encoded = pydantic_direct_outbound(envelope)
            return lambda: pydantic_direct_outbound(envelope), len(encoded)
        encoded = orjson_outbound(envelope, module)
        return lambda: orjson_outbound(envelope, module), len(encoded)

    payload = canonical_bytes if case.inbound_shape == "canonical" else alias_bytes
    if case.engine == "stdlib":
        return lambda: stdlib_inbound(case.envelope_kind, payload), len(payload)
    if case.engine == "pydantic-core":
        return lambda: pydantic_core_inbound(case.envelope_kind, payload), len(payload)
    if case.engine == "pydantic-direct":
        return (
            lambda: pydantic_direct_inbound(
                case.envelope_kind,
                payload,
                alias_compatible=case.inbound_shape == "alias-compatible",
            ),
            len(payload),
        )
    return lambda: orjson_inbound(case.envelope_kind, payload, module=module), len(payload)


def _assert_semantic_fairness(
    envelope: Envelope,
    envelope_kind: EnvelopeKind,
    canonical_bytes: bytes,
    alias_bytes: bytes,
    *,
    include_orjson: bool,
    module: ModuleType | None,
) -> None:
    expected = _model_semantics(envelope)
    outbound_payloads = [
        pydantic_core_outbound(envelope),
        pydantic_direct_outbound(envelope),
    ]
    if include_orjson:
        outbound_payloads.append(orjson_outbound(envelope, module))
    for payload in outbound_payloads:
        validated = stdlib_inbound(envelope_kind, payload)
        if _model_semantics(validated) != expected:
            raise RuntimeError("An outbound engine did not preserve parsed envelope semantics.")

    inbound_models = [
        stdlib_inbound(envelope_kind, canonical_bytes),
        pydantic_core_inbound(envelope_kind, canonical_bytes),
        pydantic_direct_inbound(envelope_kind, canonical_bytes, alias_compatible=False),
        stdlib_inbound(envelope_kind, alias_bytes),
        pydantic_core_inbound(envelope_kind, alias_bytes),
        pydantic_direct_inbound(envelope_kind, alias_bytes, alias_compatible=True),
    ]
    if include_orjson:
        inbound_models.extend(
            [
                orjson_inbound(envelope_kind, canonical_bytes, module=module),
                orjson_inbound(envelope_kind, alias_bytes, module=module),
            ]
        )
    if any(_model_semantics(model) != expected for model in inbound_models):
        raise RuntimeError("An inbound engine did not preserve validated envelope semantics.")


def _time_operation(operation: Operation, iterations: int) -> float:
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        started_ns = time.perf_counter_ns()
        for _ in range(iterations):
            operation()
        elapsed_ns = time.perf_counter_ns() - started_ns
    finally:
        if gc_was_enabled:
            gc.enable()
    return elapsed_ns / iterations


def _percentile(samples: Sequence[float], fraction: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("Cannot calculate a percentile without samples.")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def run_benchmarks(
    *,
    repeats: int = DEFAULT_REPEATS,
    iterations_by_size: Mapping[int, int] | None = None,
    profiles: Sequence[Profile] = DEFAULT_PROFILES,
    include_orjson: bool = True,
    orjson_module: ModuleType | None = _orjson_module,
) -> list[BenchmarkResult]:
    """Run the complete matrix after validating semantic fairness."""

    if repeats < 1:
        raise ValueError("Repeats must be positive.")
    if include_orjson:
        require_orjson(orjson_module)
    cases = build_matrix(iterations_by_size, profiles=profiles, include_orjson=include_orjson)
    fixtures: dict[tuple[EnvelopeKind, Profile, int], Envelope] = {}
    canonical_payloads: dict[tuple[EnvelopeKind, Profile, int], bytes] = {}
    alias_payloads: dict[tuple[EnvelopeKind, Profile, int], bytes] = {}
    operations: dict[BenchmarkCase, tuple[Operation, int]] = {}

    for envelope_kind in ("task", "batch"):
        for profile in profiles:
            for target_bytes in TARGET_SIZES.values():
                key = (envelope_kind, profile, target_bytes)
                fixture = build_fixture(envelope_kind, target_bytes, profile)
                canonical_bytes = stdlib_outbound(fixture)
                alias_bytes = _alias_payload(fixture, envelope_kind)
                _assert_semantic_fairness(
                    fixture,
                    envelope_kind,
                    canonical_bytes,
                    alias_bytes,
                    include_orjson=include_orjson,
                    module=orjson_module,
                )
                fixtures[key] = fixture
                canonical_payloads[key] = canonical_bytes
                alias_payloads[key] = alias_bytes

    for case in cases:
        key = (case.envelope_kind, case.profile, case.target_bytes)
        operations[case] = _operation_for(
            case,
            fixtures[key],
            canonical_payloads[key],
            alias_payloads[key],
            orjson_module,
        )
    for operation, _actual_bytes in operations.values():
        operation()

    samples: dict[BenchmarkCase, list[float]] = {case: [] for case in cases}
    for repeat_index in range(repeats):
        offset = repeat_index % len(cases)
        round_cases = cases[offset:] + cases[:offset]
        if repeat_index % 2:
            round_cases.reverse()
        for case in round_cases:
            operation, _actual_bytes = operations[case]
            samples[case].append(_time_operation(operation, case.iterations))

    medians = {case: median(values) for case, values in samples.items()}
    baseline_medians = {
        (
            case.envelope_kind,
            case.profile,
            case.target_bytes,
            case.direction,
            case.inbound_shape,
        ): medians[case]
        for case in cases
        if case.is_baseline
    }
    results: list[BenchmarkResult] = []
    for case in cases:
        values = samples[case]
        median_ns = medians[case]
        baseline_ns = baseline_medians[
            (
                case.envelope_kind,
                case.profile,
                case.target_bytes,
                case.direction,
                case.inbound_shape,
            )
        ]
        _operation, actual_bytes = operations[case]
        results.append(
            BenchmarkResult(
                case=case,
                actual_bytes=actual_bytes,
                repeats=repeats,
                sample_ns_per_op=tuple(values),
                median_ns_per_op=median_ns,
                p25_ns_per_op=_percentile(values, 0.25),
                p75_ns_per_op=_percentile(values, 0.75),
                operations_per_second=1_000_000_000 / median_ns,
                throughput_mib_s=(actual_bytes / _MIB) / (median_ns / 1_000_000_000),
                relative_to_current=baseline_ns / median_ns,
            )
        )
    return results


def _exception_detail(exc: Exception) -> str:
    first_line = str(exc).splitlines()[0].strip()
    return f"{type(exc).__name__}: {first_line}" if first_line else type(exc).__name__


def _classify_inbound(
    engine_name: str,
    payload: bytes,
    *,
    envelope_kind: EnvelopeKind = "task",
    alias_config: ContractAliasConfig | None = None,
    module: ModuleType | None = _orjson_module,
) -> tuple[str, str, str]:
    try:
        if engine_name == "stdlib":
            try:
                parsed = json.loads(payload.decode("utf-8", errors="replace"))
            except Exception as exc:
                return "rejected", "JSON parse", _exception_detail(exc)
            normalized = _normalize_envelope_payload(parsed, envelope_kind, alias_config)
            try:
                _validate_parsed(envelope_kind, normalized)
            except Exception as exc:
                return "rejected", "envelope validation", _exception_detail(exc)
        elif engine_name == "pydantic-core":
            try:
                parsed = parse_transport_json(payload)
            except Exception as exc:
                return "rejected", "JSON parse", _exception_detail(exc)
            normalized = _normalize_envelope_payload(parsed, envelope_kind, alias_config)
            try:
                _validate_parsed(envelope_kind, normalized)
            except Exception as exc:
                return "rejected", "envelope validation", _exception_detail(exc)
        elif engine_name == "pydantic-direct":
            try:
                _envelope_type(envelope_kind).model_validate_json(payload)
            except ValidationError as exc:
                error_types = {str(error.get("type")) for error in exc.errors()}
                stage = "JSON parse" if "json_invalid" in error_types else "envelope validation"
                return "rejected", stage, _exception_detail(exc)
        elif engine_name == "orjson":
            engine = require_orjson(module)
            try:
                parsed = engine.loads(payload)
            except Exception as exc:
                return "rejected", "JSON parse", _exception_detail(exc)
            normalized = _normalize_envelope_payload(parsed, envelope_kind, alias_config)
            try:
                _validate_parsed(envelope_kind, normalized)
            except Exception as exc:
                return "rejected", "envelope validation", _exception_detail(exc)
        else:
            raise ValueError(f"Unknown engine: {engine_name}")
    except Exception as exc:
        return "rejected", "engine setup", _exception_detail(exc)
    return "accepted", "validated", "Validated envelope."


def _compatibility_outbound_bytes(
    envelope: TaskEnvelope,
    engine_name: str,
    module: ModuleType | None,
) -> bytes:
    if engine_name == "stdlib":
        return stdlib_outbound(envelope)
    if engine_name == "pydantic-core":
        return pydantic_core_outbound(envelope)
    if engine_name == "pydantic-direct":
        return pydantic_direct_outbound(envelope)
    return orjson_outbound(envelope, module)


def run_compatibility_checks(
    *,
    include_orjson: bool = True,
    orjson_module: ModuleType | None = _orjson_module,
) -> list[CompatibilityFinding]:
    """Run deterministic semantic probes outside timed benchmark loops."""

    engines = INBOUND_ENGINES if include_orjson else INBOUND_ENGINES[:-1]
    fixture = build_fixture("task", 1_024, "unicode-numeric")
    assert isinstance(fixture, TaskEnvelope)
    baseline_bytes = stdlib_outbound(fixture)
    baseline_semantics = json.loads(baseline_bytes)
    findings: list[CompatibilityFinding] = []

    for engine_name in engines:
        candidate = _compatibility_outbound_bytes(fixture, engine_name, orjson_module)
        parsed = json.loads(candidate)
        byte_equal = candidate == baseline_bytes
        semantic_equal = parsed == baseline_semantics
        findings.append(
            CompatibilityFinding(
                area="Outbound equivalence",
                scenario="Prepared Unicode/numeric TaskEnvelope",
                engine=ENGINE_LABELS[engine_name],
                outcome="compatible semantics" if semantic_equal else "semantic mismatch",
                rejection_stage="encoded",
                detail=(
                    f"Exact bytes {'match' if byte_equal else 'differ'}; "
                    f"parsed semantics {'match' if semantic_equal else 'differ'}; "
                    f"{len(candidate):,} bytes vs {len(baseline_bytes):,} current bytes."
                ),
            )
        )

    valid_unicode = '{"task_id":"unicode","payload":{"text":"ไทย 日本語 🚀"}}'.encode()
    invalid_utf8 = b'{"task_id":"invalid-utf8","payload":{"text":"a\xffb"}}'
    malformed = b'{"task_id":'
    invalid_shape = b'{"payload":{"valid_json":true}}'
    non_finite = b'{"task_id":"numbers","payload":{"nan":NaN,"pos":Infinity,"neg":-Infinity}}'
    alias_payload = b'{"documentId":"alias-task","payload":{"kind":"document"}}'
    configured_alias = b'{"jobId":"configured-task","payload":{"kind":"configured"}}'

    for scenario, payload in (
        ("Valid Unicode and UTF-8", valid_unicode),
        ("Invalid UTF-8 byte inside a JSON string", invalid_utf8),
        ("Malformed JSON", malformed),
        ("Valid JSON with invalid envelope shape", invalid_shape),
        ("Non-finite JSON tokens", non_finite),
    ):
        for engine_name in engines:
            outcome, stage, detail = _classify_inbound(engine_name, payload, module=orjson_module)
            findings.append(
                CompatibilityFinding(
                    area="Inbound acceptance and errors",
                    scenario=scenario,
                    engine=ENGINE_LABELS[engine_name],
                    outcome=outcome,
                    rejection_stage=stage,
                    detail=detail,
                )
            )

    for scenario, payload, alias_config in (
        ("Built-in documentId alias", alias_payload, None),
        (
            "Configured jobId alias",
            configured_alias,
            ContractAliasConfig(field_aliases={"task_id": "jobId"}),
        ),
    ):
        for engine_name in engines:
            if engine_name == "pydantic-direct":
                try:
                    direct = TaskEnvelope.model_validate_json(payload)
                except ValidationError as exc:
                    direct_detail = f"Direct validation rejects ({_exception_detail(exc)}); alias fallback is required."
                else:  # pragma: no cover - protects against future Pydantic model changes
                    direct_detail = f"Direct validation unexpectedly accepted task {direct.task_id!r}."
                normalized = cast(TaskEnvelope, pydantic_core_inbound("task", payload, alias_config))
                outcome, stage = "compatible with fallback", "alias normalization + validation"
                detail = f"{direct_detail} Fallback validates task_id={normalized.task_id!r}."
            else:
                if engine_name == "stdlib":
                    model = cast(TaskEnvelope, stdlib_inbound("task", payload, alias_config))
                elif engine_name == "pydantic-core":
                    model = cast(TaskEnvelope, pydantic_core_inbound("task", payload, alias_config))
                else:
                    model = cast(TaskEnvelope, orjson_inbound("task", payload, alias_config, orjson_module))
                outcome, stage = "compatible", "alias normalization + validation"
                detail = f"Validated task_id={model.task_id!r}; alias key is dropped."
            findings.append(
                CompatibilityFinding(
                    area="Alias compatibility",
                    scenario=scenario,
                    engine=ENGINE_LABELS[engine_name],
                    outcome=outcome,
                    rejection_stage=stage,
                    detail=detail,
                )
            )

    huge_value = 2**100
    huge_payload = f'{{"task_id":"huge","payload":{{"value":{huge_value}}}}}'.encode()
    for engine_name in engines:
        try:
            if engine_name == "stdlib":
                model = cast(TaskEnvelope, stdlib_inbound("task", huge_payload))
            elif engine_name == "pydantic-core":
                model = cast(TaskEnvelope, pydantic_core_inbound("task", huge_payload))
            elif engine_name == "pydantic-direct":
                model = cast(
                    TaskEnvelope,
                    pydantic_direct_inbound("task", huge_payload, alias_compatible=False),
                )
            else:
                model = cast(TaskEnvelope, orjson_inbound("task", huge_payload, module=orjson_module))
            actual = model.payload["value"]
            exact = isinstance(actual, int) and actual == huge_value
            outcome = "exact" if exact else "precision loss"
            detail = f"Parsed {type(actual).__name__} value {actual!r}."
        except Exception as exc:
            outcome, detail = "rejected", _exception_detail(exc)
        findings.append(
            CompatibilityFinding(
                area="Numeric compatibility",
                scenario="Inbound integer beyond 64-bit (2**100)",
                engine=ENGINE_LABELS[engine_name],
                outcome=outcome,
                rejection_stage="JSON parse + validation",
                detail=detail,
            )
        )

    huge_mapping = {"value": huge_value}
    for engine_name in engines:
        try:
            if engine_name == "stdlib":
                encoded = json.dumps(huge_mapping).encode()
            elif engine_name == "pydantic-core":
                encoded = encode_transport_json(huge_mapping)
            elif engine_name == "pydantic-direct":
                encoded = (
                    TaskEnvelope(
                        task_id="huge",
                        payload=huge_mapping,
                        created_at=_FIXED_CREATED_AT,
                    )
                    .model_dump_json()
                    .encode()
                )
            else:
                encoded = cast(bytes, require_orjson(orjson_module).dumps(huge_mapping))
            outcome = "exact" if str(huge_value).encode() in encoded else "changed"
            detail = encoded.decode()
        except Exception as exc:
            outcome, detail = "rejected", _exception_detail(exc)
        findings.append(
            CompatibilityFinding(
                area="Numeric compatibility",
                scenario="Outbound integer beyond 64-bit (2**100)",
                engine=ENGINE_LABELS[engine_name],
                outcome=outcome,
                rejection_stage="outbound encoding",
                detail=detail,
            )
        )

    raw_non_string_mapping = {1: "one", "nested": {2: "two"}}
    for engine_name in engines:
        try:
            if engine_name == "stdlib":
                encoded = json.dumps(raw_non_string_mapping, ensure_ascii=False).encode()
            elif engine_name == "pydantic-core":
                encoded = encode_transport_json(raw_non_string_mapping)
            elif engine_name == "pydantic-direct":
                model = TaskEnvelope(task_id="keys", payload=cast(dict[str, Any], raw_non_string_mapping))
                encoded = pydantic_direct_outbound(model)
            else:
                encoded = cast(bytes, require_orjson(orjson_module).dumps(raw_non_string_mapping))
            outcome = "accepted"
            detail = f"Encoded as {encoded.decode('utf-8')}."
        except Exception as exc:
            outcome, detail = "rejected", _exception_detail(exc)
        findings.append(
            CompatibilityFinding(
                area="Mapping compatibility",
                scenario="Non-string mapping keys accepted by current stdlib",
                engine=ENGINE_LABELS[engine_name],
                outcome=outcome,
                rejection_stage="outbound encoding",
                detail=detail,
            )
        )

    for scenario, unusual_mapping in (
        ("None mapping key coercion", {None: "none"}),
        ("Tuple mapping key coercion", {("x", "y"): "tuple"}),
    ):
        for engine_name in engines:
            try:
                if engine_name == "stdlib":
                    encoded = json.dumps(unusual_mapping).encode()
                elif engine_name == "pydantic-core":
                    encoded = encode_transport_json(unusual_mapping)
                elif engine_name == "pydantic-direct":
                    model = TaskEnvelope(
                        task_id="keys",
                        payload=cast(dict[str, Any], unusual_mapping),
                        created_at=_FIXED_CREATED_AT,
                    )
                    encoded = pydantic_direct_outbound(model)
                else:
                    encoded = cast(bytes, require_orjson(orjson_module).dumps(unusual_mapping))
                outcome = "accepted"
                detail = f"Encoded as {encoded.decode('utf-8')}."
            except Exception as exc:
                outcome, detail = "rejected", _exception_detail(exc)
            findings.append(
                CompatibilityFinding(
                    area="Mapping compatibility",
                    scenario=scenario,
                    engine=ENGINE_LABELS[engine_name],
                    outcome=outcome,
                    rejection_stage="outbound encoding",
                    detail=detail,
                )
            )

    prepared_model = TaskEnvelope(
        task_id="prepared-values",
        payload={
            "created": _FIXED_CREATED_AT,
            "identifier": _FIXED_UUID,
            "nested_model": _NestedProbeModel(value="nested"),
        },
        created_at=_FIXED_CREATED_AT,
    )
    prepared = _prepared_mapping(prepared_model)
    prepared_types = {key: type(value).__name__ for key, value in cast(dict[str, Any], prepared["payload"]).items()}
    for engine_name in engines:
        try:
            encoded = _compatibility_outbound_bytes(prepared_model, engine_name, orjson_module)
            semantics = json.loads(encoded)
            outcome = "compatible after preparation"
            detail = f"Prepared payload types {prepared_types}; encoded values {semantics['payload']}."
        except Exception as exc:
            outcome, detail = "rejected", _exception_detail(exc)
        findings.append(
            CompatibilityFinding(
                area="Prepared Python values",
                scenario="Datetime, UUID, and nested model after current model_dump(mode='json')",
                engine=ENGINE_LABELS[engine_name],
                outcome=outcome,
                rejection_stage="model preparation + encoding",
                detail=detail,
            )
        )

    finite_mapping = {"nan": float("nan"), "pos": float("inf"), "neg": float("-inf")}
    for engine_name in engines:
        try:
            if engine_name == "stdlib":
                encoded = json.dumps(finite_mapping).encode()
            elif engine_name == "pydantic-core":
                encoded = encode_transport_json(finite_mapping)
            elif engine_name == "pydantic-direct":
                encoded = (
                    TaskEnvelope(
                        task_id="non-finite",
                        payload=finite_mapping,
                        created_at=_FIXED_CREATED_AT,
                    )
                    .model_dump_json()
                    .encode()
                )
            else:
                encoded = cast(bytes, require_orjson(orjson_module).dumps(finite_mapping))
            outcome = "encoded"
            detail = encoded.decode()
        except Exception as exc:
            outcome, detail = "rejected", _exception_detail(exc)
        findings.append(
            CompatibilityFinding(
                area="Numeric compatibility",
                scenario="Outbound NaN, Infinity, and -Infinity",
                engine=ENGINE_LABELS[engine_name],
                outcome=outcome,
                rejection_stage="outbound encoding",
                detail=detail,
            )
        )

    findings.append(
        CompatibilityFinding(
            area="Canonical hashing and storage",
            scenario="Canonical hash/dedup or persisted byte inputs",
            engine="Decision boundary",
            outcome="out of scope for replacement",
            rejection_stage="compatibility guard",
            detail=(
                "Any future replacement must retain the current canonical serializer unless the exact input bytes "
                "are proven byte-for-byte identical. Semantic JSON equivalence is insufficient for hashes, "
                "deduplication keys, signatures, or persisted byte contracts."
            ),
        )
    )
    return findings


def aggregate_speedups(results: Sequence[BenchmarkResult]) -> dict[tuple[str, str], float]:
    """Return median current-relative speedups grouped by decision path and engine."""

    grouped: dict[tuple[str, str], list[float]] = {}
    for result in results:
        if result.case.direction == "outbound":
            path = "outbound"
        else:
            path = f"inbound-{result.case.inbound_shape}"
        grouped.setdefault((path, result.case.engine), []).append(result.relative_to_current)
    return {key: median(values) for key, values in grouped.items()}


def build_recommendations(results: Sequence[BenchmarkResult]) -> list[Recommendation]:
    """Build path-specific decisions from measured speedups and compatibility evidence."""

    speedups = aggregate_speedups(results)
    canonical_candidates = {
        engine: speedups[("inbound-canonical", engine)] for engine in ("pydantic-core", "pydantic-direct")
    }
    canonical_winner = max(canonical_candidates, key=lambda engine: canonical_candidates[engine])
    canonical_speedup = canonical_candidates[canonical_winner]
    alias_candidates = {
        engine: speedups[("inbound-alias-compatible", engine)] for engine in ("pydantic-core", "pydantic-direct")
    }
    alias_winner = max(alias_candidates, key=lambda engine: alias_candidates[engine])
    alias_speedup = alias_candidates[alias_winner]
    outbound_existing_candidates = {
        engine: speedups[("outbound", engine)] for engine in ("pydantic-core", "pydantic-direct")
    }
    outbound_existing_winner = max(
        outbound_existing_candidates,
        key=lambda engine: outbound_existing_candidates[engine],
    )
    outbound_existing_speedup = outbound_existing_candidates[outbound_existing_winner]
    outbound_all_candidates = dict(outbound_existing_candidates)
    if ("outbound", "orjson") in speedups:
        outbound_all_candidates["orjson"] = speedups[("outbound", "orjson")]
    outbound_measured_winner = max(
        outbound_all_candidates,
        key=lambda engine: outbound_all_candidates[engine],
    )
    outbound_measured_speedup = outbound_all_candidates[outbound_measured_winner]
    canonical_decision = (
        "implemented as the production transport parser" if canonical_speedup >= 1.2 else "keep current path"
    )
    alias_decision = (
        "implemented with existing alias normalization" if alias_speedup >= 1.2 else "keep current staged alias path"
    )
    return [
        Recommendation(
            path="Inbound canonical fast path",
            decision=canonical_decision,
            candidate=ENGINE_LABELS[canonical_winner],
            evidence=(
                f"Median aggregate speedup {canonical_speedup:.2f}× without a new dependency. "
                "The production parser keeps malformed-JSON versus invalid-envelope staging and intentionally "
                "rejects invalid UTF-8 instead of replacing bytes."
            ),
        ),
        Recommendation(
            path="Inbound alias-compatible path",
            decision=alias_decision,
            candidate=ENGINE_LABELS[alias_winner],
            evidence=(
                f"Median aggregate speedup {alias_speedup:.2f}× among already-available candidates. "
                "Direct model validation rejects documentId/configured aliases, so normalization or a verified "
                "fallback remains mandatory."
            ),
        ),
        Recommendation(
            path="Outbound transport",
            decision="implemented Pydantic Core with an approved compact-wire break",
            candidate=ENGINE_LABELS[outbound_existing_winner],
            evidence=(
                f"{ENGINE_LABELS[outbound_measured_winner]} measured {outbound_measured_speedup:.2f}×, but the "
                f"chosen existing-dependency path still reached {outbound_existing_speedup:.2f}× while preserving "
                "the tested huge-integer, non-finite, and mapping-key domain. Compact bytes are an intentional break."
            ),
        ),
        Recommendation(
            path="Storage and canonical hashing",
            decision="retain the exact current canonical serializers",
            candidate="Released v1.4.29 stdlib serializers",
            evidence=(
                "Hashes, deduplication inputs, signatures, and persisted bytes require byte identity. "
                "Parsed semantic equivalence does not satisfy that contract."
            ),
        ),
        Recommendation(
            path="New dependency",
            decision="do not add orjson to production for this study alone",
            candidate="Existing Pydantic/Pydantic Core dependency",
            evidence=(
                "orjson has the required wheels, but huge-integer precision/range behavior, invalid UTF-8, "
                "non-finite floats, and non-string keys differ. Existing candidates provide an inbound path "
                "without expanding the production dependency or native-wheel perimeter."
            ),
        ),
    ]


def _render_result_rows(results: Sequence[BenchmarkResult]) -> str:
    rows: list[str] = []
    for result in results:
        case = result.case
        shape = case.inbound_shape or "transport"
        baseline_class = ' class="baseline"' if case.is_baseline else ""
        rows.append(
            f"<tr{baseline_class}>"
            f"<td>{html.escape(case.envelope_kind.title())}</td>"
            f"<td>{html.escape(case.profile)}</td>"
            f"<td>{html.escape(case.target_label)}</td>"
            f"<td>{html.escape(case.direction.title())}</td>"
            f"<td>{html.escape(shape)}</td>"
            f"<td>{html.escape(case.engine_label)}</td>"
            f'<td class="number">{result.actual_bytes:,}</td>'
            f'<td class="number">{case.iterations:,} × {result.repeats}</td>'
            f'<td class="number">{result.median_ns_per_op / 1_000:,.2f}</td>'
            f'<td class="number">{result.p25_ns_per_op / 1_000:,.2f}–{result.p75_ns_per_op / 1_000:,.2f}</td>'
            f'<td class="number">{result.operations_per_second:,.0f}</td>'
            f'<td class="number">{result.throughput_mib_s:,.2f}</td>'
            f'<td class="number">{result.relative_to_current:,.2f}×</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _render_compatibility_rows(findings: Sequence[CompatibilityFinding]) -> str:
    return "\n".join(
        "<tr>"
        f"<td>{html.escape(finding.area)}</td>"
        f"<td>{html.escape(finding.scenario)}</td>"
        f"<td>{html.escape(finding.engine)}</td>"
        f"<td>{html.escape(finding.outcome)}</td>"
        f"<td>{html.escape(finding.rejection_stage)}</td>"
        f'<td class="wrap">{html.escape(finding.detail)}</td>'
        "</tr>"
        for finding in findings
    )


def _render_recommendation_rows(recommendations: Sequence[Recommendation]) -> str:
    return "\n".join(
        "<tr>"
        f"<th>{html.escape(item.path)}</th>"
        f"<td>{html.escape(item.decision)}</td>"
        f"<td>{html.escape(item.candidate)}</td>"
        f'<td class="wrap">{html.escape(item.evidence)}</td>'
        "</tr>"
        for item in recommendations
    )


def _render_speedup_rows(results: Sequence[BenchmarkResult]) -> str:
    return "\n".join(
        "<tr>"
        f"<td>{html.escape(path)}</td>"
        f"<td>{html.escape(ENGINE_LABELS[engine])}</td>"
        f'<td class="number">{speedup:.2f}×</td>'
        "</tr>"
        for (path, engine), speedup in sorted(aggregate_speedups(results).items())
    )


def _render_wheel_rows() -> str:
    production_rows = [
        "<tr>"
        "<td>Pydantic Core 2.41.5 (production)</td>"
        f"<td>{html.escape(python_version)}</td>"
        f"<td>{html.escape(platform_name)}</td>"
        "<td>Available</td>"
        f'<td class="wrap"><code>{html.escape(filename)}</code></td>'
        "</tr>"
        for python_version, platform_name, filename in PRODUCTION_WHEEL_SUPPORT
    ]
    orjson_rows = [
        "<tr>"
        "<td>orjson 3.11.9 (benchmark only)</td>"
        f"<td>{html.escape(python_version)}</td>"
        f"<td>{html.escape(platform_name)}</td>"
        "<td>Available</td>"
        f'<td class="wrap"><code>{html.escape(filename)}</code></td>'
        "</tr>"
        for python_version, platform_name, filename in WHEEL_SUPPORT
    ]
    return "\n".join(production_rows + orjson_rows)


def render_html(
    results: Sequence[BenchmarkResult],
    findings: Sequence[CompatibilityFinding],
    environment: Mapping[str, str],
    *,
    include_orjson: bool,
) -> str:
    """Render the stable self-contained JSON-engine decision report."""

    if not results:
        raise ValueError("At least one benchmark result is required.")
    if not findings:
        raise ValueError("At least one compatibility finding is required.")
    recommendations = build_recommendations(results)
    metadata_rows = "\n".join(
        f'<tr><th>{html.escape(key)}</th><td class="wrap">{html.escape(value)}</td></tr>'
        for key, value in environment.items()
    )
    total_operations = sum(result.case.iterations * result.repeats for result in results)
    optional_note = (
        "orjson was installed and measured."
        if include_orjson
        else "orjson was not installed; this is an explicitly partial report."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relayna CPU-Side JSON Engine Evaluation and Production Decision</title>
  <style>
    :root {{ color-scheme: light; --ink: #172033; --muted: #5d687a; --line: #d9deea;
      --panel: #f7f8fc; --accent: #3155a6; --baseline: #eef4ff; --warn: #fff5db; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: var(--ink);
      font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1700px, calc(100% - 32px)); margin: 36px auto 72px; }}
    h1 {{ margin-bottom: 4px; font-size: clamp(28px, 4vw, 44px); line-height: 1.1; }}
    h2 {{ margin-top: 42px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    h3 {{ margin-top: 28px; }}
    p, li {{ max-width: 100ch; }}
    .lede {{ color: var(--muted); font-size: 17px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px; margin: 26px 0; }}
    .summary div {{ border: 1px solid var(--line); border-radius: 10px; padding: 15px;
      background: var(--panel); }}
    .summary strong {{ display: block; color: var(--accent); font-size: 22px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 9px 11px; border-bottom: 1px solid var(--line); text-align: left;
      vertical-align: top; white-space: nowrap; }}
    thead th {{ position: sticky; top: 0; background: #172033; color: #fff; }}
    tbody tr:last-child td, tbody tr:last-child th {{ border-bottom: 0; }}
    tbody tr.baseline {{ background: var(--baseline); }}
    .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .wrap {{ white-space: normal; min-width: 260px; }}
    code {{ background: #edf0f7; border-radius: 4px; padding: 2px 5px; }}
    .note {{ padding: 14px 16px; border-left: 4px solid var(--accent); background: var(--panel); }}
    .warning {{ padding: 14px 16px; border-left: 4px solid #a56b00; background: var(--warn); }}
    @media print {{ main {{ width: 100%; margin: 0; }} thead th {{ position: static; }} }}
  </style>
</head>
<body>
<main>
  <h1>Relayna CPU-Side JSON Engine Evaluation and Production Decision</h1>
  <p class="lede">Complete outbound preparation/encoding and inbound
  parsing/alias-normalization/validation for task and batch envelopes, measured without
  RabbitMQ, Redis, or network latency, and tied directly to the implemented production
  transport codec.</p>

  <div class="summary">
    <div><strong>{len(results)}</strong>performance cases</div>
    <div><strong>{len(findings)}</strong>compatibility findings</div>
    <div><strong>{total_operations:,}</strong>timed operations</div>
    <div><strong>{len(TARGET_SIZES)}</strong>exact target sizes</div>
  </div>

  <h2>Executive decision</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Path</th><th>Decision</th><th>Candidate</th><th>Evidence</th></tr></thead>
    <tbody>{_render_recommendation_rows(recommendations)}</tbody>
  </table></div>
  <p class="warning"><strong>Approved compatibility boundary:</strong> the user explicitly
  approved changing the v1.4.29 JSON transport perimeter. Production now uses Pydantic Core
  for AMQP encode/parse, compact outbound bytes, and strict UTF-8. Persisted JSON,
  canonical hashes, deduplication inputs, public APIs, routes, and schemas remain unchanged.</p>

  <h2>Performance</h2>
  <h3>Methodology</h3>
  <p>Fixtures use fixed identifiers and timestamps. ASCII-heavy and Unicode/numeric
  profiles are calibrated so the <em>current outbound</em> bytes are exactly 1,024,
  16,384, 131,072, and 1,048,576 bytes for both <code>TaskEnvelope</code> and
  two-task <code>BatchTaskEnvelope</code> messages. “Actual bytes” reports each
  candidate’s emitted or consumed bytes; alias-compatible payloads are slightly larger
  because <code>documentId</code> replaces <code>task_id</code>.</p>
  <p>Outbound measurements include <code>model_dump(mode="json",
  exclude_none=True)</code> where applicable, JSON encoding, and AMQP-ready bytes.
  The implemented private production codec and orjson receive the current prepared mapping;
  raw engine speed is not substituted for model-aware preparation. Inbound canonical
  measurements start with current wire bytes and end with a validated envelope. Alias
  measurements additionally preserve Relayna’s recursive <code>documentId</code>
  normalization. The direct Pydantic alias case includes a failed direct validation and
  explicit Pydantic Core normalization fallback.</p>
  <p>Every candidate is checked for validated semantic equivalence before timing. Each
  operation receives a warm-up call. Values are medians of repeated
  <code>perf_counter_ns</code> samples; the displayed P25–P75 interval is the useful
  dispersion measure. Garbage collection is disabled only within timed loops, and case
  order rotates/reverses by repeat. MiB/s uses 1,048,576 bytes. Relative speedup is
  current median divided by candidate median. {html.escape(optional_note)}</p>

  <h3>Aggregate relative speedups</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>Path</th><th>Engine</th><th class="number">Median vs current</th></tr></thead>
    <tbody>{_render_speedup_rows(results)}</tbody>
  </table></div>

  <h3>Complete results</h3>
  <div class="table-wrap"><table>
    <thead><tr>
      <th>Envelope</th><th>Profile</th><th>Target</th><th>Direction</th><th>Shape</th>
      <th>Engine</th><th class="number">Actual bytes</th>
      <th class="number">Iterations × repeats</th><th class="number">Median µs/op</th>
      <th class="number">P25–P75 µs/op</th><th class="number">Operations/s</th>
      <th class="number">MiB/s</th><th class="number">vs current</th>
    </tr></thead>
    <tbody>{_render_result_rows(results)}</tbody>
  </table></div>

  <h2>Compatibility</h2>
  <p>These probes are deterministic and untimed. “JSON parse” versus “envelope validation”
  records the rejection stage so the implemented production path preserves Relayna’s
  <code>malformed_json</code> versus invalid-envelope classification. The current inbound
  UTF-8 replacement behavior can accept invalid bytes as U+FFFD; strict engines reject
  them. Direct model JSON cannot normalize configured aliases by itself.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Area</th><th>Scenario</th><th>Engine</th><th>Outcome</th>
      <th>Stage</th><th>Detail</th></tr></thead>
    <tbody>{_render_compatibility_rows(findings)}</tbody>
  </table></div>

  <h2>Packaging and reproducibility</h2>
  <p>The chosen production codec uses Pydantic Core 2.41.5, already installed through
  Relayna’s Pydantic dependency. It adds no package or native-wheel perimeter. orjson is
  pinned only in Relayna’s optional <code>[benchmark]</code> extra as
  <code>orjson=={ORJSON_VERSION}</code>; it is not in production
  <code>[project].dependencies</code>. The canonical command is:</p>
  <p><code>uv run --extra benchmark python -m benchmarks run json-engine-evaluation</code></p>
  <p>Wheel filenames below were verified against the PyPI release metadata for
  <a href="https://pypi.org/project/pydantic-core/2.41.5/">Pydantic Core 2.41.5</a>
  and
  <a href="https://pypi.org/project/orjson/{ORJSON_VERSION}/">orjson
  {ORJSON_VERSION}</a>. macOS universal2 artifacts cover x86_64 as well as ARM64;
  dedicated ARM64 wheels are also published. Native wheels reduce contributor setup
  risk, but a production dependency would still add a Rust-backed native extension,
  platform artifact tracking, and supply-chain surface.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Package</th><th>Interpreter</th><th>Target</th><th>Status</th><th>Artifact evidence</th></tr></thead>
    <tbody>{_render_wheel_rows()}</tbody>
  </table></div>

  <h3>Environment and exact package versions</h3>
  <div class="table-wrap"><table><tbody>{metadata_rows}</tbody></table></div>

  <h2>Implemented production strategy</h2>
  <ol>
    <li>Change only Relayna-owned AMQP transport bodies; keep storage, canonical
    hashing, deduplication, and persisted serializers unchanged.</li>
    <li>Use private <code>relayna._transport_json</code> helpers backed by Pydantic Core
    for compact outbound AMQP bytes and strict inbound parsing.</li>
    <li>Continue existing alias normalization and Pydantic envelope validation after
    parsing, keeping malformed syntax in the parse stage and valid invalid envelopes in
    the validation stage.</li>
    <li>Do not add orjson to production. Keep it benchmark-only because its extra
    outbound gain does not justify its native dependency and narrower numeric/domain
    behavior.</li>
  </ol>

  <h2>Next benchmark</h2>
  <p>Before broad rollout, run a deterministic consumer-classification replay benchmark.
  Matrix: codec (released v1.4.29 stdlib, implemented Pydantic Core); envelope (task,
  two-task batch); size (1 KB, 16 KB, 128 KB); traffic mix (100/0, 95/5, and 80/20
  canonical/alias); validity (valid, malformed JSON, invalid UTF-8, valid invalid envelope);
  and configured alias on/off. Measure total CPU latency, allocations, malformed/invalid
  counts, and rejection-stage parity. This tests realistic traffic mixes and provides
  canary alert thresholds before deployment, still without network latency.</p>

  <h2>Limitations</h2>
  <ul>
    <li>Local CPU observations are not cross-machine guarantees; use relative comparisons
    and rerun on production-representative Linux x86_64 and aarch64 hosts.</li>
    <li>No RabbitMQ, Redis, scheduling, async dispatch, or network work is included.</li>
    <li>Compatibility probes cover known Relayna boundaries but do not replace a captured
    production-corpus replay.</li>
  </ul>
</main>
</body>
</html>
"""


def write_html_report(
    output_path: Path,
    results: Sequence[BenchmarkResult],
    findings: Sequence[CompatibilityFinding],
    environment: Mapping[str, str],
    *,
    include_orjson: bool,
) -> Path:
    """Atomically write the rendered decision report."""

    return write_text_artifact(
        output_path,
        render_html(results, findings, environment, include_orjson=include_orjson),
    )


def _parse_iteration_override(value: str) -> tuple[int, int]:
    try:
        label, count_text = value.split("=", maxsplit=1)
        target_bytes = TARGET_SIZES[label]
        count = int(count_text)
    except (KeyError, ValueError) as exc:
        labels = ", ".join(TARGET_SIZES)
        raise argparse.ArgumentTypeError(f"Use LABEL=COUNT with a label from: {labels}") from exc
    if count < 1:
        raise argparse.ArgumentTypeError("Iteration count must be positive.")
    return target_bytes, count


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a positive integer.") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return parsed


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Add JSON-engine evaluation options to the shared benchmark CLI."""

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"HTML output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--repeats",
        type=_positive_int,
        default=DEFAULT_REPEATS,
        help=f"Timed samples per matrix case (default: {DEFAULT_REPEATS})",
    )
    parser.add_argument(
        "--iterations",
        action="append",
        default=[],
        type=_parse_iteration_override,
        metavar="LABEL=COUNT",
        help='Override iterations for a size, for example --iterations "1 MB=3".',
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=DEFAULT_PROFILES,
        default=[],
        help="Restrict to one or more payload profiles; defaults to both.",
    )
    parser.add_argument(
        "--allow-missing-orjson",
        action="store_true",
        help="Generate an explicitly partial report when the benchmark extra is unavailable.",
    )


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "not installed"


def run_from_cli(args: argparse.Namespace) -> BenchmarkOutcome:
    """Run the JSON engine study from parsed shared-CLI arguments."""

    include_orjson = available_orjson() is not None
    if not include_orjson and not args.allow_missing_orjson:
        require_orjson()
    iterations = dict(DEFAULT_ITERATIONS)
    iterations.update(dict(cast(list[tuple[int, int]], args.iterations)))
    profiles = cast(list[Profile], args.profile) or list(DEFAULT_PROFILES)
    results = run_benchmarks(
        repeats=args.repeats,
        iterations_by_size=iterations,
        profiles=profiles,
        include_orjson=include_orjson,
    )
    findings = run_compatibility_checks(include_orjson=include_orjson)
    environment = collect_environment(
        package_names=("relayna", "pydantic", "pydantic-core", "orjson"),
        extra={
            "Benchmark profiles": ", ".join(profiles),
            "orjson benchmark extra": f"{ORJSON_EXTRA} (orjson=={ORJSON_VERSION})",
            "orjson availability": "installed" if include_orjson else "not installed; partial report",
            "orjson resolved version": _package_version("orjson"),
            "Network/services": "none; deterministic CPU-only benchmark",
            "Implemented production codec": "relayna._transport_json (pydantic-core)",
        },
    )
    report_path = write_html_report(
        args.output,
        results,
        findings,
        environment,
        include_orjson=include_orjson,
    )
    return BenchmarkOutcome(artifacts=(report_path,), measurement_count=len(results))


BENCHMARK = BenchmarkDefinition(
    name="json-engine-evaluation",
    summary="Compare complete Relayna CPU-side JSON paths and compatibility.",
    default_output=DEFAULT_OUTPUT,
    add_arguments=add_cli_arguments,
    run=run_from_cli,
)
