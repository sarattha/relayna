from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..rabbitmq import RelaynaRabbitClient
from ..status.store import RedisStatusStore
from .broker import BrokerDLQMessageInspector
from .models import (
    BrokerDLQMessageList,
    DLQMessageDetail,
    DLQMessageList,
    DLQMessageSummary,
    DLQQueueSummary,
    DLQRecord,
    DLQRecordState,
    DLQReplayConflict,
    DLQReplayResult,
    QueueInspectionResult,
)
from .store import DLQStore


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class DLQService:
    rabbitmq: RelaynaRabbitClient
    dlq_store: DLQStore
    status_store: RedisStatusStore | None = None
    broker_message_inspector: BrokerDLQMessageInspector | None = None

    async def get_queue_summaries(self) -> list[DLQQueueSummary]:
        return await self._build_queue_summaries(await self.dlq_store.summarize_queues())

    async def get_broker_queue_summaries(self, candidate_queue_names) -> list[DLQQueueSummary]:
        indexed = {
            queue_name: (indexed_count, last_indexed_at)
            for queue_name, indexed_count, last_indexed_at in await self.dlq_store.summarize_queues()
        }
        for queue_name in candidate_queue_names:
            normalized = str(queue_name).strip()
            if not normalized or normalized in indexed:
                continue
            indexed[normalized] = (0, None)
        queue_summaries = [
            (queue_name, indexed_count, last_indexed_at)
            for queue_name, (indexed_count, last_indexed_at) in sorted(indexed.items())
        ]
        return await self._build_queue_summaries(queue_summaries, include_broker_only=True)

    async def inspect_queue(self, queue_name: str) -> QueueInspectionResult:
        inspection = await self.rabbitmq.inspect_queue(queue_name)
        if inspection is None:
            return QueueInspectionResult(queue_name=queue_name, exists=False, message_count=None)
        return QueueInspectionResult(queue_name=queue_name, exists=True, message_count=inspection.message_count)

    @property
    def supports_broker_message_reads(self) -> bool:
        return self.broker_message_inspector is not None

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

    async def list_broker_messages(
        self,
        candidate_queue_names,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> BrokerDLQMessageList:
        if self.broker_message_inspector is None:
            raise RuntimeError("Broker DLQ message inspection is not configured.")
        normalized_limit = max(1, min(int(limit), 200))
        ordered_queue_names: list[str] = []
        if queue_name is not None and str(queue_name).strip():
            ordered_queue_names.append(str(queue_name).strip())
        else:
            for candidate in candidate_queue_names:
                normalized = str(candidate).strip()
                if normalized and normalized not in ordered_queue_names:
                    ordered_queue_names.append(normalized)
        items = []
        for current_queue_name in ordered_queue_names:
            queue_items = await self.broker_message_inspector.list_messages(current_queue_name, limit=normalized_limit)
            for item in queue_items:
                if task_id and item.task_id != task_id:
                    continue
                items.append(item)
                if len(items) >= normalized_limit:
                    return BrokerDLQMessageList(items=items)
        return BrokerDLQMessageList(items=items)

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

    async def _build_queue_summaries(
        self,
        queue_summaries,
        *,
        include_broker_only: bool = False,
    ) -> list[DLQQueueSummary]:
        items: list[DLQQueueSummary] = []
        for queue_name, indexed_count, last_indexed_at in queue_summaries:
            inspection = await self.inspect_queue(queue_name)
            if not include_broker_only and indexed_count <= 0:
                continue
            if include_broker_only and indexed_count <= 0 and not inspection.exists:
                continue
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


__all__ = ["DLQReplayConflict", "DLQService"]
