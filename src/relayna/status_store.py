from __future__ import annotations

import hashlib
import json
from typing import Any

from redis.asyncio import Redis


class RedisStatusStore:
    """Redis-backed task history + pubsub fanout store."""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "task",
        ttl_seconds: int | None = 86400,
        history_maxlen: int = 50,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.history_maxlen = history_maxlen

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
        pipe.publish(self.channel_name(task_id), payload)
        await pipe.execute()

    async def get_history(self, task_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None:
            limit = self.history_maxlen
        items = await self.redis.lrange(self.history_key(task_id), 0, max(0, limit - 1))  # type: ignore[misc]
        return [json.loads(item) for item in items]

    async def get_latest(self, task_id: str) -> dict[str, Any] | None:
        item = await self.redis.lindex(self.history_key(task_id), 0)  # type: ignore[misc]
        if item is None:
            return None
        return json.loads(item)
