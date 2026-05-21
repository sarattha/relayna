from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PressureSeverity(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class RuntimePressureSignal(BaseModel):
    scope: str
    kind: str
    severity: PressureSeverity
    observed_at: datetime = Field(default_factory=_utcnow)
    scope_id: str | None = None
    value: float | None = None
    threshold: float | None = None
    reason: str | None = None
    recommended_action: str | None = None


class RuntimePressureSnapshot(BaseModel):
    reported_at: datetime = Field(default_factory=_utcnow)
    signals: list[RuntimePressureSignal] = Field(default_factory=list)


class PressureSignalCollector(Protocol):
    async def collect(self) -> Iterable[RuntimePressureSignal]: ...


def severity_for_thresholds(
    value: float | int | None,
    *,
    warning_threshold: float | int | None = None,
    critical_threshold: float | int | None = None,
) -> PressureSeverity:
    if value is None:
        return PressureSeverity.UNKNOWN
    normalized = float(value)
    if critical_threshold is not None and normalized >= float(critical_threshold):
        return PressureSeverity.CRITICAL
    if warning_threshold is not None and normalized >= float(warning_threshold):
        return PressureSeverity.WARNING
    return PressureSeverity.NORMAL


class RuntimePressureService:
    def __init__(
        self,
        *,
        collectors: Sequence[PressureSignalCollector] = (),
        metrics_recorder: Callable[[RuntimePressureSignal], None] | None = None,
    ) -> None:
        self._collectors = tuple(collectors)
        self._metrics_recorder = metrics_recorder

    async def snapshot(self) -> RuntimePressureSnapshot:
        signals: list[RuntimePressureSignal] = []
        for collector in self._collectors:
            try:
                signals.extend(await collector.collect())
            except Exception as exc:
                signals.append(
                    RuntimePressureSignal(
                        scope="collector",
                        scope_id=type(collector).__name__,
                        kind="pressure_collector_unavailable",
                        severity=PressureSeverity.UNKNOWN,
                        reason=str(exc),
                        recommended_action="Inspect the pressure signal collector configuration.",
                    )
                )
        snapshot = RuntimePressureSnapshot(signals=signals)
        if self._metrics_recorder is not None:
            for signal in signals:
                self._metrics_recorder(signal)
        return snapshot


class QueuePressureCollector:
    def __init__(
        self,
        *,
        queue_names: Iterable[str],
        inspect_queue: Callable[[str], Awaitable[Any | None]],
        warning_depth: int = 100,
        critical_depth: int = 1000,
    ) -> None:
        self._queue_names = tuple(dict.fromkeys(str(name).strip() for name in queue_names if str(name).strip()))
        self._inspect_queue = inspect_queue
        self._warning_depth = warning_depth
        self._critical_depth = critical_depth

    async def collect(self) -> Iterable[RuntimePressureSignal]:
        signals: list[RuntimePressureSignal] = []
        for queue_name in self._queue_names:
            inspection = await self._inspect_queue(queue_name)
            if inspection is None:
                signals.append(
                    RuntimePressureSignal(
                        scope="queue",
                        scope_id=queue_name,
                        kind="queue_inspection_unavailable",
                        severity=PressureSeverity.UNKNOWN,
                        reason="Queue inspection returned no data.",
                        recommended_action="Verify RabbitMQ management inspection is configured.",
                    )
                )
                continue
            severity = severity_for_thresholds(
                inspection.message_count,
                warning_threshold=self._warning_depth,
                critical_threshold=self._critical_depth,
            )
            signals.append(
                RuntimePressureSignal(
                    scope="queue",
                    scope_id=queue_name,
                    kind="queue_depth_high",
                    severity=severity,
                    value=float(inspection.message_count),
                    threshold=float(
                        self._critical_depth if severity == PressureSeverity.CRITICAL else self._warning_depth
                    ),
                    reason=f"Queue has {inspection.message_count} ready messages.",
                    recommended_action="Add consumers or slow publishers."
                    if severity != PressureSeverity.NORMAL
                    else None,
                )
            )
            if inspection.consumer_count == 0:
                signals.append(
                    RuntimePressureSignal(
                        scope="queue",
                        scope_id=queue_name,
                        kind="consumer_count_zero",
                        severity=PressureSeverity.CRITICAL,
                        value=0,
                        threshold=1,
                        reason="Queue has no active consumers.",
                        recommended_action="Start at least one worker for this queue.",
                    )
                )
        return signals


class WorkerHealthPressureCollector:
    def __init__(
        self,
        *,
        workers_provider: Callable[[], Iterable[Any]],
        stale_after_seconds: int = 90,
    ) -> None:
        self._workers_provider = workers_provider
        self._stale_after_seconds = stale_after_seconds

    async def collect(self) -> Iterable[RuntimePressureSignal]:
        now = _utcnow()
        signals: list[RuntimePressureSignal] = []
        workers = list(self._workers_provider())
        if not workers:
            return [
                RuntimePressureSignal(
                    scope="workers",
                    kind="worker_count_zero",
                    severity=PressureSeverity.UNKNOWN,
                    value=0,
                    threshold=1,
                    reason="No worker heartbeat records are available.",
                    recommended_action="Verify worker heartbeat reporting is configured.",
                )
            ]
        for worker in workers:
            heartbeat_at = worker.last_heartbeat_at
            if heartbeat_at is not None and heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
            stale = heartbeat_at is None or now - heartbeat_at.astimezone(UTC) > timedelta(
                seconds=self._stale_after_seconds
            )
            if stale or not worker.running:
                signals.append(
                    RuntimePressureSignal(
                        scope="worker",
                        scope_id=worker.worker_name,
                        kind="worker_heartbeat_stale" if stale else "worker_not_running",
                        severity=PressureSeverity.WARNING,
                        reason="Worker heartbeat is stale." if stale else "Worker reports it is not running.",
                        recommended_action="Inspect worker process health and logs.",
                    )
                )
            expired_leases = [lease for lease in worker.active_leases if lease.expired]
            if expired_leases:
                signals.append(
                    RuntimePressureSignal(
                        scope="worker",
                        scope_id=worker.worker_name,
                        kind="lease_expiry_rate_high",
                        severity=PressureSeverity.WARNING,
                        value=float(len(expired_leases)),
                        threshold=1,
                        reason="Worker has expired active task leases.",
                        recommended_action="Inspect stuck tasks and worker ownership.",
                    )
                )
        return signals


class DLQPressureCollector:
    def __init__(
        self,
        *,
        summaries_provider: Callable[[], Awaitable[Iterable[Any]]],
        warning_count: int = 1,
        critical_count: int = 100,
    ) -> None:
        self._summaries_provider = summaries_provider
        self._warning_count = warning_count
        self._critical_count = critical_count

    async def collect(self) -> Iterable[RuntimePressureSignal]:
        signals: list[RuntimePressureSignal] = []
        for summary in await self._summaries_provider():
            value = summary.message_count if summary.message_count is not None else summary.indexed_count
            severity = severity_for_thresholds(
                value,
                warning_threshold=self._warning_count,
                critical_threshold=self._critical_count,
            )
            signals.append(
                RuntimePressureSignal(
                    scope="dlq",
                    scope_id=summary.queue_name,
                    kind="dlq_growth_high",
                    severity=severity,
                    value=float(value),
                    threshold=float(
                        self._critical_count if severity == PressureSeverity.CRITICAL else self._warning_count
                    ),
                    reason=f"DLQ queue has {value} indexed or broker-visible messages.",
                    recommended_action="Inspect DLQ causes and replay or resolve messages."
                    if severity != PressureSeverity.NORMAL
                    else None,
                )
            )
        return signals


__all__ = [
    "DLQPressureCollector",
    "PressureSeverity",
    "PressureSignalCollector",
    "QueuePressureCollector",
    "RuntimePressureService",
    "RuntimePressureSignal",
    "RuntimePressureSnapshot",
    "WorkerHealthPressureCollector",
    "severity_for_thresholds",
]
