from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from redis.asyncio import Redis

from .models import DLQRecord, DLQRecordState, DLQReplayConflict, FailedTaskInvestigationStatus


class DLQRecorder(Protocol):
    async def add(self, record: DLQRecord) -> None: ...


class DLQStore(Protocol):
    async def add(self, record: DLQRecord) -> None: ...

    async def get(self, dlq_id: str) -> DLQRecord | None: ...

    async def list_records(
        self,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[DLQRecord], str | None]: ...

    async def summarize_queues(self) -> list[tuple[str, int, datetime | None]]: ...

    async def claim_replay(self, dlq_id: str, *, force: bool = False) -> DLQRecord | None: ...

    async def release_replay_claim(self, dlq_id: str) -> None: ...

    async def mark_replayed(
        self,
        dlq_id: str,
        *,
        replayed_at: datetime,
        target_queue_name: str,
    ) -> DLQRecord | None: ...

    async def list_failed_task_records(
        self,
        *,
        service_name: str | None = None,
        queue_name: str | None = None,
        dlq_name: str | None = None,
        error_type: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        investigation_status: FailedTaskInvestigationStatus | str | None = None,
        failed_from: datetime | None = None,
        failed_to: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[DLQRecord], str | None]: ...

    async def update(self, record: DLQRecord) -> DLQRecord: ...

    async def delete(self, dlq_id: str) -> bool: ...

    async def cleanup_failed_task_index(self, *, older_than: datetime | None = None) -> int: ...


class RedisDLQStore:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "relayna",
        ttl_seconds: int | None = None,
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def record_key(self, dlq_id: str) -> str:
        return f"{self.prefix}:dlq:record:{dlq_id}"

    def records_key(self) -> str:
        return f"{self.prefix}:dlq:records"

    def failed_tasks_index_key(self) -> str:
        return f"{self.prefix}:failed_tasks:index"

    def replay_lock_key(self, dlq_id: str) -> str:
        return f"{self.prefix}:dlq:replay-lock:{dlq_id}"

    async def add(self, record: DLQRecord) -> None:
        payload = record.model_dump_json()
        pipe = self.redis.pipeline()
        pipe.set(self.record_key(record.dlq_id), payload, ex=self.ttl_seconds)
        pipe.lpush(self.records_key(), record.dlq_id)
        pipe.zadd(self.failed_tasks_index_key(), {record.dlq_id: _failed_score(record)})
        if self.ttl_seconds:
            pipe.expire(self.records_key(), self.ttl_seconds)
            pipe.expire(self.failed_tasks_index_key(), self.ttl_seconds)
        await pipe.execute()

    async def get(self, dlq_id: str) -> DLQRecord | None:
        payload = await self.redis.get(self.record_key(dlq_id))
        if payload is None:
            return None
        return DLQRecord.model_validate_json(payload)

    async def list_records(
        self,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[DLQRecord], str | None]:
        raw_ids = await cast(Awaitable[list[str | bytes]], self.redis.lrange(self.records_key(), 0, -1))
        ids = [value.decode("utf-8") if isinstance(value, bytes) else str(value) for value in raw_ids]
        start_index = 0
        if cursor:
            try:
                start_index = ids.index(cursor) + 1
            except ValueError:
                start_index = 0

        normalized_state = DLQRecordState(state) if isinstance(state, str) and state else state
        items: list[DLQRecord] = []
        next_cursor: str | None = None

        for index in range(start_index, len(ids)):
            record = await self.get(ids[index])
            if record is None:
                continue
            if queue_name and record.queue_name != queue_name:
                continue
            if task_id and record.task_id != task_id:
                continue
            if reason and record.reason != reason:
                continue
            if source_queue_name and record.source_queue_name != source_queue_name:
                continue
            if normalized_state is not None and record.state != normalized_state:
                continue
            items.append(record)
            if len(items) == limit:
                next_cursor = record.dlq_id
                break

        if next_cursor is not None:
            for index in range(ids.index(next_cursor) + 1, len(ids)):
                record = await self.get(ids[index])
                if record is None:
                    continue
                if queue_name and record.queue_name != queue_name:
                    continue
                if task_id and record.task_id != task_id:
                    continue
                if reason and record.reason != reason:
                    continue
                if source_queue_name and record.source_queue_name != source_queue_name:
                    continue
                if normalized_state is not None and record.state != normalized_state:
                    continue
                break
            else:
                next_cursor = None

        return items, next_cursor

    async def list_failed_task_records(
        self,
        *,
        service_name: str | None = None,
        queue_name: str | None = None,
        dlq_name: str | None = None,
        error_type: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        investigation_status: FailedTaskInvestigationStatus | str | None = None,
        failed_from: datetime | None = None,
        failed_to: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[DLQRecord], str | None]:
        max_score = _timestamp(failed_to) if failed_to is not None else "+inf"
        min_score = _timestamp(failed_from) if failed_from is not None else "-inf"
        raw_ids = await cast(
            Awaitable[list[str | bytes]],
            self.redis.zrevrangebyscore(self.failed_tasks_index_key(), max_score, min_score),
        )
        ids = [value.decode("utf-8") if isinstance(value, bytes) else str(value) for value in raw_ids]
        start_index = 0
        if cursor:
            try:
                start_index = ids.index(cursor) + 1
            except ValueError:
                start_index = 0

        normalized_investigation: FailedTaskInvestigationStatus | None = None
        if isinstance(investigation_status, FailedTaskInvestigationStatus):
            normalized_investigation = investigation_status
        elif isinstance(investigation_status, str) and investigation_status:
            normalized_investigation = FailedTaskInvestigationStatus(investigation_status)
        items: list[DLQRecord] = []
        next_cursor: str | None = None

        for index in range(start_index, len(ids)):
            record = await self.get(ids[index])
            if record is None:
                continue
            if not _failed_record_matches(
                record,
                service_name=service_name,
                queue_name=queue_name,
                dlq_name=dlq_name,
                error_type=error_type,
                status=status,
                task_id=task_id,
                worker_id=worker_id,
                investigation_status=normalized_investigation,
            ):
                continue
            items.append(record)
            if len(items) == limit:
                next_cursor = record.dlq_id
                break

        if next_cursor is not None:
            for index in range(ids.index(next_cursor) + 1, len(ids)):
                record = await self.get(ids[index])
                if record is not None and _failed_record_matches(
                    record,
                    service_name=service_name,
                    queue_name=queue_name,
                    dlq_name=dlq_name,
                    error_type=error_type,
                    status=status,
                    task_id=task_id,
                    worker_id=worker_id,
                    investigation_status=normalized_investigation,
                ):
                    break
            else:
                next_cursor = None

        return items, next_cursor

    async def summarize_queues(self) -> list[tuple[str, int, datetime | None]]:
        raw_ids = await cast(Awaitable[list[str | bytes]], self.redis.lrange(self.records_key(), 0, -1))
        ids = [value.decode("utf-8") if isinstance(value, bytes) else str(value) for value in raw_ids]
        counts: dict[str, int] = {}
        latest: dict[str, datetime] = {}
        for dlq_id in ids:
            record = await self.get(dlq_id)
            if record is None:
                continue
            counts[record.queue_name] = counts.get(record.queue_name, 0) + 1
            previous = latest.get(record.queue_name)
            if previous is None or record.dead_lettered_at > previous:
                latest[record.queue_name] = record.dead_lettered_at
        return [(queue_name, counts[queue_name], latest.get(queue_name)) for queue_name in sorted(counts)]

    async def claim_replay(self, dlq_id: str, *, force: bool = False) -> DLQRecord | None:
        lock_acquired = await self.redis.set(self.replay_lock_key(dlq_id), "1", nx=True, ex=30)
        if not lock_acquired:
            raise DLQReplayConflict(dlq_id, detail="DLQ replay is already in progress.")

        try:
            record = await self.get(dlq_id)
            if record is None:
                return None
            if record.state == DLQRecordState.REPLAYED and not force:
                raise DLQReplayConflict(dlq_id)
            return record
        except Exception:
            await self.release_replay_claim(dlq_id)
            raise

    async def release_replay_claim(self, dlq_id: str) -> None:
        await self.redis.delete(self.replay_lock_key(dlq_id))

    async def mark_replayed(
        self,
        dlq_id: str,
        *,
        replayed_at: datetime,
        target_queue_name: str,
    ) -> DLQRecord | None:
        record = await self.get(dlq_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "state": DLQRecordState.REPLAYED,
                "replay_count": record.replay_count + 1,
                "replayed_at": replayed_at,
                "replay_target_queue_name": target_queue_name,
            }
        )
        await self.redis.set(self.record_key(updated.dlq_id), updated.model_dump_json(), ex=self.ttl_seconds)
        return updated

    async def update(self, record: DLQRecord) -> DLQRecord:
        await self.redis.set(self.record_key(record.dlq_id), record.model_dump_json(), ex=self.ttl_seconds)
        await self.redis.zadd(self.failed_tasks_index_key(), {record.dlq_id: _failed_score(record)})
        return record

    async def delete(self, dlq_id: str) -> bool:
        deleted = await self.redis.delete(self.record_key(dlq_id))
        await self.redis.zrem(self.failed_tasks_index_key(), dlq_id)
        return bool(deleted)

    async def cleanup_failed_task_index(self, *, older_than: datetime | None = None) -> int:
        if older_than is None:
            if self.ttl_seconds is None:
                return 0
            older_than = datetime.now(UTC) - timedelta(seconds=self.ttl_seconds)
        return int(await self.redis.zremrangebyscore(self.failed_tasks_index_key(), "-inf", _timestamp(older_than)))


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).timestamp()
    return value.timestamp()


def _failed_score(record: DLQRecord) -> float:
    return _timestamp(record.failed_at or record.dead_lettered_at)


def _failed_record_matches(
    record: DLQRecord,
    *,
    service_name: str | None,
    queue_name: str | None,
    dlq_name: str | None,
    error_type: str | None,
    status: str | None,
    task_id: str | None,
    worker_id: str | None,
    investigation_status: FailedTaskInvestigationStatus | None,
) -> bool:
    if service_name and record.service_name != service_name:
        return False
    if queue_name and record.source_queue_name != queue_name:
        return False
    if dlq_name and record.queue_name != dlq_name:
        return False
    if error_type and record.exception_type != error_type:
        return False
    if status and record.status != status:
        return False
    if task_id and record.task_id != task_id:
        return False
    if worker_id and record.worker_id != worker_id:
        return False
    if investigation_status is not None and record.investigation_status != investigation_status:
        return False
    return True


__all__ = ["DLQRecorder", "DLQStore", "RedisDLQStore"]
