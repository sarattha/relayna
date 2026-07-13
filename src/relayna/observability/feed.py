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

_STORE_SERVICE_EVENT_SCRIPT = """
local ttl_seconds = tonumber(ARGV[4])
local set_result
if ttl_seconds ~= 0 then
    set_result = redis.call("SET", KEYS[1], "1", "NX", "EX", ttl_seconds)
else
    set_result = redis.call("SET", KEYS[1], "1", "NX")
end
if not set_result then
    return 0
end

local sequence = redis.call("INCR", KEYS[2])
redis.call("ZADD", KEYS[3], sequence, ARGV[2])
redis.call("HSET", KEYS[4], ARGV[2], ARGV[1])

local excess = redis.call("ZCARD", KEYS[3]) - tonumber(ARGV[3])
if excess > 0 then
    local evicted = redis.call("ZRANGE", KEYS[3], 0, excess - 1)
    for _, cursor in ipairs(evicted) do
        redis.call("HDEL", KEYS[4], cursor)
    end
    redis.call("ZREMRANGEBYRANK", KEYS[3], 0, excess - 1)
end

if ttl_seconds ~= 0 then
    redis.call("EXPIRE", KEYS[2], ttl_seconds)
    redis.call("EXPIRE", KEYS[3], ttl_seconds)
    redis.call("EXPIRE", KEYS[4], ttl_seconds)
end
return 1
"""

_READ_SERVICE_EVENT_PAGE_SCRIPT = """
local page_size = tonumber(ARGV[2])
local cursors
if ARGV[1] == "" then
    cursors = redis.call("ZREVRANGE", KEYS[1], 0, page_size)
else
    local score = redis.call("ZSCORE", KEYS[1], ARGV[1])
    if not score then
        cursors = redis.call("ZREVRANGE", KEYS[1], 0, page_size)
    else
        cursors = redis.call("ZREVRANGEBYSCORE", KEYS[1], "(" .. score, "-inf", "LIMIT", 0, page_size + 1)
    end
end

local result = {1}
for _, cursor in ipairs(cursors) do
    local payload = redis.call("HGET", KEYS[2], cursor)
    if not payload then
        return {0, cursor}
    end
    table.insert(result, cursor)
    table.insert(result, payload)
end
return result
"""


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
        self._store_script = None
        self._read_script = None

    def feed_key(self) -> str:
        return f"{self.prefix}:feed:index"

    def feed_payloads_key(self) -> str:
        return f"{self.prefix}:feed:payloads"

    def feed_sequence_key(self) -> str:
        return f"{self.prefix}:feed:sequence"

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
        page_size = max(1, limit)
        read_script = self._read_script
        if read_script is None:
            read_script = self.redis.register_script(_READ_SERVICE_EVENT_PAGE_SCRIPT)
            self._read_script = read_script
        raw_page = await cast(
            Awaitable[list[int | str | bytes]],
            read_script(
                keys=[self.feed_key(), self.feed_payloads_key()],
                args=[after or "", page_size],
            ),
        )
        candidates = self._parse_page(raw_page)
        page = candidates[:page_size]
        next_cursor = page[-1].cursor if len(candidates) > page_size and page else None
        return RelaynaServiceEventFeedResponse(count=len(page), items=page, next_cursor=next_cursor)

    @staticmethod
    def _parse_page(raw_page: list[int | str | bytes]) -> list[RelaynaServiceEvent]:
        if not raw_page:
            raise RuntimeError("Service event feed returned an empty script response.")
        if raw_page[0] != 1:
            cursor = raw_page[1] if len(raw_page) > 1 else "unknown"
            if isinstance(cursor, bytes):
                cursor = cursor.decode()
            raise RuntimeError(f"Service event feed payload is missing for cursor '{cursor}'.")
        values = raw_page[1:]
        if len(values) % 2:
            raise RuntimeError("Service event feed returned an incomplete cursor/payload pair.")
        items: list[RelaynaServiceEvent] = []
        for raw_cursor, payload in zip(values[::2], values[1::2], strict=True):
            if isinstance(raw_cursor, int) or isinstance(payload, int):
                raise RuntimeError("Service event feed returned an invalid cursor/payload value.")
            cursor = raw_cursor.decode() if isinstance(raw_cursor, bytes) else raw_cursor
            item = RelaynaServiceEvent.model_validate_json(payload)
            if item.cursor != cursor:
                raise RuntimeError(f"Service event feed payload cursor mismatch for '{cursor}'.")
            items.append(item)
        return items

    async def _store(self, event: RelaynaServiceEvent) -> bool:
        serialized = event.model_dump_json()
        store_script = self._store_script
        if store_script is None:
            store_script = self.redis.register_script(_STORE_SERVICE_EVENT_SCRIPT)
            self._store_script = store_script
        stored = await cast(
            Awaitable[int],
            store_script(
                keys=[
                    self.event_key(event.cursor),
                    self.feed_sequence_key(),
                    self.feed_key(),
                    self.feed_payloads_key(),
                ],
                args=[serialized, event.cursor, self.feed_maxlen, self.ttl_seconds or 0],
            ),
        )
        return bool(stored)


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
        batch = list(self._pending)
        payload = {
            "events": [
                {
                    "service_id": self._service_id,
                    "ingest_method": StudioEventIngestMethod.PUSH,
                    "event": item.model_dump(mode="json"),
                }
                for item in batch
            ]
        }
        try:
            async with self._client_factory(self._timeout_seconds) as client:
                response = await client.post(f"{self._studio_base_url}/studio/ingest/events", json=payload)
                response.raise_for_status()
        except Exception:
            return
        del self._pending[: len(batch)]


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
