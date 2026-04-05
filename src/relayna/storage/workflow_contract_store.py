from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from redis.asyncio import Redis


class WorkflowContractStore(Protocol):
    async def acquire_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> bool: ...

    async def release_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None: ...

    async def mark_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None: ...

    async def clear_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None: ...


class RedisWorkflowContractStore:
    def __init__(self, redis: Redis, *, prefix: str = "relayna", ttl_seconds: int | None = 86400) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    async def acquire_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> bool:
        key = self._dedup_key(
            stage=stage,
            task_id=task_id,
            action=action,
            payload=payload,
            dedup_key_fields=dedup_key_fields,
        )
        stored = await self.redis.set(key, "1", ex=self.ttl_seconds, nx=True)
        return bool(stored)

    async def release_dedup(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None:
        await self.redis.delete(
            self._dedup_key(
                stage=stage,
                task_id=task_id,
                action=action,
                payload=payload,
                dedup_key_fields=dedup_key_fields,
            )
        )

    async def mark_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None:
        signature = build_dedup_signature(
            task_id=task_id,
            action=action,
            payload=payload,
            dedup_key_fields=dedup_key_fields,
        )
        key = self._inflight_key(stage=stage, task_id=task_id)
        value = json.dumps(
            {
                "task_id": task_id,
                "stage": stage,
                "action": action,
                "dedup_key_fields": list(dedup_key_fields),
                "payload": {field: payload.get(field) for field in dedup_key_fields},
            },
            sort_keys=True,
        )
        await self.redis.hset(key, signature, value)
        if self.ttl_seconds is not None:
            await self.redis.expire(key, self.ttl_seconds)

    async def clear_inflight(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> None:
        key = self._inflight_key(stage=stage, task_id=task_id)
        signature = build_dedup_signature(
            task_id=task_id,
            action=action,
            payload=payload,
            dedup_key_fields=dedup_key_fields,
        )
        await self.redis.hdel(key, signature)

    def _dedup_key(
        self,
        *,
        stage: str,
        task_id: str,
        action: str | None,
        payload: Mapping[str, Any],
        dedup_key_fields: Sequence[str],
    ) -> str:
        signature = build_dedup_signature(
            task_id=task_id,
            action=action,
            payload=payload,
            dedup_key_fields=dedup_key_fields,
        )
        return f"{self.prefix}:workflow:contract:{stage}:dedup:{task_id}:{signature}"

    def _inflight_key(self, *, stage: str, task_id: str) -> str:
        return f"{self.prefix}:workflow:contract:{stage}:inflight:{task_id}"


def build_dedup_signature(
    *, task_id: str, action: str | None, payload: Mapping[str, Any], dedup_key_fields: Sequence[str]
) -> str:
    material = {
        "task_id": task_id,
        "action": action,
        "fields": {field: payload.get(field) for field in dedup_key_fields},
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["RedisWorkflowContractStore", "WorkflowContractStore", "build_dedup_signature"]
