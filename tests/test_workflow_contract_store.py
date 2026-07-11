from __future__ import annotations

import json

import pytest

from relayna.storage.workflow_contract_store import RedisWorkflowContractStore, build_dedup_signature


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None, nx: bool) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key: str, field: str) -> int:
        return int(self.hashes.setdefault(key, {}).pop(field, None) is not None)

    async def expire(self, key: str, ttl: int) -> bool:
        self.expirations[key] = ttl
        return True


@pytest.mark.asyncio
async def test_workflow_contract_store_dedup_and_inflight_lifecycle() -> None:
    redis = Redis()
    store = RedisWorkflowContractStore(redis, prefix="test", ttl_seconds=60)  # type: ignore[arg-type]
    kwargs = {
        "stage": "planner",
        "task_id": "task-1",
        "action": "plan",
        "payload": {"request_id": "request-1", "ignored": True},
        "dedup_key_fields": ("request_id",),
    }
    signature = build_dedup_signature(
        task_id="task-1",
        action="plan",
        payload=kwargs["payload"],
        dedup_key_fields=("request_id",),
    )
    assert len(signature) == 64
    assert await store.acquire_dedup(**kwargs) is True  # type: ignore[arg-type]
    assert await store.acquire_dedup(**kwargs) is False  # type: ignore[arg-type]
    await store.release_dedup(**kwargs)  # type: ignore[arg-type]
    assert await store.acquire_dedup(**kwargs) is True  # type: ignore[arg-type]

    await store.mark_inflight(**kwargs)  # type: ignore[arg-type]
    key = "test:workflow:contract:planner:inflight:task-1"
    payload = json.loads(redis.hashes[key][signature])
    assert payload == {
        "action": "plan",
        "dedup_key_fields": ["request_id"],
        "payload": {"request_id": "request-1"},
        "stage": "planner",
        "task_id": "task-1",
    }
    assert redis.expirations[key] == 60
    await store.clear_inflight(**kwargs)  # type: ignore[arg-type]
    assert redis.hashes[key] == {}


@pytest.mark.asyncio
async def test_workflow_contract_store_supports_no_ttl_and_non_json_signature_values() -> None:
    redis = Redis()
    store = RedisWorkflowContractStore(redis, ttl_seconds=None)  # type: ignore[arg-type]
    payload = {"value": object()}
    first = build_dedup_signature(task_id="task", action=None, payload=payload, dedup_key_fields=("value",))
    second = build_dedup_signature(task_id="task", action=None, payload=payload, dedup_key_fields=("value",))
    assert first == second
    await store.mark_inflight(
        stage="stage",
        task_id="task",
        action=None,
        payload={"value": "serializable"},
        dedup_key_fields=("value",),
    )
    assert redis.expirations == {}
