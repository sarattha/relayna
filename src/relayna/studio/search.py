from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .registry import ServiceRecord, ServiceRegistryService

if TYPE_CHECKING:
    from .events import StudioControlPlaneEvent, StudioEventStore

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def _tokenize(value: str | None) -> list[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return []
    return [match.group(0) for match in _TOKEN_RE.finditer(normalized)]


def _build_prefix_tokens(value: str | None) -> set[str]:
    tokens: set[str] = set()
    for token in _tokenize(value):
        for index in range(1, len(token) + 1):
            tokens.add(token[:index])
    return tokens


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _later_iso(left: str | None, right: str | None) -> str | None:
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None:
        return _isoformat(right_dt)
    if right_dt is None:
        return _isoformat(left_dt)
    return _isoformat(max(left_dt, right_dt))


def _earlier_iso(left: str | None, right: str | None) -> str | None:
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None:
        return _isoformat(right_dt)
    if right_dt is None:
        return _isoformat(left_dt)
    return _isoformat(min(left_dt, right_dt))


def _encode_cursor(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> dict[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid cursor.") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid cursor.")
    normalized: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            normalized[key] = value
    return normalized


class StudioTaskSearchDocument(BaseModel):
    service_id: str
    service_name: str
    environment: str
    task_id: str
    correlation_id: str | None = None
    status: str | None = None
    stage: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    latest_event_type: str | None = None
    latest_event_at: str | None = None
    latest_ingested_at: str | None = None
    detail_path: str
    expires_at: str | None = None

    @property
    def document_id(self) -> str:
        return _task_document_id(self.service_id, self.task_id)


class StudioTaskSearchResponse(BaseModel):
    count: int
    items: list[StudioTaskSearchDocument] = Field(default_factory=list)
    next_cursor: str | None = None


class StudioTaskSearchQuery(BaseModel):
    service_id: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    status: str | None = None
    stage: str | None = None
    from_timestamp: str | None = None
    to_timestamp: str | None = None
    limit: int = 50
    cursor: str | None = None


class StudioServiceSearchDocument(BaseModel):
    service_id: str
    name: str
    environment: str
    tags: list[str] = Field(default_factory=list)
    status: str
    health_status: str | None = None
    base_url: str
    auth_mode: str
    last_seen_at: str | None = None


class StudioServiceSearchItem(StudioServiceSearchDocument):
    matched_fields: list[str] = Field(default_factory=list)


class StudioServiceSearchResponse(BaseModel):
    count: int
    items: list[StudioServiceSearchItem] = Field(default_factory=list)
    next_cursor: str | None = None


class StudioServiceSearchQuery(BaseModel):
    query: str | None = None
    environment: str | None = None
    status: str | None = None
    health: str | None = None
    tag: str | None = None
    limit: int = 50
    cursor: str | None = None


class StudioSearchIndexer(Protocol):
    async def upsert_service_document(self, service: ServiceRecord, *, health_status: str | None = None) -> None: ...

    async def delete_service_document(self, service_id: str) -> None: ...

    async def delete_task_documents_for_service(self, service_id: str) -> None: ...

    async def upsert_task_document(self, service: ServiceRecord, event: StudioControlPlaneEvent) -> None: ...


class StudioSearchStore(Protocol):
    async def task_index_is_empty(self) -> bool: ...

    async def get_task_document(self, document_id: str) -> StudioTaskSearchDocument | None: ...

    async def set_task_document(self, document: StudioTaskSearchDocument) -> None: ...

    async def delete_task_document(self, document_id: str) -> None: ...

    async def list_task_document_ids(self) -> set[str]: ...

    async def list_task_document_ids_for_service(self, service_id: str) -> set[str]: ...

    async def list_task_document_ids_for_filter(self, field: str, value: str) -> set[str]: ...

    async def get_service_document(self, service_id: str) -> StudioServiceSearchDocument | None: ...

    async def set_service_document(self, document: StudioServiceSearchDocument) -> None: ...

    async def delete_service_document(self, service_id: str) -> None: ...

    async def list_service_document_ids(self) -> set[str]: ...

    async def list_service_document_ids_for_filter(self, field: str, value: str) -> set[str]: ...

    async def list_service_document_ids_for_token(self, token: str) -> set[str]: ...


class RedisStudioSearchStore:
    def __init__(self, redis: Any, *, prefix: str = "studio:search") -> None:
        self._redis = redis
        self._prefix = prefix

    async def task_index_is_empty(self) -> bool:
        return not bool(await self.list_task_document_ids())

    async def get_task_document(self, document_id: str) -> StudioTaskSearchDocument | None:
        payload = await cast(Awaitable[str | bytes | None], self._redis.get(self._task_doc_key(document_id)))
        if payload is None:
            return None
        return StudioTaskSearchDocument.model_validate_json(payload)

    async def set_task_document(self, document: StudioTaskSearchDocument) -> None:
        document_id = document.document_id
        previous = await self.get_task_document(document_id)
        if previous is not None:
            await self._remove_task_indexes(previous)
        await self._redis.set(self._task_doc_key(document_id), document.model_dump_json())
        await self._redis.sadd(self._task_all_key(), document_id)
        await self._redis.sadd(self._task_service_members_key(document.service_id), document_id)
        await self._redis.sadd(self._task_filter_key("service_id", document.service_id), document_id)
        if document.correlation_id:
            await self._redis.sadd(self._task_filter_key("correlation_id", document.correlation_id), document_id)
        if document.status:
            await self._redis.sadd(self._task_filter_key("status", document.status), document_id)
        if document.stage:
            await self._redis.sadd(self._task_filter_key("stage", document.stage), document_id)
        await self._redis.sadd(self._task_filter_key("task_id", document.task_id), document_id)

    async def delete_task_document(self, document_id: str) -> None:
        existing = await self.get_task_document(document_id)
        if existing is not None:
            await self._remove_task_indexes(existing)
        await self._redis.delete(self._task_doc_key(document_id))
        await self._redis.srem(self._task_all_key(), document_id)

    async def list_task_document_ids(self) -> set[str]:
        return _decode_members(await self._redis.smembers(self._task_all_key()))

    async def list_task_document_ids_for_service(self, service_id: str) -> set[str]:
        return _decode_members(await self._redis.smembers(self._task_service_members_key(service_id)))

    async def list_task_document_ids_for_filter(self, field: str, value: str) -> set[str]:
        return _decode_members(await self._redis.smembers(self._task_filter_key(field, value)))

    async def get_service_document(self, service_id: str) -> StudioServiceSearchDocument | None:
        payload = await cast(Awaitable[str | bytes | None], self._redis.get(self._service_doc_key(service_id)))
        if payload is None:
            return None
        return StudioServiceSearchDocument.model_validate_json(payload)

    async def set_service_document(self, document: StudioServiceSearchDocument) -> None:
        previous = await self.get_service_document(document.service_id)
        if previous is not None:
            await self._remove_service_indexes(previous)
        await self._redis.set(self._service_doc_key(document.service_id), document.model_dump_json())
        await self._redis.sadd(self._service_all_key(), document.service_id)
        await self._redis.sadd(self._service_filter_key("environment", document.environment), document.service_id)
        await self._redis.sadd(self._service_filter_key("status", document.status), document.service_id)
        if document.health_status:
            await self._redis.sadd(self._service_filter_key("health", document.health_status), document.service_id)
        for tag in document.tags:
            await self._redis.sadd(self._service_filter_key("tag", tag), document.service_id)
        for token in _service_tokens(document):
            await self._redis.sadd(self._service_token_key(token), document.service_id)

    async def delete_service_document(self, service_id: str) -> None:
        existing = await self.get_service_document(service_id)
        if existing is not None:
            await self._remove_service_indexes(existing)
        await self._redis.delete(self._service_doc_key(service_id))
        await self._redis.srem(self._service_all_key(), service_id)

    async def list_service_document_ids(self) -> set[str]:
        return _decode_members(await self._redis.smembers(self._service_all_key()))

    async def list_service_document_ids_for_filter(self, field: str, value: str) -> set[str]:
        return _decode_members(await self._redis.smembers(self._service_filter_key(field, value)))

    async def list_service_document_ids_for_token(self, token: str) -> set[str]:
        return _decode_members(await self._redis.smembers(self._service_token_key(token)))

    async def _remove_task_indexes(self, document: StudioTaskSearchDocument) -> None:
        document_id = document.document_id
        await self._redis.srem(self._task_service_members_key(document.service_id), document_id)
        await self._redis.srem(self._task_filter_key("service_id", document.service_id), document_id)
        await self._redis.srem(self._task_filter_key("task_id", document.task_id), document_id)
        if document.correlation_id:
            await self._redis.srem(self._task_filter_key("correlation_id", document.correlation_id), document_id)
        if document.status:
            await self._redis.srem(self._task_filter_key("status", document.status), document_id)
        if document.stage:
            await self._redis.srem(self._task_filter_key("stage", document.stage), document_id)

    async def _remove_service_indexes(self, document: StudioServiceSearchDocument) -> None:
        await self._redis.srem(self._service_filter_key("environment", document.environment), document.service_id)
        await self._redis.srem(self._service_filter_key("status", document.status), document.service_id)
        if document.health_status:
            await self._redis.srem(self._service_filter_key("health", document.health_status), document.service_id)
        for tag in document.tags:
            await self._redis.srem(self._service_filter_key("tag", tag), document.service_id)
        for token in _service_tokens(document):
            await self._redis.srem(self._service_token_key(token), document.service_id)

    def _task_doc_key(self, document_id: str) -> str:
        return f"{self._prefix}:task:doc:{document_id}"

    def _task_all_key(self) -> str:
        return f"{self._prefix}:task:all"

    def _task_filter_key(self, field: str, value: str) -> str:
        return f"{self._prefix}:task:filter:{field}:{value}"

    def _task_service_members_key(self, service_id: str) -> str:
        return f"{self._prefix}:task:service:{service_id}"

    def _service_doc_key(self, service_id: str) -> str:
        return f"{self._prefix}:service:doc:{service_id}"

    def _service_all_key(self) -> str:
        return f"{self._prefix}:service:all"

    def _service_filter_key(self, field: str, value: str) -> str:
        return f"{self._prefix}:service:filter:{field}:{value}"

    def _service_token_key(self, token: str) -> str:
        return f"{self._prefix}:service:token:{token}"


@dataclass(slots=True)
class StudioSearchService(StudioSearchIndexer):
    registry_service: ServiceRegistryService
    event_store: StudioEventStore
    store: StudioSearchStore
    task_index_ttl_seconds: int = 86400
    backfill_event_limit: int = 5000

    async def initialize(self) -> None:
        if not await self.store.task_index_is_empty():
            return
        services = await self.registry_service.list_services()
        for service in services:
            history = await self.event_store.list_service_events(service.service_id, limit=self.backfill_event_limit)
            items = sorted(
                history.items,
                key=lambda item: (
                    _parse_datetime(item.timestamp) or datetime.min.replace(tzinfo=UTC),
                    item.ingested_at,
                ),
            )
            for event in items:
                await self.upsert_task_document(service, event)
            await self.upsert_service_document(service)

    async def prune_expired_task_documents(self) -> int:
        removed = 0
        for document_id in await self.store.list_task_document_ids():
            document = await self.store.get_task_document(document_id)
            if document is None or _document_expired(document):
                await self.store.delete_task_document(document_id)
                removed += 1
        return removed

    async def upsert_service_document(self, service: ServiceRecord, *, health_status: str | None = None) -> None:
        existing = await self.store.get_service_document(service.service_id)
        document = StudioServiceSearchDocument(
            service_id=service.service_id,
            name=service.name,
            environment=service.environment,
            tags=list(service.tags),
            status=service.status.value if hasattr(service.status, "value") else str(service.status),
            health_status=health_status
            if health_status is not None
            else (existing.health_status if existing else None),
            base_url=service.base_url,
            auth_mode=service.auth_mode,
            last_seen_at=_isoformat(service.last_seen_at),
        )
        await self.store.set_service_document(document)
        await self._refresh_task_service_metadata(service)

    async def delete_service_document(self, service_id: str) -> None:
        await self.store.delete_service_document(service_id)

    async def delete_task_documents_for_service(self, service_id: str) -> None:
        for document_id in await self.store.list_task_document_ids_for_service(service_id):
            await self.store.delete_task_document(document_id)

    async def upsert_task_document(self, service: ServiceRecord, event: StudioControlPlaneEvent) -> None:
        document_id = _task_document_id(service.service_id, event.task_id)
        existing = await self.store.get_task_document(document_id)
        current = existing or StudioTaskSearchDocument(
            service_id=service.service_id,
            service_name=service.name,
            environment=service.environment,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            status=_normalize_optional_string(event.payload.get("status")),
            stage=_normalize_optional_string(event.payload.get("stage")),
            first_seen_at=event.timestamp or event.ingested_at,
            last_seen_at=event.timestamp or event.ingested_at,
            latest_event_type=event.event_type,
            latest_event_at=event.timestamp,
            latest_ingested_at=event.ingested_at,
            detail_path=f"/studio/tasks/{service.service_id}/{event.task_id}",
        )
        status_value = _normalize_optional_string(event.payload.get("status"))
        stage_value = _normalize_optional_string(event.payload.get("stage"))
        next_timestamp = event.timestamp or event.ingested_at
        first_seen_at = _earlier_iso(current.first_seen_at, next_timestamp)
        last_seen_at = _later_iso(current.last_seen_at, next_timestamp)
        latest_event_at = current.latest_event_at
        latest_event_type = current.latest_event_type
        if _is_not_earlier(next_timestamp, current.latest_event_at):
            latest_event_at = _isoformat(_parse_datetime(next_timestamp))
            latest_event_type = event.event_type
        document = current.model_copy(
            update={
                "service_name": service.name,
                "environment": service.environment,
                "correlation_id": event.correlation_id or current.correlation_id,
                "status": status_value if status_value is not None else current.status,
                "stage": stage_value if stage_value is not None else current.stage,
                "first_seen_at": first_seen_at,
                "last_seen_at": last_seen_at,
                "latest_event_type": latest_event_type,
                "latest_event_at": latest_event_at,
                "latest_ingested_at": _later_iso(current.latest_ingested_at, event.ingested_at),
                "detail_path": f"/studio/tasks/{service.service_id}/{event.task_id}",
                "expires_at": _isoformat(_utcnow() + timedelta(seconds=max(60, self.task_index_ttl_seconds))),
            }
        )
        await self.store.set_task_document(document)

    async def search_tasks(self, query: StudioTaskSearchQuery) -> StudioTaskSearchResponse:
        if not any(
            [
                query.service_id,
                query.task_id,
                query.correlation_id,
                query.status,
                query.stage,
                query.from_timestamp,
                query.to_timestamp,
            ]
        ):
            raise ValueError("Provide at least one task search filter or a time range.")

        filter_sets: list[set[str]] = []
        for field_name, value in (
            ("service_id", query.service_id),
            ("task_id", query.task_id),
            ("correlation_id", query.correlation_id),
            ("status", query.status),
            ("stage", query.stage),
        ):
            normalized = _normalize_optional_string(value)
            if normalized is None:
                continue
            filter_sets.append(await self.store.list_task_document_ids_for_filter(field_name, normalized))
        candidate_ids = set.intersection(*filter_sets) if filter_sets else await self.store.list_task_document_ids()

        items = await self._load_task_documents(candidate_ids)
        from_dt = _parse_datetime(query.from_timestamp)
        to_dt = _parse_datetime(query.to_timestamp)
        filtered = [
            item for item in items if _within_range(_parse_datetime(item.last_seen_at), from_dt=from_dt, to_dt=to_dt)
        ]
        filtered.sort(
            key=lambda item: (
                _parse_datetime(item.last_seen_at) or datetime.min.replace(tzinfo=UTC),
                item.service_id,
                item.task_id,
            ),
            reverse=True,
        )
        page, next_cursor = _paginate_task_documents(filtered, limit=query.limit, cursor=query.cursor)
        return StudioTaskSearchResponse(count=len(page), items=page, next_cursor=next_cursor)

    async def search_services(self, query: StudioServiceSearchQuery) -> StudioServiceSearchResponse:
        filter_sets: list[set[str]] = []
        for field_name, value in (
            ("environment", query.environment),
            ("status", query.status),
            ("health", query.health),
            ("tag", query.tag),
        ):
            normalized = _normalize_optional_string(value)
            if normalized is None:
                continue
            filter_sets.append(await self.store.list_service_document_ids_for_filter(field_name, normalized))
        if filter_sets:
            candidate_ids = set.intersection(*filter_sets)
        else:
            candidate_ids = await self.store.list_service_document_ids()

        matched_fields_by_service: dict[str, list[str]] = {}
        query_terms = _tokenize(query.query)
        if query_terms:
            for term in query_terms:
                token_matches = await self.store.list_service_document_ids_for_token(term)
                candidate_ids &= token_matches
            for service_id in list(candidate_ids):
                document = await self.store.get_service_document(service_id)
                if document is None:
                    candidate_ids.discard(service_id)
                    continue
                matched_fields = _matched_service_fields(document, query_terms)
                if not matched_fields:
                    candidate_ids.discard(service_id)
                    continue
                matched_fields_by_service[service_id] = matched_fields

        items: list[StudioServiceSearchItem] = []
        for service_id in candidate_ids:
            document = await self.store.get_service_document(service_id)
            if document is None:
                continue
            items.append(
                StudioServiceSearchItem(
                    **document.model_dump(),
                    matched_fields=matched_fields_by_service.get(service_id, []),
                )
            )
        items.sort(key=lambda item: (item.name.lower(), item.service_id))
        page, next_cursor = _paginate_service_documents(items, limit=query.limit, cursor=query.cursor)
        return StudioServiceSearchResponse(count=len(page), items=page, next_cursor=next_cursor)

    async def _load_task_documents(self, document_ids: Iterable[str]) -> list[StudioTaskSearchDocument]:
        items: list[StudioTaskSearchDocument] = []
        expired_ids: list[str] = []
        for document_id in document_ids:
            document = await self.store.get_task_document(document_id)
            if document is None:
                continue
            if _document_expired(document):
                expired_ids.append(document_id)
                continue
            items.append(document)
        for document_id in expired_ids:
            await self.store.delete_task_document(document_id)
        return items

    async def _refresh_task_service_metadata(self, service: ServiceRecord) -> None:
        for document_id in await self.store.list_task_document_ids_for_service(service.service_id):
            existing = await self.store.get_task_document(document_id)
            if existing is None:
                continue
            await self.store.set_task_document(
                existing.model_copy(
                    update={
                        "service_name": service.name,
                        "environment": service.environment,
                        "detail_path": f"/studio/tasks/{service.service_id}/{existing.task_id}",
                    }
                )
            )


@dataclass(slots=True)
class StudioRetentionWorker:
    search_service: StudioSearchService
    interval_seconds: float = 60.0
    _stopped: asyncio.Event = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            await self.search_service.prune_expired_task_documents()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue


def create_studio_search_router(
    *,
    search_service: StudioSearchService,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/tasks/search", response_model=StudioTaskSearchResponse)
    async def search_tasks(
        service_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        correlation_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        stage: str | None = Query(default=None),
        from_timestamp: str | None = Query(default=None, alias="from"),
        to_timestamp: str | None = Query(default=None, alias="to"),
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
    ) -> StudioTaskSearchResponse:
        try:
            return await search_service.search_tasks(
                StudioTaskSearchQuery(
                    service_id=service_id,
                    task_id=task_id,
                    correlation_id=correlation_id,
                    status=status,
                    stage=stage,
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                    limit=limit,
                    cursor=cursor,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(f"{prefix}/services/search", response_model=StudioServiceSearchResponse)
    async def search_services(
        query: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        status: str | None = Query(default=None),
        health: str | None = Query(default=None),
        tag: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None),
    ) -> StudioServiceSearchResponse:
        try:
            return await search_service.search_services(
                StudioServiceSearchQuery(
                    query=query,
                    environment=environment,
                    status=status,
                    health=health,
                    tag=tag,
                    limit=limit,
                    cursor=cursor,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


def _paginate_task_documents(
    items: list[StudioTaskSearchDocument],
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[StudioTaskSearchDocument], str | None]:
    start_index = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        for index, item in enumerate(items):
            if decoded == {
                "last_seen_at": item.last_seen_at or "",
                "service_id": item.service_id,
                "task_id": item.task_id,
            }:
                start_index = index + 1
                break
    page = items[start_index : start_index + max(1, limit)]
    next_cursor = None
    if start_index + len(page) < len(items) and page:
        last = page[-1]
        next_cursor = _encode_cursor(
            {
                "last_seen_at": last.last_seen_at or "",
                "service_id": last.service_id,
                "task_id": last.task_id,
            }
        )
    return page, next_cursor


def _paginate_service_documents(
    items: list[StudioServiceSearchItem],
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[StudioServiceSearchItem], str | None]:
    start_index = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        for index, item in enumerate(items):
            if decoded == {"name": item.name.lower(), "service_id": item.service_id}:
                start_index = index + 1
                break
    page = items[start_index : start_index + max(1, limit)]
    next_cursor = None
    if start_index + len(page) < len(items) and page:
        last = page[-1]
        next_cursor = _encode_cursor({"name": last.name.lower(), "service_id": last.service_id})
    return page, next_cursor


def _task_document_id(service_id: str, task_id: str) -> str:
    return f"{service_id}:{task_id}"


def _decode_members(values: set[Any]) -> set[str]:
    decoded: set[str] = set()
    for value in values:
        decoded.add(value.decode() if isinstance(value, bytes) else str(value))
    return decoded


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _is_not_earlier(left: str | None, right: str | None) -> bool:
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None:
        return False
    if right_dt is None:
        return True
    return left_dt >= right_dt


def _document_expired(document: StudioTaskSearchDocument) -> bool:
    expires_at = _parse_datetime(document.expires_at)
    return expires_at is not None and expires_at <= _utcnow()


def _within_range(value: datetime | None, *, from_dt: datetime | None, to_dt: datetime | None) -> bool:
    if value is None:
        return from_dt is None and to_dt is None
    if from_dt is not None and value < from_dt:
        return False
    if to_dt is not None and value > to_dt:
        return False
    return True


def _service_tokens(document: StudioServiceSearchDocument) -> set[str]:
    tokens: set[str] = set()
    for value in [document.service_id, document.name, document.environment, *document.tags]:
        tokens.update(_build_prefix_tokens(value))
    return tokens


def _matched_service_fields(document: StudioServiceSearchDocument, query_terms: list[str]) -> list[str]:
    matched: list[str] = []
    field_values = {
        "service_id": [document.service_id],
        "name": [document.name],
        "environment": [document.environment],
        "tags": document.tags,
    }
    for field_name, values in field_values.items():
        field_tokens: set[str] = set()
        for value in values:
            field_tokens.update(_build_prefix_tokens(value))
        if all(term in field_tokens for term in query_terms):
            matched.append(field_name)
    return matched


__all__ = [
    "RedisStudioSearchStore",
    "StudioRetentionWorker",
    "StudioSearchIndexer",
    "StudioSearchService",
    "StudioServiceSearchDocument",
    "StudioServiceSearchItem",
    "StudioServiceSearchQuery",
    "StudioServiceSearchResponse",
    "StudioTaskSearchDocument",
    "StudioTaskSearchQuery",
    "StudioTaskSearchResponse",
    "create_studio_search_router",
]
