from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable
from datetime import datetime
from typing import Any, cast

from redis.asyncio import Redis

from ..metrics import RelaynaMetrics
from .exporters import event_to_dict
from .feed import RedisServiceEventFeedStore


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class RedisObservationStore:
    """Redis-backed per-task observation history."""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "relayna-observations",
        ttl_seconds: int | None = 86400,
        history_maxlen: int = 500,
        service_event_store: RedisServiceEventFeedStore | None = None,
        metrics: RelaynaMetrics | None = None,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen
        self.service_event_store = service_event_store
        self.metrics = metrics

    def history_key(self, task_id: str) -> str:
        return f"{self.prefix}:history:{task_id}"

    def event_key(self, task_id: str, event: dict[str, Any]) -> str:
        canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, default=_json_default)
        token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self.prefix}:event:{task_id}:{token}"

    async def set_event(self, event: object) -> bool:
        payload = self.normalize_event(event)
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return False

        dedupe_key = self.event_key(task_id, payload)
        set_kwargs: dict[str, Any] = {"nx": True}
        if self.ttl_seconds:
            set_kwargs["ex"] = self.ttl_seconds
        inserted = await self.redis.set(dedupe_key, "1", **set_kwargs)
        if not inserted:
            return False

        serialized = json.dumps(payload, ensure_ascii=False, default=_json_default)
        pipe = self.redis.pipeline()
        history_key = self.history_key(task_id)
        pipe.lpush(history_key, serialized)
        pipe.ltrim(history_key, 0, self.history_maxlen - 1)
        if self.ttl_seconds:
            pipe.expire(history_key, self.ttl_seconds)
        await pipe.execute()
        if self.service_event_store is not None:
            await self.service_event_store.add_observation_event(event)
        if self.metrics is not None:
            self.metrics.record_observation_event(status=str(payload.get("event_type") or "observation"))
        return True

    async def get_history(self, task_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None:
            limit = self.history_maxlen
        items = await cast(
            Awaitable[list[str | bytes]],
            self.redis.lrange(self.history_key(task_id), 0, max(0, limit - 1)),
        )
        return [json.loads(item) for item in items]

    def normalize_event(self, event: object) -> dict[str, Any]:
        try:
            payload = event_to_dict(event)
        except Exception:
            payload = {}
        payload["event_type"] = type(event).__name__
        return payload


def make_redis_observation_sink(store: RedisObservationStore):
    async def _sink(event: object) -> None:
        await store.set_event(event)

    return _sink


__all__ = ["RedisObservationStore", "make_redis_observation_sink"]
