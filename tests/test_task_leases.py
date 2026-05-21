from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from relayna.storage import LeasePolicy, LeaseRecoveryAction, RedisTaskLeaseStore, TaskLease, TaskLeaseExpiryScanner


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)

    async def sadd(self, key: str, *values: str) -> int:
        items = self.sets.setdefault(key, set())
        before = len(items)
        items.update(values)
        return len(items) - before

    async def srem(self, key: str, *values: str) -> int:
        items = self.sets.setdefault(key, set())
        before = len(items)
        for value in values:
            items.discard(value)
        return before - len(items)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        items = self.sorted_sets.setdefault(key, {})
        before = len(items)
        items.update(mapping)
        return len(items) - before

    async def zrem(self, key: str, *values: str) -> int:
        items = self.sorted_sets.setdefault(key, {})
        before = len(items)
        for value in values:
            items.pop(value, None)
        return before - len(items)

    async def zrangebyscore(
        self,
        key: str,
        min: float | str,
        max: float,
        *,
        start: int = 0,
        num: int | None = None,
    ) -> list[str]:
        lower = float("-inf") if min == "-inf" else float(min)
        values = [
            value
            for value, score in sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1])
            if lower <= score <= max
        ]
        return values[start:] if num is None else values[start : start + num]


def make_lease(**overrides: Any) -> TaskLease:
    now = datetime.now(UTC)
    payload = {
        "lease_id": "task-1",
        "task_id": "task-1",
        "owner_id": "worker-a",
        "consumer_name": "worker-a",
        "acquired_at": now,
        "heartbeat_at": now,
        "expires_at": now + timedelta(seconds=60),
    }
    payload.update(overrides)
    return TaskLease.model_validate(payload)


@pytest.mark.asyncio
async def test_redis_task_lease_store_acquire_heartbeat_and_release() -> None:
    store = RedisTaskLeaseStore(FakeRedis())
    lease = make_lease()

    assert await store.acquire(lease) is True
    assert await store.acquire(lease) is False

    refreshed = await store.heartbeat(
        lease.lease_id,
        owner_id=lease.owner_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    assert refreshed is not None
    assert refreshed.owner_id == lease.owner_id
    assert refreshed.expires_at > lease.expires_at

    assert await store.heartbeat(lease.lease_id, owner_id="worker-b", expires_at=datetime.now(UTC)) is None
    assert await store.release(lease.lease_id, owner_id="worker-b") is False
    assert await store.release(lease.lease_id, owner_id=lease.owner_id) is True
    assert await store.get(lease.lease_id) is None


@pytest.mark.asyncio
async def test_task_lease_expiry_scanner_claims_once_and_publishes_status() -> None:
    redis = FakeRedis()
    store = RedisTaskLeaseStore(redis)
    expired = make_lease(
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        recovery_action=LeaseRecoveryAction.PUBLISH_STALE_STATUS,
    )
    published: list[TaskLease] = []

    async def publish(lease: TaskLease) -> None:
        published.append(lease)

    await store.acquire(expired)
    scanner = TaskLeaseExpiryScanner(store=store, status_publisher=publish)

    assert await scanner.scan_once() == [expired]
    assert published == [expired]
    assert await scanner.scan_once() == []


@pytest.mark.asyncio
async def test_task_lease_expiry_claim_allows_later_reacquire_of_same_lease_id() -> None:
    redis = FakeRedis()
    store = RedisTaskLeaseStore(redis)
    expired = make_lease(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    await store.acquire(expired)
    assert await store.claim_expired() == [expired]
    redis.values.pop("relayna:lease:task:task-1")

    replacement = make_lease(owner_id="worker-b")
    assert await store.acquire(replacement) is True
    assert await store.claim_expired(now=datetime.now(UTC) + timedelta(seconds=120)) == [replacement]


def test_lease_policy_defaults_to_disabled_observe_only() -> None:
    policy = LeasePolicy()

    assert policy.enabled is False
    assert policy.recovery_action is LeaseRecoveryAction.OBSERVE_ONLY
