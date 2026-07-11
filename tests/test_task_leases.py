from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from relayna.storage import (
    LeasePolicy,
    LeaseRecoveryAction,
    RedisTaskLeaseStore,
    TaskLease,
    TaskLeaseExpiryScanner,
    task_leases_for_health,
)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("set", args, kwargs))

    def delete(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("delete", args, kwargs))

    def sadd(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("sadd", args, kwargs))

    def srem(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("srem", args, kwargs))

    def zadd(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("zadd", args, kwargs))

    def zrem(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("zrem", args, kwargs))

    async def execute(self) -> list[Any]:
        return [await getattr(self._redis, name)(*args, **kwargs) for name, args, kwargs in self._ops]


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

    async def mget(self, keys: list[str]) -> list[str | None]:
        if not keys:
            raise RuntimeError("MGET requires at least one key")
        return [self.values.get(key) for key in keys]

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

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
async def test_task_lease_expiry_scanner_retries_after_publisher_failure() -> None:
    redis = FakeRedis()
    store = RedisTaskLeaseStore(redis)
    expired = make_lease(
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        recovery_action=LeaseRecoveryAction.PUBLISH_STALE_STATUS,
    )
    attempts: list[str] = []

    async def publish(lease: TaskLease) -> None:
        attempts.append(lease.lease_id)
        if len(attempts) == 1:
            raise RuntimeError("publisher unavailable")

    await store.acquire(expired)
    scanner = TaskLeaseExpiryScanner(store=store, status_publisher=publish)

    assert await scanner.scan_once() == [expired]
    assert attempts == ["task-1"]
    assert redis.sets["relayna:lease:expired_claims"] == set()
    assert redis.sorted_sets["relayna:lease:expiries"] == {"task-1": expired.expires_at.timestamp()}

    assert await scanner.scan_once() == [expired]
    assert attempts == ["task-1", "task-1"]


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


@pytest.mark.asyncio
async def test_task_lease_store_owner_and_expiry_edge_paths() -> None:
    redis = FakeRedis()
    store = RedisTaskLeaseStore(redis)
    lease = make_lease()
    assert lease.expired is False
    assert await store.list_by_owner("missing") == []
    await store.acquire(lease)
    redis.sets["relayna:lease:owner:worker-a"].add("missing")
    assert await store.list_by_owner("worker-a") == [lease]
    assert await store.heartbeat("missing", owner_id="worker-a", expires_at=datetime.now(UTC)) is None
    assert await store.release("missing", owner_id="worker-a") is False

    redis.sorted_sets["relayna:lease:expiries"]["missing"] = 0
    future = make_lease(lease_id="future", task_id="future", expires_at=datetime.now(UTC) + timedelta(seconds=60))
    redis.values["relayna:lease:task:future"] = future.model_dump_json()
    redis.sorted_sets["relayna:lease:expiries"]["future"] = 0
    assert await store.claim_expired(now=datetime.now(UTC)) == []
    assert "missing" not in redis.sorted_sets["relayna:lease:expiries"]
    assert redis.sorted_sets["relayna:lease:expiries"]["future"] == future.expires_at.timestamp()
    assert await store.claim_expired(now=datetime(1970, 1, 1, tzinfo=UTC)) == []

    await store._release_expired_claim("missing", retry=False)
    await store._release_expired_claim("future", retry=True)
    assert redis.sorted_sets["relayna:lease:expiries"]["future"] == future.expires_at.timestamp()


@pytest.mark.asyncio
async def test_task_lease_scanner_no_publisher_non_actionable_run_loop_and_health() -> None:
    expired = make_lease(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    class Store:
        def __init__(self) -> None:
            self.calls = 0

        async def claim_expired(self, *, limit: int) -> list[TaskLease]:
            self.calls += 1
            return [expired] if self.calls == 1 else []

    store = Store()
    scanner = TaskLeaseExpiryScanner(store=store, interval_seconds=0.001, batch_size=1)  # type: ignore[arg-type]
    assert await scanner.scan_once() == [expired]

    published: list[str] = []

    async def publish(lease: TaskLease) -> None:
        published.append(lease.lease_id)

    scanner = TaskLeaseExpiryScanner(store=Store(), status_publisher=publish, interval_seconds=0.001)  # type: ignore[arg-type]
    assert await scanner.scan_once() == [expired]
    assert published == []
    run_task = asyncio.create_task(scanner.run_forever())
    await asyncio.sleep(0.005)
    scanner.stop()
    await asyncio.wait_for(run_task, timeout=1)

    health = task_leases_for_health([expired])[0]
    assert health["lease_id"] == expired.lease_id
    assert health["expired"] is True
    assert health["recovery_action"] == "observe_only"


@pytest.mark.asyncio
async def test_task_lease_scanner_publisher_failure_without_private_release_hook() -> None:
    expired = make_lease(
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        recovery_action=LeaseRecoveryAction.RETRY,
    )

    class Store:
        async def claim_expired(self, *, limit: int) -> list[TaskLease]:
            return [expired]

    async def fail(_lease: TaskLease) -> None:
        raise RuntimeError("publisher failed")

    scanner = TaskLeaseExpiryScanner(store=Store(), status_publisher=fail)  # type: ignore[arg-type]
    assert await scanner.scan_once() == [expired]


@pytest.mark.asyncio
async def test_task_lease_expiry_claim_clears_marker_when_payload_is_missing() -> None:
    redis = FakeRedis()
    store = RedisTaskLeaseStore(redis)
    await redis.zadd("relayna:lease:expiries", {"missing-task": datetime.now(UTC).timestamp() - 1})

    assert await store.claim_expired() == []
    assert redis.sorted_sets["relayna:lease:expiries"] == {}
    assert redis.sets["relayna:lease:expired_claims"] == set()


def test_lease_policy_defaults_to_disabled_observe_only() -> None:
    policy = LeasePolicy()

    assert policy.enabled is False
    assert policy.recovery_action is LeaseRecoveryAction.OBSERVE_ONLY
