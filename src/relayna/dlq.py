from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from .rabbitmq import RelaynaRabbitClient
from .status_store import RedisStatusStore


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DLQRecordState(str, Enum):
    DEAD_LETTERED = "dead_lettered"
    REPLAYED = "replayed"


class QueueInspectionResult(BaseModel):
    queue_name: str
    exists: bool
    message_count: int | None = None


class DLQRecord(BaseModel):
    dlq_id: str
    queue_name: str
    source_queue_name: str
    retry_queue_name: str
    task_id: str | None = None
    correlation_id: str | None = None
    reason: str
    exception_type: str | None = None
    retry_attempt: int
    max_retries: int
    headers: dict[str, Any] = Field(default_factory=dict)
    content_type: str | None = None
    body: Any
    body_encoding: str
    raw_body_b64: str
    dead_lettered_at: datetime = Field(default_factory=_utcnow)
    state: DLQRecordState = DLQRecordState.DEAD_LETTERED
    replay_count: int = 0
    replayed_at: datetime | None = None
    replay_target_queue_name: str | None = None


class DLQMessageSummary(BaseModel):
    dlq_id: str
    queue_name: str
    source_queue_name: str
    retry_queue_name: str
    task_id: str | None = None
    correlation_id: str | None = None
    reason: str
    exception_type: str | None = None
    retry_attempt: int
    max_retries: int
    content_type: str | None = None
    body_encoding: str
    dead_lettered_at: datetime
    state: DLQRecordState
    replay_count: int
    replayed_at: datetime | None = None
    replay_target_queue_name: str | None = None


class DLQMessageDetail(DLQMessageSummary):
    headers: dict[str, Any] = Field(default_factory=dict)
    body: Any
    latest_status: dict[str, Any] | None = None
    status_history: list[dict[str, Any]] = Field(default_factory=list)


class DLQMessageList(BaseModel):
    items: list[DLQMessageSummary]
    next_cursor: str | None = None


class DLQQueueSummary(BaseModel):
    queue_name: str
    indexed_count: int
    last_indexed_at: datetime | None = None
    exists: bool
    message_count: int | None = None


class DLQReplayResult(BaseModel):
    dlq_id: str
    target_queue_name: str
    state: DLQRecordState
    replayed_at: datetime
    replay_count: int
    forced: bool


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


def inspect_dead_letter_body(body: bytes) -> tuple[Any, str, str]:
    raw_body_b64 = base64.b64encode(body).decode("ascii")
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return raw_body_b64, "base64", raw_body_b64

    try:
        return json.loads(decoded), "json", raw_body_b64
    except json.JSONDecodeError:
        return decoded, "text", raw_body_b64


def build_dlq_id(
    *,
    queue_name: str,
    source_queue_name: str,
    correlation_id: str | None,
    retry_attempt: int,
    body: bytes,
    dead_lettered_at: datetime,
) -> str:
    digest = hashlib.sha256()
    digest.update(queue_name.encode("utf-8"))
    digest.update(source_queue_name.encode("utf-8"))
    digest.update(str(correlation_id or "").encode("utf-8"))
    digest.update(str(retry_attempt).encode("utf-8"))
    digest.update(dead_lettered_at.isoformat().encode("utf-8"))
    digest.update(body)
    return digest.hexdigest()


def build_dlq_record(
    *,
    queue_name: str,
    source_queue_name: str,
    retry_queue_name: str,
    task_id: str | None,
    correlation_id: str | None,
    reason: str,
    exception_type: str | None,
    retry_attempt: int,
    max_retries: int,
    headers: Mapping[str, Any],
    content_type: str | None,
    body: bytes,
    dead_lettered_at: datetime | None = None,
) -> DLQRecord:
    timestamp = dead_lettered_at or _utcnow()
    inspected_body, body_encoding, raw_body_b64 = inspect_dead_letter_body(body)
    return DLQRecord(
        dlq_id=build_dlq_id(
            queue_name=queue_name,
            source_queue_name=source_queue_name,
            correlation_id=correlation_id,
            retry_attempt=retry_attempt,
            body=body,
            dead_lettered_at=timestamp,
        ),
        queue_name=queue_name,
        source_queue_name=source_queue_name,
        retry_queue_name=retry_queue_name,
        task_id=task_id,
        correlation_id=correlation_id,
        reason=reason,
        exception_type=exception_type,
        retry_attempt=retry_attempt,
        max_retries=max_retries,
        headers=dict(headers),
        content_type=content_type,
        body=inspected_body,
        body_encoding=body_encoding,
        raw_body_b64=raw_body_b64,
        dead_lettered_at=timestamp,
    )


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

    def replay_lock_key(self, dlq_id: str) -> str:
        return f"{self.prefix}:dlq:replay-lock:{dlq_id}"

    async def add(self, record: DLQRecord) -> None:
        payload = record.model_dump_json()
        pipe = self.redis.pipeline()
        pipe.set(self.record_key(record.dlq_id), payload, ex=self.ttl_seconds)
        pipe.lpush(self.records_key(), record.dlq_id)
        if self.ttl_seconds:
            pipe.expire(self.records_key(), self.ttl_seconds)
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
        raw_ids = await self.redis.lrange(self.records_key(), 0, -1)  # type: ignore[misc]
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

    async def summarize_queues(self) -> list[tuple[str, int, datetime | None]]:
        raw_ids = await self.redis.lrange(self.records_key(), 0, -1)  # type: ignore[misc]
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


@dataclass(slots=True)
class DLQService:
    rabbitmq: RelaynaRabbitClient
    dlq_store: DLQStore
    status_store: RedisStatusStore | None = None

    async def get_queue_summaries(self) -> list[DLQQueueSummary]:
        queue_summaries = await self.dlq_store.summarize_queues()
        items: list[DLQQueueSummary] = []
        for queue_name, indexed_count, last_indexed_at in queue_summaries:
            inspection = await self.inspect_queue(queue_name)
            items.append(
                DLQQueueSummary(
                    queue_name=queue_name,
                    indexed_count=indexed_count,
                    last_indexed_at=last_indexed_at,
                    exists=inspection.exists,
                    message_count=inspection.message_count,
                )
            )
        return items

    async def inspect_queue(self, queue_name: str) -> QueueInspectionResult:
        inspection = await self.rabbitmq.inspect_queue(queue_name)
        if inspection is None:
            return QueueInspectionResult(queue_name=queue_name, exists=False, message_count=None)
        return QueueInspectionResult(queue_name=queue_name, exists=True, message_count=inspection.message_count)

    async def list_messages(
        self,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> DLQMessageList:
        records, next_cursor = await self.dlq_store.list_records(
            queue_name=queue_name,
            task_id=task_id,
            reason=reason,
            source_queue_name=source_queue_name,
            state=state,
            cursor=cursor,
            limit=limit,
        )
        return DLQMessageList(items=[self._summary(record) for record in records], next_cursor=next_cursor)

    async def get_message_detail(self, dlq_id: str) -> DLQMessageDetail | None:
        record = await self.dlq_store.get(dlq_id)
        if record is None:
            return None
        latest_status: dict[str, Any] | None = None
        status_history: list[dict[str, Any]] = []
        if self.status_store is not None and record.task_id:
            latest_status = await self.status_store.get_latest(record.task_id)
            status_history = await self.status_store.get_history(record.task_id)
        summary = self._summary(record)
        return DLQMessageDetail(
            **summary.model_dump(),
            headers=dict(record.headers),
            body=record.body,
            latest_status=latest_status,
            status_history=status_history,
        )

    async def replay_message(self, dlq_id: str, *, force: bool = False) -> DLQReplayResult | None:
        record = await self.dlq_store.claim_replay(dlq_id, force=force)
        if record is None:
            await self.dlq_store.release_replay_claim(dlq_id)
            return None

        replayed_at = _utcnow()
        headers = dict(record.headers)
        if "x-relayna-retry-attempt" in headers:
            headers["x-relayna-original-retry-attempt"] = headers["x-relayna-retry-attempt"]
        if "x-relayna-failure-reason" in headers:
            headers["x-relayna-original-failure-reason"] = headers["x-relayna-failure-reason"]
        if "x-relayna-exception-type" in headers:
            headers["x-relayna-original-exception-type"] = headers["x-relayna-exception-type"]
        headers["x-relayna-replayed-from-dlq"] = True
        headers["x-relayna-dlq-id"] = record.dlq_id
        headers["x-relayna-replayed-at"] = replayed_at.isoformat()
        headers["x-relayna-retry-attempt"] = 0
        headers.pop("x-relayna-failure-reason", None)
        headers.pop("x-relayna-exception-type", None)

        try:
            await self.rabbitmq.publish_raw_to_queue(
                record.retry_queue_name,
                base64.b64decode(record.raw_body_b64.encode("ascii")),
                correlation_id=record.correlation_id,
                headers=headers,
                content_type=record.content_type,
            )
            updated = await self.dlq_store.mark_replayed(
                record.dlq_id,
                replayed_at=replayed_at,
                target_queue_name=record.retry_queue_name,
            )
            if updated is None:
                return None
            return DLQReplayResult(
                dlq_id=updated.dlq_id,
                target_queue_name=updated.retry_queue_name,
                state=updated.state,
                replayed_at=updated.replayed_at or replayed_at,
                replay_count=updated.replay_count,
                forced=force,
            )
        finally:
            await self.dlq_store.release_replay_claim(dlq_id)

    def _summary(self, record: DLQRecord) -> DLQMessageSummary:
        return DLQMessageSummary(
            dlq_id=record.dlq_id,
            queue_name=record.queue_name,
            source_queue_name=record.source_queue_name,
            retry_queue_name=record.retry_queue_name,
            task_id=record.task_id,
            correlation_id=record.correlation_id,
            reason=record.reason,
            exception_type=record.exception_type,
            retry_attempt=record.retry_attempt,
            max_retries=record.max_retries,
            content_type=record.content_type,
            body_encoding=record.body_encoding,
            dead_lettered_at=record.dead_lettered_at,
            state=record.state,
            replay_count=record.replay_count,
            replayed_at=record.replayed_at,
            replay_target_queue_name=record.replay_target_queue_name,
        )


class DLQReplayConflict(RuntimeError):
    def __init__(self, dlq_id: str, *, detail: str | None = None) -> None:
        super().__init__(detail or f"DLQ message '{dlq_id}' has already been replayed.")
        self.dlq_id = dlq_id


__all__ = [
    "DLQMessageDetail",
    "DLQMessageList",
    "DLQMessageSummary",
    "DLQQueueSummary",
    "DLQRecord",
    "DLQRecordState",
    "DLQReplayConflict",
    "DLQReplayResult",
    "DLQService",
    "RedisDLQStore",
    "build_dlq_record",
    "inspect_dead_letter_body",
]
