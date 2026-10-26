"""Read-only queue/worker evidence; no service metrics are joined to queue rates."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from relayna.api import HEALTH_WORKERS_ROUTE_ID, WorkerHeartbeatListResponse
from relayna.observability import RuntimePressureSnapshot, RuntimeQueueObservation

from .federation import StudioFederationError, StudioFederationService
from .metrics import StudioMetricGroup, StudioMetricsQuery, StudioMetricsQueryService


def _freshness(value: datetime | None, now: datetime, seconds: int) -> str:
    if value is None or value.tzinfo is None:
        return "missing"
    age = (now - value).total_seconds()
    return "fresh" if -30 <= age <= seconds else "stale"


def _drain(queue: RuntimeQueueObservation, freshness: str) -> dict[str, Any]:
    result: dict[str, Any] = {"state": "unknown", "seconds": None}
    if freshness != "fresh":
        return result | {"reason": "Queue observation is not fresh."}
    if (
        queue.ready is None
        or queue.unacknowledged is None
        or queue.arrival_rate is None
        or queue.completion_rate is None
        or queue.rate_window_seconds is None
    ):
        return result | {
            "reason": "Requires ready + unacknowledged backlog and same-queue arrival/completion rates with a window."
        }
    backlog = queue.ready + queue.unacknowledged
    arrival, completion = queue.arrival_rate, queue.completion_rate
    if arrival >= completion:
        return result | {"state": "not_draining", "reason": "Arrival rate meets or exceeds completion rate."}
    seconds = backlog / (completion - arrival)
    if not math.isfinite(seconds):
        return result | {"reason": "Drain estimate exceeds the supported range."}
    return {
        "state": "draining",
        "seconds": seconds,
        "reason": "Assumes constant rates; includes ready + unacknowledged work for this queue/source.",
    }


def _queues(snapshot: RuntimePressureSnapshot, now: datetime, stale_after: int) -> list[dict[str, Any]]:
    # Preserve source identity. Never sum two providers observing the same queue.
    observations = {
        (q.queue_name, q.source): q
        for q in sorted(
            snapshot.queues, key=lambda q: q.observed_at.timestamp() if q.observed_at.tzinfo else float("-inf")
        )
    }
    represented = {q.queue_name for q in snapshot.queues}
    legacy: dict[str, RuntimeQueueObservation] = {}
    legacy_stale: set[str] = set()
    for signal in snapshot.signals:
        if signal.scope != "queue" or not signal.scope_id or signal.scope_id in represented:
            continue
        queue = legacy.setdefault(
            signal.scope_id,
            RuntimeQueueObservation(
                queue_name=signal.scope_id,
                source="runtime_pressure_signals",
                observed_at=snapshot.reported_at,
            ),
        )
        # Legacy snapshots report only ready depth and explicitly observed zero consumers.
        # Do not infer a positive consumer count from absence of an alert.
        observed_at = signal.observed_at if "observed_at" in signal.model_fields_set else snapshot.reported_at
        if signal.kind in {"queue_depth_high", "consumer_count_zero"}:
            if _freshness(observed_at, now, stale_after) != "fresh":
                legacy_stale.add(signal.scope_id)
            if observed_at.tzinfo is not None and queue.observed_at.tzinfo is not None:
                queue.observed_at = min(queue.observed_at, observed_at)
            else:
                queue.observed_at = observed_at
        if signal.value is not None and math.isfinite(signal.value) and signal.value >= 0:
            if signal.kind == "queue_depth_high" and signal.value.is_integer():
                queue.ready = int(signal.value)
            if signal.kind == "consumer_count_zero" and signal.value == 0:
                queue.consumers = 0
    observations.update({(q.queue_name, q.source): q for q in legacy.values()})
    result = []
    for queue in observations.values():
        state = _freshness(queue.observed_at, now, stale_after)
        if _freshness(snapshot.reported_at, now, stale_after) != "fresh" or queue.queue_name in legacy_stale:
            state = "stale"
        diagnosis = "Queue coverage is incomplete; inspect the available evidence."
        if state != "fresh":
            diagnosis = "Queue evidence is stale; refresh the source before diagnosing capacity."
        elif queue.consumers == 0:
            diagnosis = "No consumers observed for this queue."
        elif queue.arrival_rate is not None and queue.completion_rate is not None and queue.rate_window_seconds:
            if queue.arrival_rate >= queue.completion_rate:
                diagnosis = "Arrival rate meets or exceeds completion rate; this queue is not draining."
            else:
                diagnosis = "Completion rate exceeds arrival rate for this queue."
        elif queue.ready is not None and queue.ready > 0:
            diagnosis = "Ready work is waiting; compatible rates are needed to assess capacity."
        result.append(
            queue.model_dump(mode="json") | {"freshness": state, "diagnosis": diagnosis, "drain": _drain(queue, state)}
        )
    return result


def _workers(
    payload: WorkerHeartbeatListResponse, now: datetime, stale_after: int, *, snapshot_stale_after: int = 90
) -> list[dict[str, Any]]:
    result = []
    snapshot_state = _freshness(payload.reported_at, now, snapshot_stale_after)
    for worker in payload.workers:
        freshness = _freshness(worker.last_heartbeat_at, now, stale_after)
        if snapshot_state != "fresh":
            freshness = "stale"
        leases = []
        for lease in worker.active_leases:
            expired = lease.expired or (lease.expires_at.tzinfo is not None and lease.expires_at <= now)
            leases.append(
                lease.model_dump(mode="json")
                | {
                    "expired": expired,
                    "freshness": _freshness(lease.heartbeat_at, now, stale_after)
                    if snapshot_state == "fresh"
                    else "stale",
                    "age_seconds": max(0, (now - lease.acquired_at).total_seconds())
                    if lease.acquired_at is not None and lease.acquired_at.tzinfo is not None
                    else None,
                }
            )
        # The provider contract does not declare periodic heartbeat cadence.
        # Fresh task/lease activity is not confirmation of worker liveness.
        state = "stopped" if not worker.running else freshness
        result.append(
            worker.model_dump(mode="json")
            | {"freshness": freshness, "state": state, "liveness": "unknown", "active_leases": leases}
        )
    return result


async def _read_cockpit(
    service_id: str,
    federation: StudioFederationService,
    metrics: StudioMetricsQueryService,
    *,
    stale_after: int,
    worker_stale_after: int,
) -> dict[str, Any]:
    service = await federation._get_proxyable_service(service_id)
    now = datetime.now(UTC)
    sources: dict[str, Any] = {}
    result: dict[str, Any] = {
        "service_id": service.service_id,
        "reported_at": now.isoformat(),
        "stale_after_seconds": stale_after,
        "worker_stale_after_seconds": worker_stale_after,
        "sources": sources,
        "queues": [],
        "workers": [],
        "signals": [],
        "metrics": None,
    }

    async def pressure_read() -> None:
        try:
            raw = await federation.get_service_runtime_backpressure(service.service_id)
            # Missing report time must not be silently replaced by Pydantic's default.
            if not raw.get("reported_at"):
                raise ValueError("Runtime pressure response has no reported_at timestamp.")
            payload = RuntimePressureSnapshot.model_validate(raw)
            if any(
                s.value is not None
                and not math.isfinite(s.value)
                or s.threshold is not None
                and not math.isfinite(s.threshold)
                for s in payload.signals
            ):
                raise ValueError("Pressure signal has a non-finite reading.")
            result["queues"] = _queues(payload, now, stale_after)
            result["signals"] = [
                signal.model_dump(mode="json")
                | {
                    "observed_at": (
                        signal.observed_at if "observed_at" in signal.model_fields_set else payload.reported_at
                    ).isoformat(),
                    "freshness": _freshness(
                        signal.observed_at if "observed_at" in signal.model_fields_set else payload.reported_at,
                        now,
                        stale_after,
                    )
                    if _freshness(payload.reported_at, now, stale_after) == "fresh"
                    else "stale",
                }
                for signal in payload.signals
            ]
            state = _freshness(payload.reported_at, now, stale_after)
            partial = any(s.severity.value == "unknown" for s in payload.signals)
            partial = partial or any(q["freshness"] != "fresh" for q in result["queues"])
            sources["pressure"] = {
                "state": "partial" if state == "fresh" and partial else state,
                "reported_at": payload.reported_at.isoformat(),
                "detail": "Runtime backpressure; queue coverage is limited to configured collectors.",
            }
        except StudioFederationError as exc:
            sources["pressure"] = _source_error(exc)
        except (ValidationError, ValueError, TypeError):
            sources["pressure"] = {"state": "unavailable", "detail": "Runtime pressure returned invalid evidence."}

    async def workers_read() -> None:
        try:
            raw = await federation._request_json(
                service, capability_id=HEALTH_WORKERS_ROUTE_ID, path="/relayna/health/workers", missing_route_404=True
            )
            payload = WorkerHeartbeatListResponse.model_validate(raw)
            result["workers"] = _workers(payload, now, worker_stale_after, snapshot_stale_after=stale_after)
            state = _freshness(payload.reported_at, now, stale_after)
            sources["workers"] = {
                "state": state,
                "reported_at": payload.reported_at.isoformat(),
                "detail": "Worker health provider; leases cover reported active work only.",
            }
        except StudioFederationError as exc:
            sources["workers"] = _source_error(exc)
        except (ValidationError, ValueError, TypeError):
            sources["workers"] = {"state": "unavailable", "detail": "Worker provider returned invalid evidence."}

    async def metrics_read() -> None:
        if service.metrics_config is None:
            sources["metrics"] = {"state": "not_configured", "detail": "No Prometheus metrics source configured."}
            return
        groups = [
            StudioMetricGroup.TASKS_RETRIED_RATE,
            StudioMetricGroup.TASKS_DLQ_RATE,
            StudioMetricGroup.ACTIVE_TASKS,
            StudioMetricGroup.TASK_DURATION_P95,
        ]
        query = StudioMetricsQuery.model_validate(
            {"from": (now - timedelta(minutes=15)).isoformat(), "to": now.isoformat(), "step": 30}
        )

        async def read_group(group: StudioMetricGroup):
            return await metrics.query_service_metrics(service.service_id, query.model_copy(update={"groups": [group]}))

        batches = await asyncio.gather(*(read_group(group) for group in groups), return_exceptions=True)
        series, warnings = [], []
        successes = 0
        fresh_groups = 0
        observed_groups = 0
        for group, batch in zip(groups, batches, strict=True):
            if isinstance(batch, BaseException):
                warnings.append(f"{group.value}: source unavailable.")
            else:
                successes += 1
                points = [point for item in batch.series for point in item.points]
                observed = [
                    point
                    for point in points
                    if point.value is not None and math.isfinite(point.value) and point.value >= 0
                ]
                observed_groups += bool(observed)
                latest = max(points, key=lambda p: p.timestamp, default=None)
                try:
                    current = (
                        latest is not None
                        and latest in observed
                        and _freshness(
                            datetime.fromisoformat(latest.timestamp.replace("Z", "+00:00")), now, stale_after
                        )
                        == "fresh"
                    )
                except ValueError:
                    current = False
                fresh_groups += bool(current)
                if not current:
                    warnings.append(f"{group.value}: latest sample is missing or stale.")
                for item in batch.series:
                    row = item.model_dump(mode="json")
                    row["points"] = [
                        {
                            "timestamp": point.timestamp,
                            "value": point.value
                            if point.value is not None and math.isfinite(point.value) and point.value >= 0
                            else None,
                        }
                        for point in item.points
                    ]
                    series.append(row)
                warnings.extend(batch.warnings)
        result["metrics"] = {
            "from": query.from_time,
            "to": query.to_time,
            "series": series,
            "warnings": warnings,
            "rate_window_seconds": 300,
            "scope": "service",
        }
        sources["metrics"] = {
            "state": "unavailable"
            if not successes
            else "stale"
            if observed_groups and not fresh_groups
            else "missing"
            if not observed_groups
            else "partial"
            if warnings
            else "fresh",
            "detail": "Prometheus service scope; retry/DLQ rates use 5-minute windows across the last 15 minutes.",
        }

    await asyncio.gather(pressure_read(), workers_read(), metrics_read())
    return result


def _source_error(exc: StudioFederationError) -> dict[str, str]:
    return {"state": "unsupported" if exc.code == "unsupported_route" else "unavailable", "detail": exc.detail}


def _create_cockpit_router(
    federation: StudioFederationService,
    metrics: StudioMetricsQueryService,
    *,
    worker_stale_after: int,
) -> APIRouter:
    router = APIRouter()

    @router.get("/studio/services/{service_id}/runtime/cockpit")
    async def cockpit(service_id: str):
        try:
            return await _read_cockpit(
                service_id, federation, metrics, stale_after=90, worker_stale_after=worker_stale_after
            )
        except StudioFederationError as exc:
            return exc.to_response()

    return router
