from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from relayna.api import HEALTH_WORKERS_ROUTE_ID, CapabilityDocument, WorkerHeartbeatListResponse, WorkerHeartbeatSummary

from .events import StudioServiceActivitySnapshot
from .registry import (
    CapabilityRefreshError,
    OutboundUrlPolicyError,
    ServiceNotFoundError,
    ServiceRecord,
    ServiceRegistryService,
    ServiceStatus,
    StudioOutboundUrlPolicy,
)

if TYPE_CHECKING:
    from .search import StudioSearchIndexer


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _latest_datetime(*values: datetime | None) -> datetime | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    return max(items)


class StudioOverallHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class StudioHttpReachability(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class CapabilityHealthState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    ERROR = "error"


class ObservationFreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


class WorkerHealthState(StrEnum):
    HEALTHY = "healthy"
    STALE = "stale"
    UNHEALTHY = "unhealthy"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class HttpStatusSummary(BaseModel):
    state: StudioHttpReachability = StudioHttpReachability.UNKNOWN
    checked_at: datetime | None = None
    error_detail: str | None = None


class CapabilityHealthSummary(BaseModel):
    state: CapabilityHealthState = CapabilityHealthState.MISSING
    checked_at: datetime | None = None
    last_successful_at: datetime | None = None
    error_detail: str | None = None


class ObservationFreshnessSummary(BaseModel):
    state: ObservationFreshnessState = ObservationFreshnessState.MISSING
    latest_status_event_at: datetime | None = None
    latest_observation_event_at: datetime | None = None
    latest_ingested_at: datetime | None = None


class WorkerHealthSummary(BaseModel):
    state: WorkerHealthState = WorkerHealthState.UNKNOWN
    reported_at: datetime | None = None
    latest_heartbeat_at: datetime | None = None
    workers: list[WorkerHeartbeatSummary] = Field(default_factory=list)
    detail: str | None = None


class StudioServiceHealthDocument(BaseModel):
    service_id: str
    registry_status: ServiceStatus
    http_status: HttpStatusSummary = Field(default_factory=HttpStatusSummary)
    capability_status: CapabilityHealthSummary = Field(default_factory=CapabilityHealthSummary)
    observation_freshness: ObservationFreshnessSummary = Field(default_factory=ObservationFreshnessSummary)
    worker_health: WorkerHealthSummary = Field(default_factory=WorkerHealthSummary)
    last_checked_at: datetime | None = None
    overall_status: StudioOverallHealthStatus = StudioOverallHealthStatus.UNKNOWN


class StudioServiceHealthStore(Protocol):
    async def get_health(self, service_id: str) -> StudioServiceHealthDocument | None: ...

    async def set_health(
        self, service_id: str, document: StudioServiceHealthDocument
    ) -> StudioServiceHealthDocument: ...


class StudioServiceActivityReader(Protocol):
    async def get_service_activity_snapshot(self, service_id: str) -> StudioServiceActivitySnapshot: ...


class RedisStudioHealthStore:
    def __init__(self, redis: Any, *, prefix: str = "studio:health") -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, service_id: str) -> str:
        return f"{self._prefix}:{service_id}"

    async def get_health(self, service_id: str) -> StudioServiceHealthDocument | None:
        payload = await cast(Awaitable[str | bytes | None], self._redis.get(self._key(service_id)))
        if payload is None:
            return None
        try:
            return StudioServiceHealthDocument.model_validate_json(payload)
        except ValidationError:
            return None

    async def set_health(self, service_id: str, document: StudioServiceHealthDocument) -> StudioServiceHealthDocument:
        await self._redis.set(self._key(service_id), document.model_dump_json())
        return document


@dataclass(slots=True)
class StudioHealthRefreshService:
    registry_service: ServiceRegistryService
    health_store: StudioServiceHealthStore
    activity_reader: StudioServiceActivityReader
    http_client: httpx.AsyncClient
    search_indexer: StudioSearchIndexer | None = None
    outbound_policy: StudioOutboundUrlPolicy | None = None
    capability_stale_after_seconds: int = 180
    observation_stale_after_seconds: int = 300
    worker_heartbeat_stale_after_seconds: int = 90

    async def get_health(self, service_id: str) -> StudioServiceHealthDocument:
        service = await self.registry_service.get_service(service_id)
        stored = await self.health_store.get_health(service.service_id)
        snapshot = await self.activity_reader.get_service_activity_snapshot(service.service_id)
        return self._materialize_document(service, snapshot, stored)

    async def build_service_record(self, service: ServiceRecord) -> ServiceRecord:
        health = await self.get_health(service.service_id)
        return service.model_copy(update={"health": health.model_dump(mode="json")})

    async def build_service_records(self, services: list[ServiceRecord]) -> list[ServiceRecord]:
        return [await self.build_service_record(service) for service in services]

    async def refresh_health(
        self, service_id: str, *, use_cached_capabilities: bool = False
    ) -> StudioServiceHealthDocument:
        service = await self.registry_service.get_service(service_id)
        snapshot = await self.activity_reader.get_service_activity_snapshot(service.service_id)
        previous = await self.health_store.get_health(service.service_id)
        now = _utcnow()
        current = (
            previous.model_copy(deep=True)
            if previous is not None
            else self._empty_document(service.service_id, service.status)
        )
        current.registry_status = service.status
        current.last_checked_at = now

        capability_document = self._capability_document(service)
        if service.status == ServiceStatus.DISABLED:
            document = self._materialize_document(service, snapshot, current, now=now)
            await self.health_store.set_health(service.service_id, document)
            if self.search_indexer is not None:
                await self.search_indexer.upsert_service_document(service, health_status=document.overall_status.value)
            return document

        if use_cached_capabilities:
            current.http_status = HttpStatusSummary(state=StudioHttpReachability.REACHABLE, checked_at=now)
            current.capability_status.checked_at = now
            if service.last_seen_at is not None:
                current.capability_status.last_successful_at = service.last_seen_at
            if current.capability_status.last_successful_at is None:
                current.capability_status.last_successful_at = now
            current.capability_status.error_detail = None
        else:
            try:
                _, fetched = await self.registry_service.fetch_capability_document(service.service_id)
                service = await self.registry_service.store_capability_document(
                    service.service_id,
                    fetched,
                    mark_healthy=False,
                )
                capability_document = fetched
                current.http_status = HttpStatusSummary(state=StudioHttpReachability.REACHABLE, checked_at=now)
                current.capability_status.checked_at = now
                current.capability_status.last_successful_at = now
                current.capability_status.error_detail = None
            except (CapabilityRefreshError, ServiceNotFoundError, Exception) as exc:
                current.http_status = HttpStatusSummary(
                    state=StudioHttpReachability.UNREACHABLE,
                    checked_at=now,
                    error_detail=str(exc),
                )
                current.capability_status.checked_at = now
                current.capability_status.error_detail = str(exc)

        if capability_document is not None and self._supports_worker_route(capability_document):
            try:
                worker_payload = await self._fetch_worker_health(service)
                current.worker_health = WorkerHealthSummary(
                    reported_at=worker_payload.reported_at,
                    latest_heartbeat_at=_latest_datetime(
                        *[_parse_datetime(worker.last_heartbeat_at) for worker in worker_payload.workers]
                    ),
                    workers=worker_payload.workers,
                    detail=None,
                )
            except Exception as exc:
                current.worker_health.detail = str(exc)
        else:
            current.worker_health = WorkerHealthSummary(state=WorkerHealthState.UNSUPPORTED)

        document = self._materialize_document(service, snapshot, current, now=now)
        await self.health_store.set_health(service.service_id, document)
        if self.search_indexer is not None:
            await self.search_indexer.upsert_service_document(service, health_status=document.overall_status.value)
        return document

    async def refresh_all_services(self) -> None:
        services = await self.registry_service.list_services()
        for service in services:
            if service.status == ServiceStatus.DISABLED:
                document = await self.get_health(service.service_id)
                await self.health_store.set_health(service.service_id, document)
                continue
            try:
                await self.refresh_health(service.service_id)
            except Exception:
                continue

    async def _fetch_worker_health(self, service: ServiceRecord) -> WorkerHeartbeatListResponse:
        self._validate_service_base_url(service)
        response = await self.http_client.get(f"{service.base_url.rstrip('/')}/relayna/health/workers")
        response.raise_for_status()
        return WorkerHeartbeatListResponse.model_validate(response.json())

    def _validate_service_base_url(self, service: ServiceRecord) -> None:
        policy = self.outbound_policy or StudioOutboundUrlPolicy()
        try:
            policy.validate_url(service.base_url, label=f"Service '{service.service_id}' base_url")
        except OutboundUrlPolicyError as exc:
            raise RuntimeError(str(exc)) from exc

    def _empty_document(self, service_id: str, registry_status: ServiceStatus) -> StudioServiceHealthDocument:
        return StudioServiceHealthDocument(service_id=service_id, registry_status=registry_status)

    def _materialize_document(
        self,
        service: ServiceRecord,
        snapshot: StudioServiceActivitySnapshot,
        stored: StudioServiceHealthDocument | None,
        *,
        now: datetime | None = None,
    ) -> StudioServiceHealthDocument:
        current = (
            stored.model_copy(deep=True)
            if stored is not None
            else self._empty_document(service.service_id, service.status)
        )
        current.service_id = service.service_id
        current.registry_status = service.status
        now = now or _utcnow()

        current.http_status = self._materialize_http_status(current.http_status)
        current.capability_status = self._materialize_capability_status(service, current.capability_status, now)
        current.observation_freshness = self._materialize_observation_freshness(snapshot, now)
        current.worker_health = self._materialize_worker_health(service, current.worker_health, now)
        current.overall_status = self._derive_overall_status(current)
        return current

    def _materialize_http_status(self, status: HttpStatusSummary) -> HttpStatusSummary:
        if status.state == StudioHttpReachability.REACHABLE and status.error_detail:
            return status.model_copy(update={"error_detail": None})
        return status

    def _materialize_capability_status(
        self,
        service: ServiceRecord,
        status: CapabilityHealthSummary,
        now: datetime,
    ) -> CapabilityHealthSummary:
        last_successful_at = status.last_successful_at or service.last_seen_at
        error_detail = status.error_detail
        if last_successful_at is None and not service.capabilities:
            return CapabilityHealthSummary(
                state=CapabilityHealthState.ERROR if error_detail else CapabilityHealthState.MISSING,
                checked_at=status.checked_at,
                last_successful_at=None,
                error_detail=error_detail,
            )
        if last_successful_at is None:
            return CapabilityHealthSummary(
                state=CapabilityHealthState.ERROR if error_detail else CapabilityHealthState.MISSING,
                checked_at=status.checked_at,
                last_successful_at=None,
                error_detail=error_detail,
            )
        age = now - last_successful_at
        if age > timedelta(seconds=self.capability_stale_after_seconds):
            state = CapabilityHealthState.STALE
        else:
            state = CapabilityHealthState.FRESH
        if error_detail and state != CapabilityHealthState.STALE:
            state = CapabilityHealthState.FRESH
        return CapabilityHealthSummary(
            state=state,
            checked_at=status.checked_at or last_successful_at,
            last_successful_at=last_successful_at,
            error_detail=error_detail,
        )

    def _materialize_observation_freshness(
        self,
        snapshot: StudioServiceActivitySnapshot,
        now: datetime,
    ) -> ObservationFreshnessSummary:
        latest_status = _parse_datetime(snapshot.latest_status_event_at)
        latest_observation = _parse_datetime(snapshot.latest_observation_event_at)
        latest_ingested = _parse_datetime(snapshot.latest_ingested_at)
        latest_event = _latest_datetime(latest_status, latest_observation)
        if latest_event is None:
            return ObservationFreshnessSummary(
                state=ObservationFreshnessState.MISSING,
                latest_status_event_at=latest_status,
                latest_observation_event_at=latest_observation,
                latest_ingested_at=latest_ingested,
            )
        state = (
            ObservationFreshnessState.STALE
            if now - latest_event > timedelta(seconds=self.observation_stale_after_seconds)
            else ObservationFreshnessState.FRESH
        )
        return ObservationFreshnessSummary(
            state=state,
            latest_status_event_at=latest_status,
            latest_observation_event_at=latest_observation,
            latest_ingested_at=latest_ingested,
        )

    def _materialize_worker_health(
        self,
        service: ServiceRecord,
        status: WorkerHealthSummary,
        now: datetime,
    ) -> WorkerHealthSummary:
        capability_document = self._capability_document(service)
        if capability_document is None:
            return WorkerHealthSummary(
                state=WorkerHealthState.UNKNOWN,
                reported_at=status.reported_at,
                latest_heartbeat_at=status.latest_heartbeat_at,
                workers=status.workers,
                detail=status.detail,
            )
        if not self._supports_worker_route(capability_document):
            return WorkerHealthSummary(state=WorkerHealthState.UNSUPPORTED)
        if not status.workers:
            return WorkerHealthSummary(
                state=WorkerHealthState.UNKNOWN,
                reported_at=status.reported_at,
                latest_heartbeat_at=status.latest_heartbeat_at,
                workers=status.workers,
                detail=status.detail,
            )
        latest_heartbeat_at = _parse_datetime(status.latest_heartbeat_at)
        if any(not worker.running for worker in status.workers):
            state = WorkerHealthState.UNHEALTHY
        elif latest_heartbeat_at is None:
            state = WorkerHealthState.STALE
        elif now - latest_heartbeat_at > timedelta(seconds=self.worker_heartbeat_stale_after_seconds):
            state = WorkerHealthState.STALE
        else:
            state = WorkerHealthState.HEALTHY
        return WorkerHealthSummary(
            state=state,
            reported_at=status.reported_at,
            latest_heartbeat_at=latest_heartbeat_at,
            workers=status.workers,
            detail=status.detail,
        )

    def _derive_overall_status(self, document: StudioServiceHealthDocument) -> StudioOverallHealthStatus:
        if document.registry_status == ServiceStatus.DISABLED:
            return StudioOverallHealthStatus.DISABLED
        if document.http_status.state == StudioHttpReachability.UNREACHABLE:
            return StudioOverallHealthStatus.UNREACHABLE
        if (
            document.capability_status.state == CapabilityHealthState.STALE
            or document.observation_freshness.state == ObservationFreshnessState.STALE
            or document.worker_health.state == WorkerHealthState.STALE
        ):
            return StudioOverallHealthStatus.STALE
        if (
            document.worker_health.state == WorkerHealthState.UNHEALTHY
            or document.capability_status.state == CapabilityHealthState.ERROR
            or document.worker_health.state == WorkerHealthState.UNKNOWN
        ):
            return StudioOverallHealthStatus.DEGRADED
        if (
            document.capability_status.state == CapabilityHealthState.FRESH
            and document.observation_freshness.state == ObservationFreshnessState.FRESH
            and document.worker_health.state in {WorkerHealthState.HEALTHY, WorkerHealthState.UNSUPPORTED}
        ):
            return StudioOverallHealthStatus.HEALTHY
        return StudioOverallHealthStatus.UNKNOWN

    def _capability_document(self, service: ServiceRecord) -> CapabilityDocument | None:
        if not service.capabilities:
            return None
        try:
            return CapabilityDocument.model_validate(service.capabilities)
        except ValidationError:
            return None

    def _supports_worker_route(self, document: CapabilityDocument) -> bool:
        return (
            document.service_metadata.compatibility == "capabilities_v1"
            and HEALTH_WORKERS_ROUTE_ID in document.supported_routes
        )


@dataclass(slots=True)
class StudioHealthRefreshWorker:
    health_service: StudioHealthRefreshService
    interval_seconds: float = 60.0
    _stopped: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            await self.health_service.refresh_all_services()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


def create_studio_health_router(
    *,
    health_service: StudioHealthRefreshService,
    prefix: str = "/studio/services",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}" + "/{service_id}/health", response_model=StudioServiceHealthDocument)
    async def get_service_health(service_id: str) -> StudioServiceHealthDocument:
        try:
            return await health_service.get_health(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(f"{prefix}" + "/{service_id}/health/refresh", response_model=StudioServiceHealthDocument)
    async def refresh_service_health(service_id: str) -> StudioServiceHealthDocument:
        try:
            return await health_service.refresh_health(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


__all__ = [
    "CapabilityHealthState",
    "CapabilityHealthSummary",
    "HttpStatusSummary",
    "ObservationFreshnessState",
    "ObservationFreshnessSummary",
    "RedisStudioHealthStore",
    "StudioHealthRefreshService",
    "StudioHealthRefreshWorker",
    "StudioHttpReachability",
    "StudioOverallHealthStatus",
    "StudioServiceHealthDocument",
    "WorkerHealthState",
    "WorkerHealthSummary",
    "create_studio_health_router",
]
