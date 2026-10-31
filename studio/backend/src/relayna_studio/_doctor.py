"""Bounded integration evidence, separate from readiness and worker liveness."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from relayna.api import CapabilityDocument
from relayna.observability import RelaynaServiceEventFeedResponse

from ._provider_availability import _provider_key
from .database import events, outbox
from .events import StudioControlPlaneEvent, _supports_events_feed
from .logs import StudioLogQuery
from .registry import ServiceNotFoundError, ServiceStatus

if TYPE_CHECKING:
    from .app import StudioRuntime

_State = Literal["ok", "empty", "stale", "unknown", "unsupported", "unconfigured", "unavailable", "disabled"]


class _Check(BaseModel):
    source: str
    state: _State = "unknown"
    scope: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checked_at: datetime | None = None
    last_successful_at: datetime | None = None
    evidence_at: datetime | None = None
    value: float | None = None
    unit: str | None = None
    detail: str
    next_check: str


class _Report(BaseModel):
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service_id: str | None = None
    environment: str | None = None
    checks: list[_Check]


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode()
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _age(value: Any, now: datetime | None = None) -> float | None:
    stamp = _stamp(value)
    seconds = ((now or datetime.now(UTC)) - stamp).total_seconds() if stamp else None
    return seconds if seconds is not None and seconds >= 0 else None


async def _bounded(source: str, scope: str, operation: Callable[[], Awaitable[_Check]]) -> _Check:
    try:
        async with asyncio.timeout(3):
            return await operation()
    except Exception as exc:
        return _Check(
            source=source,
            scope=scope,
            state="unavailable",
            checked_at=datetime.now(UTC),
            detail=f"Check failed ({type(exc).__name__}); no value was established.",
            next_check="Check source connectivity, permissions and configured target policy, then retry.",
        )


async def _observation(redis: Any, prefix: str, source: str, scope: str, stale_after: float) -> _Check:
    values = await redis.mget([f"{prefix}:doctor:{source}:{suffix}" for suffix in ("attempt", "outcome", "success")])
    attempted, outcome, success = values
    if isinstance(outcome, bytes):
        outcome = outcome.decode()
    age = _age(attempted)
    state: _State = (
        "unknown"
        if age is None or outcome not in {"ok", "unavailable"}
        else "stale"
        if age > stale_after
        else "ok"
        if outcome == "ok"
        else "unavailable"
    )
    return _Check(
        source=source,
        scope=scope,
        state=state,
        checked_at=_stamp(attempted),
        last_successful_at=_stamp(success),
        detail=(
            "Last completed attempt; a successful pull may be empty. "
            "Delivery means provider accepted email, not inbox receipt. Evidence expires after 7 days."
        ),
        next_check="Compare this attempt with retained evidence; check worker logs if absent or failing.",
    )


class _Doctor:
    def __init__(self, runtime: StudioRuntime) -> None:
        self.runtime = runtime

    async def studio(self) -> _Report:
        rt = self.runtime

        async def ready() -> _Check:
            if rt.database is not None:
                await rt.database.check_ready()
                await rt.database.check_schema()
            await rt.redis.ping()
            return _Check(
                source="readiness",
                scope="Studio database/schema and Redis (same checks as /readyz)",
                state="ok",
                checked_at=datetime.now(UTC),
                detail="Dependencies respond. This does not establish fresh data or upstream provider health.",
                next_check="Inspect service evidence and background refresh timestamps below.",
            )

        async def backlog() -> _Check:
            if rt.database is None:
                return _Check(
                    source="outbox",
                    scope="Studio Redis-only deployment",
                    state="unsupported",
                    detail="This deployment does not use the PostgreSQL publication outbox.",
                    next_check="Inspect event ingestion evidence per service.",
                )
            # Existing partial index bounds admission and order; include delayed retries.
            async with rt.database.sessions() as session:
                rows = (
                    await session.execute(
                        select(outbox.c.created_at, outbox.c.attempt_count)
                        .where(outbox.c.delivered_at.is_(None))
                        .order_by(outbox.c.available_at, outbox.c.id)
                        .limit(101)
                    )
                ).all()
            sample = rows[:100]
            ages = [_age(row.created_at) for row in sample]
            valid = [age for age in ages if age is not None]
            oldest = max(valid) if len(valid) == len(sample) and valid else None
            return _Check(
                source="outbox",
                scope="Up to 100 pending PostgreSQL publications, ordered by available_at; includes retries",
                state="empty" if not rows else "unknown" if oldest is None else "stale" if oldest > 60 else "ok",
                checked_at=datetime.now(UTC),
                value=oldest,
                unit="seconds",
                detail=(
                    f"{len(sample)} pending rows sampled{' (more exist)' if len(rows) > 100 else ''}; "
                    "oldest age within sample only. Empty is no pending publications, not proof of ingestion."
                ),
                next_check="Check outbox relay/Redis if age grows; sampling does not establish total backlog.",
            )

        async def notification() -> _Check:
            if not rt.failed_task_email_configured:
                return _Check(
                    source="delivery",
                    scope="Studio failed-task email",
                    state="unconfigured",
                    detail="Notification delivery is not configured.",
                    next_check="Configure the existing email integration if delivery is required.",
                )
            assert rt.failed_task_email_settings_store is not None
            settings = await rt.failed_task_email_settings_store.get()
            if not settings.enabled:
                return _Check(
                    source="delivery",
                    scope="Studio failed-task email",
                    state="disabled",
                    detail="Email integration is configured but disabled by runtime settings.",
                    next_check="Enable delivery in existing failed-task email settings if intended.",
                )
            worker = rt.failed_task_email_worker
            assert worker is not None
            return await _observation(
                rt.redis,
                worker.notification_service.config.redis_prefix,
                "delivery",
                "Studio failed-task email provider acceptance",
                86400,
            )

        checks = list(
            await asyncio.gather(
                _bounded("readiness", "Studio dependencies", ready),
                _bounded("outbox", "Studio PostgreSQL publication backlog", backlog),
                _bounded("delivery", "Studio failed-task email", notification),
            )
        )
        for name, worker, task in (
            ("health_refresh_loop", rt.health_refresh_worker, rt.health_refresh_task),
            ("event_pull_loop", rt.pull_sync_worker, rt.pull_sync_task),
            ("retention_loop", rt.retention_worker, rt.retention_task),
        ):
            checks.append(
                _Check(
                    source=name,
                    scope="This Studio process only",
                    state="disabled"
                    if worker is None
                    else "unknown"
                    if task is None
                    else "unavailable"
                    if task.done()
                    else "ok",
                    checked_at=datetime.now(UTC),
                    detail=(
                        "Local task scheduling state only; another replica may hold the coordinator lock. "
                        "Running does not establish successful refresh."
                    ),
                    next_check="Compare per-service refresh evidence; inspect this process logs if the loop stopped.",
                )
            )
        return _Report(checks=checks)

    async def _recent(self, service_id: str) -> list[StudioControlPlaneEvent]:
        rt = self.runtime
        sample_limit = min(20, max(1, rt.event_store.history_maxlen))
        if rt.database is None:
            store = rt.event_store
            ids = await rt.redis.lrange(store.service_history_key(service_id), 0, sample_limit - 1)
            if not ids:
                return []
            payloads = await rt.redis.mget(
                [store.event_key(item.decode() if isinstance(item, bytes) else str(item)) for item in ids]
            )
            return [StudioControlPlaneEvent.model_validate_json(payload) for payload in payloads if payload]
        # Fetch only small evidence columns using the existing service/effective-time index.
        now = datetime.now(UTC)
        async with rt.database.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            events.c.service_id,
                            events.c.ingested_at,
                            events.c.event_timestamp_text,
                            events.c.task_id,
                            events.c.source_kind,
                            events.c.event_type,
                            events.c.dedupe_key,
                            events.c.ingest_method,
                        )
                        .where(
                            events.c.service_id == service_id,
                            (events.c.expires_at.is_(None) | (events.c.expires_at > now)),
                        )
                        .order_by(
                            func.coalesce(events.c.event_timestamp, events.c.ingested_at).desc(), events.c.id.desc()
                        )
                        .limit(sample_limit)
                    )
                )
                .mappings()
                .all()
            )
        return [
            StudioControlPlaneEvent(
                **{**dict(row), "timestamp": row["event_timestamp_text"], "ingested_at": row["ingested_at"].isoformat()}
            )
            for row in rows
        ]

    async def service(self, service_id: str, *, live: bool = False, task_id: str | None = None) -> _Report:
        rt = self.runtime
        async with asyncio.timeout(3):
            service = await rt.registry_service.get_service(service_id)
        scope = f"service={service_id}; environment={service.environment}"
        checks: list[_Check] = []

        async def health() -> _Check:
            # Materialize current health but interpret worker activity separately.
            doc = (
                await rt.health_service.refresh_health(service_id)
                if live and service.status != ServiceStatus.DISABLED
                else await rt.health_service.get_health(service_id)
            )
            stamp = _stamp(doc.capability_status.last_successful_at)
            age = _age(stamp)
            return _Check(
                source="capabilities",
                scope=scope,
                state="disabled"
                if service.status == ServiceStatus.DISABLED
                else "unavailable"
                if doc.http_status.state == "unreachable"
                else "unknown"
                if age is None
                else "stale"
                if age > rt.health_service.capability_stale_after_seconds
                else "ok",
                checked_at=_stamp(doc.last_checked_at),
                last_successful_at=stamp,
                detail=(
                    "Capability refresh establishes service HTTP reachability and advertised support only. "
                    "Worker timestamps reflect task activity, not periodic liveness."
                ),
                next_check="Run a check, then compare the feed sample, indexed evidence and provider results.",
            )

        checks.append(await _bounded("capabilities", scope, health))
        # Refresh may update the registry capability document.
        async with asyncio.timeout(3):
            service = await rt.registry_service.get_service(service_id)
        try:
            document = CapabilityDocument.model_validate(service.capabilities) if service.capabilities else None
        except ValueError:
            document = None
        checks.append(
            _Check(
                source="supported_routes",
                scope=scope,
                state="ok" if document else "unknown",
                evidence_at=_stamp(service.last_seen_at),
                detail=", ".join(document.supported_routes)
                if document
                else "No valid capability document is available.",
                next_check="Advertised support is not a successful request; verify the relevant source.",
            )
        )
        recent: list[StudioControlPlaneEvent] = []
        sample_observed_at = datetime.now(UTC)

        async def retained() -> _Check:
            nonlocal recent, sample_observed_at
            recent = await self._recent(service_id)
            sample_observed_at = datetime.now(UTC)
            stamped = [(item, _stamp(item.timestamp)) for item in recent]
            valid = [(item, stamp) for item, stamp in stamped if stamp is not None]
            latest, occurred = max(valid, key=lambda pair: pair[1]) if valid else (None, None)
            age = _age(occurred, sample_observed_at)
            return _Check(
                source="indexed_events",
                scope=f"{scope}; up to 20 retained event records",
                observed_at=sample_observed_at,
                state="empty"
                if not recent
                else "unknown"
                if age is None
                else "stale"
                if age > rt.health_service.observation_stale_after_seconds
                else "ok",
                checked_at=datetime.now(UTC),
                evidence_at=occurred,
                value=age,
                unit="seconds since source event",
                detail=(
                    f"{len(recent)} retained records sampled. Source={latest.source_kind if latest else 'unknown'}. "
                    "No timezone means freshness is unknown. "
                    "Empty is limited to retained evidence, not all task history."
                ),
                next_check=(
                    "Compare feed/ingestion, environment and retention; "
                    "emit a known test task if expected evidence is missing."
                ),
            )

        checks.append(await _bounded("indexed_events", scope, retained))
        valid_pairs = [(item, _stamp(item.timestamp), _stamp(item.ingested_at)) for item in recent]
        lags = [
            (ingested - occurred).total_seconds()
            for _, occurred, ingested in valid_pairs
            if occurred and ingested and occurred <= ingested <= sample_observed_at
        ]
        checks.append(
            _Check(
                source="ingestion_lag",
                observed_at=sample_observed_at,
                scope=f"{scope}; up to 20 retained records with timezone-aware, nonnegative timestamp pairs",
                state="ok" if lags else "unknown",
                value=max(lags) if lags else None,
                unit="seconds",
                checked_at=datetime.now(UTC),
                detail=(
                    "Maximum observed source-to-index delay in the sample. "
                    "Not current queue lag; invalid/future timestamp pairs are excluded."
                ),
                next_check=(
                    "Compare source clocks and the latest event time; "
                    "an old sample cannot establish current ingestion health."
                ),
            )
        )
        ttl = rt.event_store.ttl_seconds
        checks.append(
            _Check(
                source="retention",
                scope=scope,
                state="ok",
                value=ttl,
                unit="seconds configured event TTL"
                if rt.database
                else "seconds configured event TTL / Redis activity TTL",
                detail=(
                    f"Event TTL={ttl if ttl is not None else 'no time expiry'}; "
                    f"task index TTL={rt.search_service.task_index_ttl_seconds}s; "
                    f"service history cap={rt.event_store.history_maxlen}. "
                    "These are limits, not guaranteed continuous coverage."
                ),
                next_check=(
                    "Keep investigation windows inside available retention; "
                    "retained samples cannot prove complete history."
                ),
            )
        )

        async def pull() -> _Check:
            if document is None:
                return _Check(
                    source="event_pull",
                    scope=scope,
                    detail="Support is unknown until capability discovery succeeds.",
                    next_check="Refresh capabilities first.",
                )
            if not _supports_events_feed(service):
                return _Check(
                    source="event_pull",
                    scope=scope,
                    state="unsupported",
                    detail="Service does not advertise the compatible event feed.",
                    next_check="Check SDK capabilities and configured push ingestion instead.",
                )
            observation = await _observation(
                rt.redis,
                rt.event_store.prefix,
                f"pull:{service_id}",
                scope,
                max(60, (rt.pull_sync_worker.interval_seconds * 3) if rt.pull_sync_worker else 60),
            )
            if rt.pull_sync_worker is None:
                observation.state = "disabled"
                observation.detail = "Periodic pull is disabled in this deployment. " + observation.detail
            return observation

        pull_check = await _bounded("event_pull", scope, pull)
        pull_check.source = "event_pull"
        checks.append(pull_check)
        if live and service.status != ServiceStatus.DISABLED:

            async def feed() -> _Check:
                if document is None or not _supports_events_feed(service):
                    return _Check(
                        source="feed_sample",
                        scope=scope,
                        state="unknown" if document is None else "unsupported",
                        detail="No compatible advertised feed available to probe.",
                        next_check="Check capability discovery or push ingestion.",
                    )
                rt.outbound_policy.validate_url(service.base_url, label="Doctor service target")
                content = bytearray()
                async with rt.http_client.stream(
                    "GET", f"{service.base_url.rstrip('/')}/events/feed", params={"limit": 1}, follow_redirects=False
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise ValueError("Feed sample exceeded 64 KiB")
                payload = RelaynaServiceEventFeedResponse.model_validate_json(bytes(content))
                return _Check(
                    source="feed_sample",
                    scope=f"{scope}; configured upstream /events/feed?limit=1, maximum 64 KiB",
                    state="ok" if payload.items else "empty",
                    checked_at=datetime.now(UTC),
                    evidence_at=_stamp(payload.items[0].timestamp) if payload.items else None,
                    detail="Upstream returned an event."
                    if payload.items
                    else "Upstream successfully returned no events. This does not prove ingestion works.",
                    next_check="Compare a known task with indexed evidence; this sample does not ingest events.",
                )

            sample_task = task_id or (recent[0].task_id if recent else None)
            log_scope = (
                f"{scope}; Loki; task={sample_task or 'not selected (service selector only)'}; last 15 minutes; limit=1"
            )

            async def logs() -> _Check:
                if not service.log_config:
                    return _Check(
                        source="log_sample",
                        scope=scope,
                        state="unconfigured",
                        detail="No log provider configured.",
                        next_check="Configure existing Loki integration if log evidence is required.",
                    )
                now = datetime.now(UTC)
                query = StudioLogQuery(
                    limit=1, from_time=(now - timedelta(minutes=15)).isoformat(), to_time=now.isoformat()
                )
                result = (
                    await rt.log_query_service.query_task_logs(service_id, sample_task, query)
                    if sample_task
                    else await rt.log_query_service.query_service_logs(service_id, query)
                )
                return _Check(
                    source="log_sample",
                    scope=log_scope,
                    state="ok" if result.items else "empty",
                    checked_at=datetime.now(UTC),
                    evidence_at=_stamp(result.items[0].timestamp) if result.items else None,
                    detail="Matching log evidence returned."
                    if result.items
                    else (
                        "Query succeeded with no matching log evidence in this scope. "
                        "This does not establish task absence or ingestion health."
                    ),
                    next_check="Check selector labels, task matching, time window and provider retention.",
                )

            checks.extend(
                await asyncio.gather(_bounded("feed_sample", scope, feed), _bounded("log_sample", log_scope, logs))
            )
        for source, config, query_service in (
            ("logs", service.log_config, rt.log_query_service),
            ("metrics", service.metrics_config, rt.metrics_query_service),
            ("traces", service.trace_config, rt.trace_query_service),
        ):
            availability = query_service._availability.describe(_provider_key(service_id, config)) if config else {}
            checks.append(
                _Check(
                    source=source,
                    scope=scope,
                    state="unconfigured" if config is None else availability.get("state", "unknown"),
                    checked_at=availability.get("checked_at"),
                    last_successful_at=availability.get("last_successful_at"),
                    detail=availability.get("detail", "No provider configured for this service."),
                    next_check=(
                        "The next query checks availability. Failed requests back off with jitter up to five minutes, "
                        "then one query checks recovery. Other features remain available."
                    ),
                )
            )
        return _Report(service_id=service_id, environment=service.environment, checks=checks)


def _create_doctor_router(runtime: StudioRuntime) -> APIRouter:
    router = APIRouter()
    doctor = _Doctor(runtime)

    @router.get("/studio/health", response_model=_Report)
    async def studio_health(response: Response) -> _Report:
        response.headers["Cache-Control"] = "no-store"
        return await doctor.studio()

    @router.get("/studio/services/{service_id}/doctor", response_model=_Report)
    async def service_doctor(service_id: str, response: Response) -> _Report:
        response.headers["Cache-Control"] = "no-store"
        try:
            return await doctor.service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Service not found.") from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Service registry read timed out.") from exc

    @router.post("/studio/services/{service_id}/doctor/check", response_model=_Report)
    async def check_service(
        service_id: str, response: Response, task_id: str | None = Query(default=None, min_length=1, max_length=255)
    ) -> _Report:
        response.headers["Cache-Control"] = "no-store"
        try:
            return await doctor.service(service_id, live=True, task_id=task_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Service not found.") from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Service registry read timed out.") from exc

    return router
