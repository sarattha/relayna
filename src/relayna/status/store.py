from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable
from typing import Any, cast

from redis.asyncio import Redis

from ..observability.feed import RedisServiceEventFeedStore


class RedisStatusStore:
    """Redis-backed task history + pubsub fanout store."""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "task",
        ttl_seconds: int | None = 86400,
        history_maxlen: int = 50,
        service_event_store: RedisServiceEventFeedStore | None = None,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen
        self.service_event_store = service_event_store

    def history_key(self, task_id: str) -> str:
        return f"{self.prefix}:history:{task_id}"

    def channel_name(self, task_id: str) -> str:
        return f"{self.prefix}:channel:{task_id}"

    def event_key(self, task_id: str, event: dict[str, Any]) -> str:
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            token = event_id.strip()
        else:
            canonical = json.dumps(event, ensure_ascii=False, sort_keys=True)
            token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self.prefix}:event:{task_id}:{token}"

    def child_tasks_key(self, parent_task_id: str) -> str:
        return f"{self.prefix}:children:{parent_task_id}"

    async def set_history(self, task_id: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        dedupe_key = self.event_key(task_id, event)
        set_kwargs: dict[str, Any] = {"nx": True}
        if self.ttl_seconds:
            set_kwargs["ex"] = self.ttl_seconds
        inserted = await self.redis.set(dedupe_key, "1", **set_kwargs)
        if not inserted:
            return
        pipe = self.redis.pipeline()
        history_key = self.history_key(task_id)
        pipe.lpush(history_key, payload)
        pipe.ltrim(history_key, 0, self.history_maxlen - 1)
        if self.ttl_seconds:
            pipe.expire(history_key, self.ttl_seconds)
        parent_task_id = self._parent_task_id(event)
        if parent_task_id is not None:
            child_tasks_key = self.child_tasks_key(parent_task_id)
            pipe.sadd(child_tasks_key, task_id)
            if self.ttl_seconds:
                pipe.expire(child_tasks_key, self.ttl_seconds)
        pipe.publish(self.channel_name(task_id), payload)
        await pipe.execute()
        if self.service_event_store is not None:
            await self.service_event_store.add_status_event(event)

    async def get_history(self, task_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None:
            limit = self.history_maxlen
        items = await cast(
            Awaitable[list[str | bytes]],
            self.redis.lrange(self.history_key(task_id), 0, max(0, limit - 1)),
        )
        return [json.loads(item) for item in items]

    async def get_latest(self, task_id: str) -> dict[str, Any] | None:
        item = await cast(Awaitable[str | bytes | None], self.redis.lindex(self.history_key(task_id), 0))
        if item is None:
            return None
        return json.loads(item)

    async def get_child_task_ids(self, parent_task_id: str, limit: int | None = None) -> list[str]:
        items = await cast(Awaitable[set[str | bytes]], self.redis.smembers(self.child_tasks_key(parent_task_id)))
        child_task_ids = sorted(str(item, "utf-8") if isinstance(item, bytes) else str(item) for item in items)
        if limit is None:
            return child_task_ids
        return child_task_ids[: max(0, limit)]

    def _parent_task_id(self, event: dict[str, Any]) -> str | None:
        meta = event.get("meta")
        if not isinstance(meta, dict):
            return None
        parent_task_id = str(meta.get("parent_task_id") or "").strip()
        if not parent_task_id or parent_task_id == str(event.get("task_id") or "").strip():
            return None
        return parent_task_id


__all__ = ["RedisStatusStore"]
