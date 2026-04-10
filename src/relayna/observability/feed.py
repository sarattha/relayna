from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import httpx
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from .exporters import event_to_dict


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _normalize_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ServiceEventSourceKind(StrEnum):
    STATUS = "status"
    OBSERVATION = "observation"


class StudioEventIngestMethod(StrEnum):
    PUSH = "push"
    PULL = "pull"


class RelaynaServiceEvent(BaseModel):
    cursor: str
    task_id: str
    event_type: str
    source_kind: ServiceEventSourceKind
    component: str | None = None
    timestamp: str | None = None
    event_id: str | None = None
    correlation_id: str | None = None
    parent_task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RelaynaServiceEventFeedResponse(BaseModel):
    count: int
    items: list[RelaynaServiceEvent] = Field(default_factory=list)
    next_cursor: str | None = None


def normalize_status_feed_event(event: dict[str, Any]) -> RelaynaServiceEvent | None:
    task_id = _normalize_string(event.get("task_id"))
    if task_id is None:
        return None
    status = _normalize_string(event.get("status")) or "unknown"
    event_id = _normalize_string(event.get("event_id"))
    correlation_id = _normalize_string(event.get("correlation_id"))
    meta = event.get("meta")
    parent_task_id = None
    if isinstance(meta, dict):
        parent_task_id = _normalize_string(meta.get("parent_task_id"))
    normalized_payload = dict(event)
    cursor_seed = {
        "source_kind": ServiceEventSourceKind.STATUS,
        "task_id": task_id,
        "event_id": event_id,
        "payload": normalized_payload,
    }
    return RelaynaServiceEvent(
        cursor=event_id or _canonical_hash(cursor_seed),
        task_id=task_id,
        event_type=f"status.{status.lower()}",
        source_kind=ServiceEventSourceKind.STATUS,
        component="status",
        timestamp=_normalize_string(event.get("timestamp")),
        event_id=event_id,
        correlation_id=correlation_id,
        parent_task_id=parent_task_id,
        payload=normalized_payload,
    )


def normalize_observation_feed_event(event: object) -> RelaynaServiceEvent | None:
    try:
        payload = event_to_dict(event)
    except Exception:
        return None
    task_id = _normalize_string(payload.get("task_id"))
    if task_id is None:
        return None
    event_id = _normalize_string(payload.get("event_id"))
    cursor_seed = {
        "source_kind": ServiceEventSourceKind.OBSERVATION,
        "event_type": type(event).__name__,
        "payload": payload,
    }
    return RelaynaServiceEvent(
        cursor=event_id or _canonical_hash(cursor_seed),
        task_id=task_id,
        event_type=type(event).__name__,
        source_kind=ServiceEventSourceKind.OBSERVATION,
        component=_normalize_string(payload.get("component")),
        timestamp=_normalize_string(payload.get("timestamp")),
        event_id=event_id,
        correlation_id=_normalize_string(payload.get("correlation_id")),
        parent_task_id=_normalize_string(payload.get("parent_task_id")),
        payload=payload,
    )


class RedisServiceEventFeedStore:
    """Redis-backed merged service event feed for status and observations."""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "relayna-service-events",
        ttl_seconds: int | None = 86400,
        feed_maxlen: int = 5000,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.feed_maxlen = feed_maxlen

    def feed_key(self) -> str:
        return f"{self.prefix}:feed"

    def event_key(self, cursor: str) -> str:
        return f"{self.prefix}:event:{cursor}"

    async def add_status_event(self, event: dict[str, Any]) -> bool:
        normalized = normalize_status_feed_event(event)
        if normalized is None:
            return False
        return await self._store(normalized)

    async def add_observation_event(self, event: object) -> bool:
        normalized = normalize_observation_feed_event(event)
        if normalized is None:
            return False
        return await self._store(normalized)

    async def get_feed(self, *, after: str | None = None, limit: int = 100) -> RelaynaServiceEventFeedResponse:
        items = await cast(
            Awaitable[list[str | bytes]],
            self.redis.lrange(self.feed_key(), 0, max(0, self.feed_maxlen - 1)),
        )
        parsed = [RelaynaServiceEvent.model_validate_json(item) for item in items]
        start_index = 0
        if after:
            for index, item in enumerate(parsed):
                if item.cursor == after:
                    start_index = index + 1
                    break
        page = parsed[start_index : start_index + max(1, limit)]
        next_cursor = page[-1].cursor if start_index + len(page) < len(parsed) and page else None
        return RelaynaServiceEventFeedResponse(count=len(page), items=page, next_cursor=next_cursor)

    async def _store(self, event: RelaynaServiceEvent) -> bool:
        dedupe_key = self.event_key(event.cursor)
        set_kwargs: dict[str, Any] = {"nx": True}
        if self.ttl_seconds:
            set_kwargs["ex"] = self.ttl_seconds
        inserted = await self.redis.set(dedupe_key, "1", **set_kwargs)
        if not inserted:
            return False

        serialized = event.model_dump_json()
        pipe = self.redis.pipeline()
        pipe.lpush(self.feed_key(), serialized)
        pipe.ltrim(self.feed_key(), 0, self.feed_maxlen - 1)
        if self.ttl_seconds:
            pipe.expire(self.feed_key(), self.ttl_seconds)
        await pipe.execute()
        return True


class StudioObservationForwarder:
    """Best-effort observation sink that batches Studio ingest requests."""

    def __init__(
        self,
        *,
        studio_base_url: str,
        service_id: str,
        batch_size: int = 20,
        timeout_seconds: float = 5.0,
        client_factory=None,
    ) -> None:
        self._studio_base_url = studio_base_url.rstrip("/")
        self._service_id = service_id.strip()
        self._batch_size = max(1, batch_size)
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))
        self._pending: list[RelaynaServiceEvent] = []

    async def __call__(self, event: object) -> None:
        normalized = normalize_observation_feed_event(event)
        if normalized is None:
            return
        self._pending.append(normalized)
        if len(self._pending) >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        if not self._pending:
            return
        payload = {
            "events": [
                {
                    "service_id": self._service_id,
                    "ingest_method": StudioEventIngestMethod.PUSH,
                    "event": item.model_dump(mode="json"),
                }
                for item in self._pending
            ]
        }
        try:
            async with self._client_factory(self._timeout_seconds) as client:
                await client.post(f"{self._studio_base_url}/studio/ingest/events", json=payload)
        except Exception:
            return
        self._pending.clear()


def make_studio_observation_forwarder(
    *,
    studio_base_url: str,
    service_id: str,
    batch_size: int = 20,
    timeout_seconds: float = 5.0,
    client_factory=None,
) -> StudioObservationForwarder:
    return StudioObservationForwarder(
        studio_base_url=studio_base_url,
        service_id=service_id,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        client_factory=client_factory,
    )


__all__ = [
    "RedisServiceEventFeedStore",
    "RelaynaServiceEvent",
    "RelaynaServiceEventFeedResponse",
    "ServiceEventSourceKind",
    "StudioEventIngestMethod",
    "StudioObservationForwarder",
    "make_studio_observation_forwarder",
    "normalize_observation_feed_event",
    "normalize_status_feed_event",
]
