from __future__ import annotations

import re
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, TypeAdapter

from ..api.capabilities_routes import (
    DLQ_MESSAGE_DETAIL_ROUTE_ID,
    DLQ_MESSAGES_ROUTE_ID,
    DLQ_REPLAY_ROUTE_ID,
    FAILED_TASK_DETAIL_ROUTE_ID,
    FAILED_TASK_RETRY_ROUTE_ID,
    FAILED_TASKS_ROUTE_ID,
)
from ..contracts import TaskEnvelope
from ..dlq.models import (
    DLQMessageDetail,
    DLQMessageList,
    DLQMessageSummary,
    DLQRecordState,
    DLQReplayResult,
    FailedTaskDetail,
    FailedTaskInvestigationStatus,
    FailedTaskList,
    FailedTaskRetryRequest,
    FailedTaskRetryResult,
    FailedTaskRetryStatus,
    FailedTaskSummary,
)
from ._submission import _publish_submission
from .contracts import spooltail_task_event

if TYPE_CHECKING:
    from relayna_spooltail import SpooltailClient  # ty: ignore[unresolved-import]

_RECOVERY_TASK_ID = re.compile(r"retry-[0-9a-f]{64}")
_RECOVERY_ROUTES = (
    FAILED_TASKS_ROUTE_ID,
    FAILED_TASK_DETAIL_ROUTE_ID,
    FAILED_TASK_RETRY_ROUTE_ID,
    DLQ_MESSAGES_ROUTE_ID,
    DLQ_MESSAGE_DETAIL_ROUTE_ID,
    DLQ_REPLAY_ROUTE_ID,
)
_TIMESTAMP = TypeAdapter(AwareDatetime)
_LINEAGE = "spooltail_recovery"


@dataclass(slots=True)
class _Failure:
    summary: FailedTaskSummary
    status: dict[str, Any]
    work: TaskEnvelope | None

    def dlq_summary(self) -> DLQMessageSummary:
        summary = self.summary
        return DLQMessageSummary(
            dlq_id=summary.failure_id,
            queue_name=summary.dlq_name,
            source_queue_name=summary.source_queue_name,
            retry_queue_name=summary.retry_queue_name,
            task_id=summary.task_id,
            correlation_id=summary.correlation_id,
            reason="terminal_failed",
            retry_attempt=summary.attempt,
            max_retries=summary.max_attempts,
            content_type="application/json",
            body_encoding="json",
            dead_lettered_at=summary.failed_at,
            state=DLQRecordState.REPLAYED if summary.retried_task_id else DLQRecordState.DEAD_LETTERED,
            replay_count=int(summary.retried_task_id is not None),
            replayed_at=summary.retried_at,
            replay_target_queue_name=summary.retry_target_queue,
        )


@dataclass(slots=True)
class _Recovery:
    get_client: Callable[[], SpooltailClient]
    task_topic: str
    status_topic: str
    service: str

    def retry_id(self, task_id: str) -> str:
        return "retry-" + sha256(f"{self.task_topic}\0{task_id}".encode()).hexdigest()

    async def _failure(self, event: dict[str, Any]) -> _Failure | None:
        task_id = event.get("task_id")
        if str(event.get("status")).lower() != "failed" or not isinstance(task_id, str):
            return None
        try:
            failed_at = _TIMESTAMP.validate_python(event.get("timestamp"))
        except ValueError:
            return None
        client = self.get_client()
        original = await client.latest(self.task_topic, task_id)
        work = None
        if original is not None:
            try:
                candidate = TaskEnvelope.model_validate(original.event)
                if (
                    candidate.task_id == task_id
                    and candidate.service in (None, self.service)
                    and candidate.priority is None
                ):
                    work = candidate
            except ValueError:
                pass
        if work is None and event.get("service") not in (None, self.service):
            return None
        retry = await client.latest(self.task_topic, self.retry_id(task_id))
        retry_event = retry.event if retry is not None else {}
        lineage = retry_event.get(_LINEAGE)
        replayed = isinstance(lineage, dict) and lineage.get("source_task_id") == task_id
        meta = lineage if replayed and isinstance(lineage, dict) else {}
        status_meta = event.get("meta")
        retry_meta = status_meta.get("spooltail_retry") if isinstance(status_meta, dict) else None
        if not isinstance(retry_meta, dict):
            retry_meta = {}
        summary = FailedTaskSummary(
            failure_id=task_id,
            service_name=self.service,
            task_id=task_id,
            correlation_id=event.get("correlation_id"),
            queue_name=self.task_topic,
            source_queue_name=self.task_topic,
            retry_queue_name=self.task_topic,
            dlq_name=self.status_topic,
            status="DLQ",
            attempt=retry_meta.get("attempt", 0),
            max_attempts=retry_meta.get("max_retries", 0),
            failed_at=failed_at,
            error_message=event.get("message"),
            investigation_status=FailedTaskInvestigationStatus.UNREVIEWED,
            retry_status=FailedTaskRetryStatus.RETRIED if replayed else FailedTaskRetryStatus.NOT_RETRIED,
            retried_at=retry_event.get("created_at") if replayed else None,
            retried_by=meta.get("retried_by"),
            retried_task_id=self.retry_id(task_id) if replayed else None,
            retry_target_queue=self.task_topic if replayed else None,
            payload_available=work is not None,
        )
        return _Failure(summary, event, work)

    async def get(self, task_id: str) -> _Failure:
        event = await self.get_client().latest(self.status_topic, task_id)
        failure = await self._failure(event.event) if event is not None else None
        if failure is None:
            raise HTTPException(status_code=404, detail="No retained Spooltail failure for this task")
        return failure

    async def page(self, task_id: str | None, cursor: int | None, limit: int) -> tuple[list[_Failure], str | None]:
        client = self.get_client()
        if task_id is not None:
            record = await client.latest(self.status_topic, task_id)
            failure = await self._failure(record.event) if record is not None else None
            return ([failure] if failure is not None else []), None
        # The status journal is append-ordered. Keep the scan bounded even on busy topics.
        from grpc import StatusCode  # ty: ignore[unresolved-import]
        from grpc.aio import AioRpcError  # ty: ignore[unresolved-import]

        if not hasattr(client, "read_after_batch"):
            raise HTTPException(
                status_code=503, detail="Recovery listing requires a Spooltail client with topic replay"
            )
        try:
            batch = await client.read_after_batch(self.status_topic, after=cursor, limit=min(limit, 64))
        except AioRpcError as exc:
            if exc.code() == StatusCode.FAILED_PRECONDITION:
                raise HTTPException(
                    status_code=410, detail="Recovery cursor expired. Reload from the first page."
                ) from exc
            raise
        items = []
        for record in batch.records:
            failure = await self._failure(record.event)
            if failure is not None:
                items.append(failure)
        return items, str(batch.records[-1].offset) if batch.records else None

    async def retry(self, task_id: str, request: FailedTaskRetryRequest) -> FailedTaskRetryResult:
        if request.target_queue not in (None, self.task_topic):
            raise HTTPException(status_code=422, detail="Spooltail retry must use the original task topic")
        if request.override_payload is not None and not isinstance(request.override_payload, dict):
            raise HTTPException(status_code=422, detail="Spooltail payload override must be a JSON object")
        failure = await self.get(task_id)
        work = failure.work
        if work is None:
            raise HTTPException(status_code=409, detail="The original Spooltail task payload is unavailable")
        retry_id = self.retry_id(task_id)
        original = work.model_dump(exclude_none=True, exclude={"event_id", _LINEAGE})
        event = spooltail_task_event(
            original
            | {
                "task_id": retry_id,
                "service": self.service,
                "correlation_id": work.correlation_id or task_id,
                "created_at": datetime.now(UTC),
                "payload": work.payload if request.override_payload is None else request.override_payload,
                _LINEAGE: {
                    "source_task_id": task_id,
                    "retried_by": request.retried_by,
                    "note": request.note,
                },
            }
        )
        client = self.get_client()
        await _publish_submission(client, self.task_topic, event, timestamp_supplied=False)
        committed = await client.latest(self.task_topic, retry_id)
        if committed is None:
            raise HTTPException(status_code=503, detail="Spooltail could not confirm the recovery task")
        return FailedTaskRetryResult(
            failure_id=task_id,
            target_queue=self.task_topic,
            retry_status=FailedTaskRetryStatus.RETRIED,
            retried_at=_TIMESTAMP.validate_python(committed.event["created_at"]),
            retried_by=request.retried_by,
            retried_task_id=retry_id,
        )


def _register_recovery_routes(
    router: APIRouter, get_client: Callable[[], SpooltailClient], task_topic: str, status_topic: str, service: str
) -> None:
    recovery = _Recovery(get_client, task_topic, status_topic, service)

    @router.get("/failed-tasks")
    async def failures(
        task_id: str | None = None,
        service_name: str | None = None,
        queue_name: str | None = None,
        dlq_name: str | None = None,
        error_type: str | None = None,
        status: str | None = None,
        worker_id: str | None = None,
        investigation_status: FailedTaskInvestigationStatus | None = None,
        failed_from: AwareDatetime | None = None,
        failed_to: AwareDatetime | None = None,
        cursor: int | None = Query(default=None, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> FailedTaskList:
        if (
            service_name not in (None, service)
            or queue_name not in (None, task_topic)
            or dlq_name not in (None, status_topic)
            or status not in (None, "DLQ")
            or investigation_status not in (None, FailedTaskInvestigationStatus.UNREVIEWED)
            or error_type
            or worker_id
        ):
            return FailedTaskList(items=[])
        items, after = await recovery.page(task_id, cursor, limit)
        return FailedTaskList(
            items=[
                item.summary
                for item in reversed(items)
                if (failed_from is None or item.summary.failed_at >= failed_from)
                and (failed_to is None or item.summary.failed_at <= failed_to)
            ],
            next_cursor=after,
        )

    @router.get("/failed-tasks/{failure_id}")
    async def failed_task(failure_id: str) -> FailedTaskDetail:
        failure = await recovery.get(failure_id)
        work = failure.work
        return FailedTaskDetail(
            **failure.summary.model_dump(),
            dlq_id=failure_id,
            reason="terminal_failed",
            content_type="application/json",
            body=work.payload if work is not None else None,
            body_encoding="json",
            raw_body_b64=b64encode(work.model_dump_json().encode()).decode() if work is not None else "",
            latest_status=failure.status,
        )

    @router.post("/failed-tasks/{failure_id}/retry")
    async def retry_failed_task(failure_id: str, request: FailedTaskRetryRequest) -> FailedTaskRetryResult:
        return await recovery.retry(failure_id, request)

    @router.get("/dlq/messages")
    async def dead_letters(
        task_id: str | None = None,
        queue_name: str | None = None,
        source_queue_name: str | None = None,
        reason: str | None = None,
        state: DLQRecordState | None = None,
        cursor: int | None = Query(default=None, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> DLQMessageList:
        if (
            queue_name not in (None, status_topic)
            or source_queue_name not in (None, task_topic)
            or reason not in (None, "terminal_failed")
        ):
            return DLQMessageList(items=[])
        failures, after = await recovery.page(task_id, cursor, limit)
        items = [item.dlq_summary() for item in reversed(failures)]
        return DLQMessageList(items=[item for item in items if state is None or item.state == state], next_cursor=after)

    @router.get("/dlq/messages/{dlq_id}")
    async def dead_letter(dlq_id: str) -> DLQMessageDetail:
        failure = await recovery.get(dlq_id)
        return DLQMessageDetail(
            **failure.dlq_summary().model_dump(),
            body=failure.work.payload if failure.work is not None else None,
            latest_status=failure.status,
        )

    @router.post("/dlq/messages/{dlq_id}/replay")
    async def replay_dead_letter(dlq_id: str, force: bool = False) -> DLQReplayResult:
        if force:
            raise HTTPException(status_code=409, detail="Spooltail recovery allows one replay per failed task")
        result = await recovery.retry(dlq_id, FailedTaskRetryRequest())
        return DLQReplayResult(
            dlq_id=dlq_id,
            target_queue_name=task_topic,
            state=DLQRecordState.REPLAYED,
            replayed_at=result.retried_at,
            replay_count=1,
            forced=False,
        )
