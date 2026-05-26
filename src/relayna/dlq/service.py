from __future__ import annotations

import base64
import json
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
    FailedTaskDetail,
    FailedTaskInvestigationStatus,
    FailedTaskList,
    FailedTaskRetryRejected,
    FailedTaskRetryRequest,
    FailedTaskRetryResult,
    FailedTaskRetryStatus,
    FailedTaskSummary,
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
            diagnosis=record.diagnosis,
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
        allowed_queue_names: list[str] = []
        allowed_queue_name_set: set[str] = set()
        for candidate in candidate_queue_names:
            normalized = str(candidate).strip()
            if normalized and normalized not in allowed_queue_name_set:
                allowed_queue_name_set.add(normalized)
                allowed_queue_names.append(normalized)
        ordered_queue_names: list[str] = []
        if queue_name is not None and str(queue_name).strip():
            normalized_queue_name = str(queue_name).strip()
            if normalized_queue_name not in allowed_queue_name_set:
                allowed = ", ".join(sorted(allowed_queue_names)) if allowed_queue_names else "none"
                raise ValueError(f"Unsupported broker DLQ queue '{normalized_queue_name}'. Allowed queues: {allowed}.")
            ordered_queue_names.append(normalized_queue_name)
        else:
            ordered_queue_names.extend(allowed_queue_names)
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

    async def list_failed_tasks(
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
    ) -> FailedTaskList:
        records, next_cursor = await self.dlq_store.list_failed_task_records(
            service_name=service_name,
            queue_name=queue_name,
            dlq_name=dlq_name,
            error_type=error_type,
            status=status,
            task_id=task_id,
            worker_id=worker_id,
            investigation_status=investigation_status,
            failed_from=failed_from,
            failed_to=failed_to,
            cursor=cursor,
            limit=limit,
        )
        return FailedTaskList(items=[self._failed_summary(record) for record in records], next_cursor=next_cursor)

    async def get_failed_task_detail(self, failure_id: str) -> FailedTaskDetail | None:
        record = await self.dlq_store.get(failure_id)
        if record is None:
            return None
        latest_status: dict[str, Any] | None = None
        status_history: list[dict[str, Any]] = []
        last_logs = list(record.last_logs)
        if self.status_store is not None and record.task_id:
            latest_status = await self.status_store.get_latest(record.task_id)
            status_history = await self.status_store.get_history(record.task_id)
            if not last_logs:
                last_logs = status_history[-100:]
        summary = self._failed_summary(record)
        return FailedTaskDetail(
            **summary.model_dump(),
            dlq_id=record.dlq_id,
            reason=record.reason,
            headers=dict(record.headers),
            content_type=record.content_type,
            body=record.body,
            body_encoding=record.body_encoding,
            raw_body_b64=record.raw_body_b64,
            traceback=record.traceback,
            input_preview=record.input_preview,
            metadata=dict(record.metadata),
            last_logs=last_logs,
            latest_status=latest_status,
            status_history=status_history,
            investigation_note=record.investigation_note,
            retry_note=record.retry_note,
            diagnosis=record.diagnosis,
        )

    async def mark_failed_task_investigated(
        self,
        failure_id: str,
        *,
        investigated_by: str | None = None,
        note: str | None = None,
    ) -> FailedTaskDetail | None:
        record = await self.dlq_store.get(failure_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "investigation_status": FailedTaskInvestigationStatus.INVESTIGATED,
                "investigated_at": _utcnow(),
                "investigated_by": investigated_by or "operator",
                "investigation_note": note,
            }
        )
        await self.dlq_store.update(updated)
        return await self.get_failed_task_detail(failure_id)

    async def mark_failed_task_uninvestigated(self, failure_id: str) -> FailedTaskDetail | None:
        record = await self.dlq_store.get(failure_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "investigation_status": FailedTaskInvestigationStatus.UNREVIEWED,
                "investigated_at": None,
                "investigated_by": None,
                "investigation_note": None,
            }
        )
        await self.dlq_store.update(updated)
        return await self.get_failed_task_detail(failure_id)

    async def delete_failed_task(self, failure_id: str) -> bool:
        return await self.dlq_store.delete(failure_id)

    async def retry_failed_task(
        self,
        failure_id: str,
        request: FailedTaskRetryRequest | None = None,
    ) -> FailedTaskRetryResult | None:
        record = await self.dlq_store.get(failure_id)
        if record is None:
            return None
        if record.status not in {"DLQ", "retry_exhausted", "terminal_failed"}:
            raise FailedTaskRetryRejected(
                failure_id,
                code="manual_retry_not_allowed",
                detail=(
                    "Manual retry is only allowed after the task exceeds max attempts and reaches "
                    "DLQ/retry_exhausted state."
                ),
            )
        if not record.payload_available or not record.raw_body_b64:
            raise FailedTaskRetryRejected(
                failure_id,
                code="manual_retry_payload_unavailable",
                detail="Manual retry is blocked because the original payload is unavailable.",
            )

        retry_request = request or FailedTaskRetryRequest()
        retried_at = _utcnow()
        target_queue = retry_request.target_queue or record.source_queue_name
        payload = _retry_payload_bytes(record, retry_request.override_payload)
        headers = dict(record.headers)
        headers["x-relayna-manual-retry-from-failure-id"] = failure_id
        headers["x-relayna-manual-retry-at"] = retried_at.isoformat()
        headers["x-relayna-retry-attempt"] = 0
        headers.pop("x-relayna-failure-reason", None)
        headers.pop("x-relayna-exception-type", None)

        await self.rabbitmq.publish_raw_to_queue(
            target_queue,
            payload,
            correlation_id=record.correlation_id,
            headers=headers,
            content_type=record.content_type,
        )
        retried_task_id = (
            _task_id_from_payload(retry_request.override_payload)
            if retry_request.override_payload is not None
            else record.task_id
        )
        updated = record.model_copy(
            update={
                "retry_status": FailedTaskRetryStatus.RETRIED,
                "retried_at": retried_at,
                "retried_by": retry_request.retried_by or "operator",
                "retried_task_id": retried_task_id,
                "retry_target_queue": target_queue,
                "retry_note": retry_request.note,
            }
        )
        await self.dlq_store.update(updated)
        return FailedTaskRetryResult(
            failure_id=failure_id,
            target_queue=target_queue,
            retry_status=FailedTaskRetryStatus.RETRIED,
            retried_at=retried_at,
            retried_by=updated.retried_by,
            retried_task_id=retried_task_id,
        )

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

    def _failed_summary(self, record: DLQRecord) -> FailedTaskSummary:
        return FailedTaskSummary(
            failure_id=record.failure_id or record.dlq_id,
            service_name=record.service_name,
            task_id=record.task_id,
            correlation_id=record.correlation_id,
            queue_name=record.source_queue_name,
            source_queue_name=record.source_queue_name,
            retry_queue_name=record.retry_queue_name,
            dlq_name=record.queue_name,
            status=record.status,
            attempt=record.retry_attempt,
            max_attempts=record.max_retries,
            failed_at=record.failed_at or record.dead_lettered_at,
            error_type=record.exception_type,
            error_message=record.exception_message or record.reason,
            worker_id=record.worker_id,
            runtime_name=record.runtime_name,
            runtime_version=record.runtime_version,
            investigation_status=record.investigation_status,
            investigated_at=record.investigated_at,
            investigated_by=record.investigated_by,
            retry_status=record.retry_status,
            retried_at=record.retried_at,
            retried_by=record.retried_by,
            retried_task_id=record.retried_task_id,
            retry_target_queue=record.retry_target_queue,
            payload_available=record.payload_available,
        )


def _retry_payload_bytes(record: DLQRecord, override_payload: Any) -> bytes:
    if override_payload is None:
        return base64.b64decode(record.raw_body_b64.encode("ascii"))
    if isinstance(override_payload, str):
        return override_payload.encode("utf-8")
    return json.dumps(override_payload, ensure_ascii=False).encode("utf-8")


def _task_id_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("task_id")
        if value is not None:
            return str(value)
    return None


__all__ = ["DLQReplayConflict", "DLQService", "FailedTaskRetryRejected"]
