from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LeaseRecoveryAction(StrEnum):
    OBSERVE_ONLY = "observe_only"
    PUBLISH_STALE_STATUS = "publish_stale_status"
    REQUEUE = "requeue"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


class LeasePolicy(BaseModel):
    enabled: bool = False
    ttl_seconds: float = Field(default=60.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=20.0, gt=0)
    recovery_action: LeaseRecoveryAction = LeaseRecoveryAction.OBSERVE_ONLY
    stale_status: str = "lease_expired"


class TaskLease(BaseModel):
    lease_id: str
    task_id: str
    owner_id: str
    consumer_name: str
    acquired_at: datetime = Field(default_factory=_utcnow)
    heartbeat_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    message_id: str | None = None
    task_type: str | None = None
    workflow_id: str | None = None
    stage: str | None = None
    attempt: int = 0
    recovery_action: LeaseRecoveryAction = LeaseRecoveryAction.OBSERVE_ONLY

    @property
    def expired(self) -> bool:
        return self.expires_at <= _utcnow()


class TaskLeaseStore(Protocol):
    async def acquire(self, lease: TaskLease) -> bool: ...

    async def heartbeat(self, lease_id: str, *, owner_id: str, expires_at: datetime) -> TaskLease | None: ...

    async def release(self, lease_id: str, *, owner_id: str) -> bool: ...

    async def get(self, lease_id: str) -> TaskLease | None: ...

    async def list_by_owner(self, owner_id: str) -> list[TaskLease]: ...

    async def claim_expired(self, *, now: datetime | None = None, limit: int = 100) -> list[TaskLease]: ...


class RedisTaskLeaseStore:
    def __init__(self, redis: Any, *, prefix: str = "relayna") -> None:
        self._redis = redis
        self._prefix = prefix

    def _lease_key(self, lease_id: str) -> str:
        return f"{self._prefix}:lease:task:{lease_id}"

    def _owner_key(self, owner_id: str) -> str:
        return f"{self._prefix}:lease:owner:{owner_id}"

    @property
    def _expiries_key(self) -> str:
        return f"{self._prefix}:lease:expiries"

    @property
    def _expired_claims_key(self) -> str:
        return f"{self._prefix}:lease:expired_claims"

    async def acquire(self, lease: TaskLease) -> bool:
        payload = lease.model_dump_json()
        ttl_seconds = max(1, int((lease.expires_at - _utcnow()).total_seconds()))
        acquired = await self._redis.set(self._lease_key(lease.lease_id), payload, ex=ttl_seconds, nx=True)
        if not acquired:
            return False
        await self._redis.srem(self._expired_claims_key, lease.lease_id)
        await self._redis.sadd(self._owner_key(lease.owner_id), lease.lease_id)
        await self._redis.zadd(self._expiries_key, {lease.lease_id: lease.expires_at.timestamp()})
        return True

    async def heartbeat(self, lease_id: str, *, owner_id: str, expires_at: datetime) -> TaskLease | None:
        current = await self.get(lease_id)
        if current is None or current.owner_id != owner_id:
            return None
        updated = current.model_copy(update={"heartbeat_at": _utcnow(), "expires_at": expires_at})
        ttl_seconds = max(1, int((expires_at - _utcnow()).total_seconds()))
        await self._redis.set(self._lease_key(lease_id), updated.model_dump_json(), ex=ttl_seconds)
        await self._redis.zadd(self._expiries_key, {lease_id: expires_at.timestamp()})
        return updated

    async def release(self, lease_id: str, *, owner_id: str) -> bool:
        current = await self.get(lease_id)
        if current is None:
            return False
        if current.owner_id != owner_id:
            return False
        await self._redis.delete(self._lease_key(lease_id))
        await self._redis.srem(self._owner_key(owner_id), lease_id)
        await self._redis.zrem(self._expiries_key, lease_id)
        await self._redis.srem(self._expired_claims_key, lease_id)
        return True

    async def get(self, lease_id: str) -> TaskLease | None:
        payload = await cast(Awaitable[str | bytes | None], self._redis.get(self._lease_key(lease_id)))
        if payload is None:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return TaskLease.model_validate_json(payload)

    async def list_by_owner(self, owner_id: str) -> list[TaskLease]:
        lease_ids = await self._redis.smembers(self._owner_key(owner_id))
        leases: list[TaskLease] = []
        for raw_lease_id in lease_ids:
            lease_id = raw_lease_id.decode("utf-8") if isinstance(raw_lease_id, bytes) else str(raw_lease_id)
            lease = await self.get(lease_id)
            if lease is not None:
                leases.append(lease)
        return leases

    async def claim_expired(self, *, now: datetime | None = None, limit: int = 100) -> list[TaskLease]:
        now = now or _utcnow()
        raw_ids = await self._redis.zrangebyscore(self._expiries_key, "-inf", now.timestamp(), start=0, num=limit)
        expired: list[TaskLease] = []
        for raw_lease_id in raw_ids:
            lease_id = raw_lease_id.decode("utf-8") if isinstance(raw_lease_id, bytes) else str(raw_lease_id)
            if not await self._redis.sadd(self._expired_claims_key, lease_id):
                continue
            lease = await self.get(lease_id)
            await self._redis.zrem(self._expiries_key, lease_id)
            if lease is None:
                continue
            if lease.expires_at > now:
                await self._redis.zadd(self._expiries_key, {lease_id: lease.expires_at.timestamp()})
                await self._redis.srem(self._expired_claims_key, lease_id)
                continue
            expired.append(lease)
        return expired


LeaseStatusPublisher = Callable[[TaskLease], Awaitable[None]]


class TaskLeaseExpiryScanner:
    def __init__(
        self,
        *,
        store: TaskLeaseStore,
        status_publisher: LeaseStatusPublisher | None = None,
        interval_seconds: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        self._store = store
        self._status_publisher = status_publisher
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def scan_once(self) -> list[TaskLease]:
        expired = await self._store.claim_expired(limit=self._batch_size)
        if self._status_publisher is None:
            return expired
        for lease in expired:
            if lease.recovery_action in {
                LeaseRecoveryAction.PUBLISH_STALE_STATUS,
                LeaseRecoveryAction.REQUEUE,
                LeaseRecoveryAction.RETRY,
                LeaseRecoveryAction.DEAD_LETTER,
            }:
                await self._status_publisher(lease)
        return expired

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            await self.scan_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue


def task_leases_for_health(leases: Iterable[TaskLease]) -> list[dict[str, Any]]:
    return [
        {
            "lease_id": lease.lease_id,
            "task_id": lease.task_id,
            "message_id": lease.message_id,
            "task_type": lease.task_type,
            "stage": lease.stage,
            "owner_id": lease.owner_id,
            "consumer_name": lease.consumer_name,
            "attempt": lease.attempt,
            "acquired_at": lease.acquired_at,
            "heartbeat_at": lease.heartbeat_at,
            "expires_at": lease.expires_at,
            "expired": lease.expired,
            "recovery_action": lease.recovery_action.value,
        }
        for lease in leases
    ]


__all__ = [
    "LeasePolicy",
    "LeaseRecoveryAction",
    "RedisTaskLeaseStore",
    "TaskLease",
    "TaskLeaseExpiryScanner",
    "TaskLeaseStore",
    "task_leases_for_health",
]
