from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class DLQReplayConflict(RuntimeError):
    def __init__(self, dlq_id: str, *, detail: str | None = None) -> None:
        super().__init__(detail or f"DLQ message '{dlq_id}' has already been replayed.")
        self.dlq_id = dlq_id


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
    headers: dict[str, Any],
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


__all__ = [
    "DLQMessageDetail",
    "DLQMessageList",
    "DLQMessageSummary",
    "DLQQueueSummary",
    "DLQRecord",
    "DLQRecordState",
    "DLQReplayResult",
    "DLQReplayConflict",
    "QueueInspectionResult",
    "build_dlq_record",
    "inspect_dead_letter_body",
]
