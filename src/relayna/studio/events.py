from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from ..api import EVENTS_FEED_ROUTE_ID, CapabilityDocument, sse_response
from ..observability import (
    RelaynaServiceEvent,
    RelaynaServiceEventFeedResponse,
    ServiceEventSourceKind,
    StudioEventIngestMethod,
)
from .registry import ServiceNotFoundError, ServiceRecord, ServiceRegistryService, ServiceStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _timestamp_key(value: str | None) -> str:
    return value or ""


class StudioEventEnvelope(BaseModel):
    service_id: str = Field(min_length=1)
    ingest_method: StudioEventIngestMethod
    event: RelaynaServiceEvent


class StudioEventIngestRequest(BaseModel):
    events: list[StudioEventEnvelope] = Field(default_factory=list)


class StudioEventIngestResponse(BaseModel):
    inserted: int = 0
    duplicate: int = 0
    invalid: int = 0


class StudioControlPlaneEvent(BaseModel):
    service_id: str
    ingest_method: StudioEventIngestMethod
    ingested_at: str
    dedupe_key: str
    out_of_order: bool = False
    task_id: str
    event_type: str
    source_kind: ServiceEventSourceKind
    component: str | None = None
    timestamp: str | None = None
    event_id: str | None = None
    correlation_id: str | None = None
    parent_task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StudioEventListResponse(BaseModel):
    count: int
    items: list[StudioControlPlaneEvent] = Field(default_factory=list)
    next_cursor: str | None = None


class StudioServiceActivitySnapshot(BaseModel):
    service_id: str
    latest_status_event_at: str | None = None
    latest_observation_event_at: str | None = None
    latest_ingested_at: str | None = None


class StudioEventStore(Protocol):
    async def insert_event(self, envelope: StudioEventEnvelope) -> bool: ...

    async def list_service_events(
        self,
        service_id: str,
        *,
        task_id: str | None = None,
        source_kind: ServiceEventSourceKind | None = None,
        event_type: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse: ...

    async def list_task_events(
        self,
        service_id: str,
        task_id: str,
        *,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse: ...

    async def get_pull_cursor(self, service_id: str) -> str | None: ...

    async def set_pull_cursor(self, service_id: str, cursor: str) -> None: ...

    async def get_service_activity_snapshot(self, service_id: str) -> StudioServiceActivitySnapshot: ...

    def service_channel(self, service_id: str) -> str: ...

    def task_channel(self, service_id: str, task_id: str) -> str: ...


class RedisStudioEventStore:
    def __init__(
        self,
        redis: Any,
        *,
        prefix: str = "studio:events",
        ttl_seconds: int | None = 86400,
        history_maxlen: int = 5000,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen

    def event_key(self, dedupe_key: str) -> str:
        return f"{self.prefix}:event:{dedupe_key}"

    def service_history_key(self, service_id: str) -> str:
        return f"{self.prefix}:service:{service_id}:history"

    def task_history_key(self, service_id: str, task_id: str) -> str:
        return f"{self.prefix}:task:{service_id}:{task_id}:history"

    def task_latest_timestamp_key(self, service_id: str, task_id: str) -> str:
        return f"{self.prefix}:task:{service_id}:{task_id}:latest-timestamp"

    def service_latest_status_timestamp_key(self, service_id: str) -> str:
        return f"{self.prefix}:service:{service_id}:latest-status-timestamp"

    def service_latest_observation_timestamp_key(self, service_id: str) -> str:
        return f"{self.prefix}:service:{service_id}:latest-observation-timestamp"

    def service_latest_ingested_timestamp_key(self, service_id: str) -> str:
        return f"{self.prefix}:service:{service_id}:latest-ingested-timestamp"

    def pull_cursor_key(self, service_id: str) -> str:
        return f"{self.prefix}:pull-cursor:{service_id}"

    def service_channel(self, service_id: str) -> str:
        return f"{self.prefix}:channel:service:{service_id}"

    def task_channel(self, service_id: str, task_id: str) -> str:
        return f"{self.prefix}:channel:task:{service_id}:{task_id}"

    async def insert_event(self, envelope: StudioEventEnvelope) -> bool:
        event = envelope.event
        dedupe_key = _event_dedupe_key(envelope.service_id, event)
        latest_timestamp = await cast(
            Awaitable[str | bytes | None],
            self.redis.get(self.task_latest_timestamp_key(envelope.service_id, event.task_id)),
        )
        latest_timestamp_value = latest_timestamp.decode() if isinstance(latest_timestamp, bytes) else latest_timestamp
        normalized = StudioControlPlaneEvent(
            service_id=envelope.service_id,
            ingest_method=envelope.ingest_method,
            ingested_at=_utcnow().isoformat(),
            dedupe_key=dedupe_key,
            out_of_order=(
                latest_timestamp_value is not None
                and event.timestamp is not None
                and _timestamp_key(event.timestamp) < _timestamp_key(latest_timestamp_value)
            ),
            task_id=event.task_id,
            event_type=event.event_type,
            source_kind=event.source_kind,
            component=event.component,
            timestamp=event.timestamp,
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            parent_task_id=event.parent_task_id,
            payload=event.payload,
        )
        serialized = normalized.model_dump_json()
        set_kwargs: dict[str, Any] = {"nx": True}
        if self.ttl_seconds:
            set_kwargs["ex"] = self.ttl_seconds
        inserted = await self.redis.set(self.event_key(dedupe_key), serialized, **set_kwargs)
        if not inserted:
            return False

        pipe = self.redis.pipeline()
        service_history_key = self.service_history_key(envelope.service_id)
        task_history_key = self.task_history_key(envelope.service_id, event.task_id)
        pipe.lpush(service_history_key, dedupe_key)
        pipe.ltrim(service_history_key, 0, self.history_maxlen - 1)
        pipe.lpush(task_history_key, dedupe_key)
        pipe.ltrim(task_history_key, 0, self.history_maxlen - 1)
        if self.ttl_seconds:
            pipe.expire(service_history_key, self.ttl_seconds)
            pipe.expire(task_history_key, self.ttl_seconds)
        if event.timestamp is not None and (
            latest_timestamp_value is None or _timestamp_key(event.timestamp) > _timestamp_key(latest_timestamp_value)
        ):
            pipe.set(self.task_latest_timestamp_key(envelope.service_id, event.task_id), event.timestamp)
            if self.ttl_seconds:
                pipe.expire(self.task_latest_timestamp_key(envelope.service_id, event.task_id), self.ttl_seconds)
        if event.timestamp is not None:
            if event.source_kind == ServiceEventSourceKind.STATUS:
                pipe.set(self.service_latest_status_timestamp_key(envelope.service_id), event.timestamp)
                if self.ttl_seconds:
                    pipe.expire(self.service_latest_status_timestamp_key(envelope.service_id), self.ttl_seconds)
            elif event.source_kind == ServiceEventSourceKind.OBSERVATION:
                pipe.set(self.service_latest_observation_timestamp_key(envelope.service_id), event.timestamp)
                if self.ttl_seconds:
                    pipe.expire(self.service_latest_observation_timestamp_key(envelope.service_id), self.ttl_seconds)
        pipe.set(self.service_latest_ingested_timestamp_key(envelope.service_id), normalized.ingested_at)
        if self.ttl_seconds:
            pipe.expire(self.service_latest_ingested_timestamp_key(envelope.service_id), self.ttl_seconds)
        pipe.publish(self.service_channel(envelope.service_id), serialized)
        pipe.publish(self.task_channel(envelope.service_id, event.task_id), serialized)
        await pipe.execute()
        return True

    async def list_service_events(
        self,
        service_id: str,
        *,
        task_id: str | None = None,
        source_kind: ServiceEventSourceKind | None = None,
        event_type: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse:
        items = await self._load_history(self.service_history_key(service_id))
        filtered = [
            item
            for item in items
            if (task_id is None or item.task_id == task_id)
            and (source_kind is None or item.source_kind == source_kind)
            and (event_type is None or item.event_type == event_type)
        ]
        return _page_events(filtered, before=before, limit=limit)

    async def list_task_events(
        self,
        service_id: str,
        task_id: str,
        *,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse:
        items = await self._load_history(self.task_history_key(service_id, task_id))
        return _page_events(items, before=before, limit=limit)

    async def get_pull_cursor(self, service_id: str) -> str | None:
        value = await cast(Awaitable[str | bytes | None], self.redis.get(self.pull_cursor_key(service_id)))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set_pull_cursor(self, service_id: str, cursor: str) -> None:
        await self.redis.set(self.pull_cursor_key(service_id), cursor)

    async def get_service_activity_snapshot(self, service_id: str) -> StudioServiceActivitySnapshot:
        latest_status = await cast(
            Awaitable[str | bytes | None],
            self.redis.get(self.service_latest_status_timestamp_key(service_id)),
        )
        latest_observation = await cast(
            Awaitable[str | bytes | None],
            self.redis.get(self.service_latest_observation_timestamp_key(service_id)),
        )
        latest_ingested = await cast(
            Awaitable[str | bytes | None],
            self.redis.get(self.service_latest_ingested_timestamp_key(service_id)),
        )
        return StudioServiceActivitySnapshot(
            service_id=service_id,
            latest_status_event_at=latest_status.decode() if isinstance(latest_status, bytes) else latest_status,
            latest_observation_event_at=(
                latest_observation.decode() if isinstance(latest_observation, bytes) else latest_observation
            ),
            latest_ingested_at=latest_ingested.decode() if isinstance(latest_ingested, bytes) else latest_ingested,
        )

    async def _load_history(self, key: str) -> list[StudioControlPlaneEvent]:
        ids = await cast(Awaitable[list[str | bytes]], self.redis.lrange(key, 0, max(0, self.history_maxlen - 1)))
        items: list[StudioControlPlaneEvent] = []
        for item_id in ids:
            normalized_id = item_id.decode() if isinstance(item_id, bytes) else str(item_id)
            payload = await cast(Awaitable[str | bytes | None], self.redis.get(self.event_key(normalized_id)))
            if payload is None:
                continue
            try:
                items.append(StudioControlPlaneEvent.model_validate_json(payload))
            except ValidationError:
                continue
        items.sort(key=lambda item: (_timestamp_key(item.timestamp), item.ingested_at, item.dedupe_key), reverse=True)
        return items


@dataclass(slots=True)
class StudioEventIngestService:
    registry_service: ServiceRegistryService
    event_store: StudioEventStore
    http_client: httpx.AsyncClient
    pull_page_limit: int = 200
    pull_max_pages: int = 25

    async def ingest_request(self, request: StudioEventIngestRequest) -> StudioEventIngestResponse:
        return await self.ingest_events(request.events)

    async def ingest_events(self, events: list[StudioEventEnvelope]) -> StudioEventIngestResponse:
        response = StudioEventIngestResponse()
        for envelope in events:
            try:
                await self.registry_service.get_service(envelope.service_id)
            except ServiceNotFoundError:
                response.invalid += 1
                continue
            inserted = await self.event_store.insert_event(envelope)
            if inserted:
                response.inserted += 1
            else:
                response.duplicate += 1
        return response

    async def list_service_events(
        self,
        service_id: str,
        *,
        task_id: str | None = None,
        source_kind: ServiceEventSourceKind | None = None,
        event_type: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse:
        await self.registry_service.get_service(service_id)
        return await self.event_store.list_service_events(
            service_id,
            task_id=task_id,
            source_kind=source_kind,
            event_type=event_type,
            before=before,
            limit=limit,
        )

    async def list_task_events(
        self,
        service_id: str,
        task_id: str,
        *,
        before: str | None = None,
        limit: int = 100,
    ) -> StudioEventListResponse:
        await self.registry_service.get_service(service_id)
        return await self.event_store.list_task_events(service_id, task_id, before=before, limit=limit)

    async def sync_registered_services(self) -> None:
        services = await self.registry_service.list_services()
        for service in services:
            if service.status != ServiceStatus.HEALTHY or not _supports_events_feed(service):
                continue
            try:
                await self.sync_service(service)
            except Exception:
                continue

    async def sync_service(self, service: ServiceRecord) -> None:
        stored_cursor = await self.event_store.get_pull_cursor(service.service_id)
        newest_cursor: str | None = None
        next_after: str | None = None
        for page_index in range(self.pull_max_pages):
            response = await self.http_client.get(
                f"{service.base_url.rstrip('/')}/events/feed",
                params={"limit": self.pull_page_limit, **({"after": next_after} if next_after is not None else {})},
            )
            response.raise_for_status()
            payload = RelaynaServiceEventFeedResponse.model_validate(response.json())
            if not payload.items:
                break
            if page_index == 0:
                newest_cursor = payload.items[0].cursor
            batch: list[StudioEventEnvelope] = []
            encountered_existing = False
            for item in payload.items:
                if stored_cursor is not None and item.cursor == stored_cursor:
                    encountered_existing = True
                    break
                batch.append(
                    StudioEventEnvelope(
                        service_id=service.service_id,
                        ingest_method=StudioEventIngestMethod.PULL,
                        event=item,
                    )
                )
            if batch:
                await self.ingest_events(batch)
            if stored_cursor is not None and encountered_existing:
                break
            if payload.next_cursor is None or page_index == self.pull_max_pages - 1:
                break
            next_after = payload.next_cursor
        if newest_cursor is not None:
            await self.event_store.set_pull_cursor(service.service_id, newest_cursor)


@dataclass(slots=True)
class StudioPullSyncWorker:
    ingest_service: StudioEventIngestService
    interval_seconds: float = 5.0
    _stopped: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            await self.ingest_service.sync_registered_services()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


class StudioEventStream:
    def __init__(self, *, event_store: RedisStudioEventStore, keepalive_interval_seconds: float | None = 15.0) -> None:
        self._event_store = event_store
        self._keepalive_interval_seconds = keepalive_interval_seconds

    async def stream_service(self, service_id: str) -> AsyncIterator[bytes]:
        yield b"event: ready\ndata: {}\n\n"
        async for chunk in self._stream(self._event_store.service_channel(service_id)):
            yield chunk

    async def stream_task(self, service_id: str, task_id: str) -> AsyncIterator[bytes]:
        yield b"event: ready\ndata: {}\n\n"
        async for chunk in self._stream(self._event_store.task_channel(service_id, task_id)):
            yield chunk

    async def _stream(self, channel: str) -> AsyncIterator[bytes]:
        pubsub = self._event_store.redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            iterator = pubsub.listen().__aiter__()
            while True:
                message = await self._next_message(pubsub, iterator)
                if message is None:
                    yield b": keepalive\n\n"
                    continue
                if message.get("type") != "message":
                    continue
                data_raw = message.get("data")
                if not data_raw:
                    continue
                try:
                    payload = StudioControlPlaneEvent.model_validate_json(data_raw)
                except ValidationError:
                    continue
                yield _sse_event(payload)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def _next_message(self, pubsub: Any, iterator: AsyncIterator[dict[str, Any]]) -> dict[str, Any] | None:
        if self._keepalive_interval_seconds is None:
            return await anext(iterator)

        get_message = getattr(pubsub, "get_message", None)
        if callable(get_message):
            try:
                return await get_message(timeout=self._keepalive_interval_seconds)
            except AttributeError:
                pass

        try:
            return await asyncio.wait_for(anext(iterator), timeout=self._keepalive_interval_seconds)
        except TimeoutError:
            return None


def create_studio_events_router(
    *,
    ingest_service: StudioEventIngestService,
    event_stream: StudioEventStream,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    @router.post(f"{prefix}/ingest/events", response_model=StudioEventIngestResponse)
    async def ingest_events(request: StudioEventIngestRequest) -> StudioEventIngestResponse:
        return await ingest_service.ingest_request(request)

    @router.get(f"{prefix}/services/{{service_id}}/events", response_model=StudioEventListResponse)
    async def service_events(
        service_id: str,
        task_id: Annotated[str | None, Query()] = None,
        source_kind: Annotated[ServiceEventSourceKind | None, Query()] = None,
        event_type: Annotated[str | None, Query()] = None,
        before: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> StudioEventListResponse:
        try:
            return await ingest_service.list_service_events(
                service_id,
                task_id=task_id,
                source_kind=source_kind,
                event_type=event_type,
                before=before,
                limit=limit,
            )
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/events", response_model=StudioEventListResponse)
    async def task_events(
        service_id: str,
        task_id: str,
        before: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> StudioEventListResponse:
        try:
            return await ingest_service.list_task_events(service_id, task_id, before=before, limit=limit)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(f"{prefix}/services/{{service_id}}/events/stream")
    async def service_events_stream(service_id: str) -> StreamingResponse:
        try:
            await ingest_service.registry_service.get_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return sse_response(event_stream.stream_service(service_id))

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/events/stream")
    async def task_events_stream(service_id: str, task_id: str) -> StreamingResponse:
        try:
            await ingest_service.registry_service.get_service(service_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return sse_response(event_stream.stream_task(service_id, task_id))

    return router


def _event_dedupe_key(service_id: str, event: RelaynaServiceEvent) -> str:
    if event.event_id is not None:
        return f"{service_id}:{event.source_kind}:{event.event_id}"
    payload = json.dumps(
        {
            "service_id": service_id,
            "source_kind": event.source_kind,
            "task_id": event.task_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "payload": event.payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{service_id}:{event.source_kind}:{event.cursor}:{token}"


def _page_events(items: list[StudioControlPlaneEvent], *, before: str | None, limit: int) -> StudioEventListResponse:
    start_index = 0
    if before is not None:
        for index, item in enumerate(items):
            if item.dedupe_key == before:
                start_index = index + 1
                break
    page = items[start_index : start_index + max(1, limit)]
    next_cursor = page[-1].dedupe_key if start_index + len(page) < len(items) and page else None
    return StudioEventListResponse(count=len(page), items=page, next_cursor=next_cursor)


def _supports_events_feed(service: ServiceRecord) -> bool:
    if not service.capabilities:
        return False
    try:
        document = CapabilityDocument.model_validate(service.capabilities)
    except ValidationError:
        return False
    if document.service_metadata.compatibility != "capabilities_v1":
        return False
    return EVENTS_FEED_ROUTE_ID in document.supported_routes


def _sse_event(payload: StudioControlPlaneEvent) -> bytes:
    lines = [f"id: {payload.dedupe_key}", "event: event", f"data: {payload.model_dump_json()}"]
    return ("\n".join(lines) + "\n\n").encode()


__all__ = [
    "RedisStudioEventStore",
    "StudioControlPlaneEvent",
    "StudioEventEnvelope",
    "StudioEventIngestRequest",
    "StudioEventIngestResponse",
    "StudioEventIngestService",
    "StudioEventListResponse",
    "StudioServiceActivitySnapshot",
    "StudioEventStore",
    "StudioEventStream",
    "StudioPullSyncWorker",
    "create_studio_events_router",
]
