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


class DLQFailureDiagnosis(BaseModel):
    reason: str
    exception_type: str | None = None
    terminal_retry_attempt: int


class DLQRetryDiagnosis(BaseModel):
    attempt: int
    max_retries: int
    source_queue_name: str
    retry_queue_name: str
    policy_name: str | None = None
    policy_reason: str | None = None


class DLQOwnershipDiagnosis(BaseModel):
    consumer_name: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None


class DLQEnvelopeDiagnosis(BaseModel):
    task_type: str | None = None
    workflow_stage: str | None = None
    action: str | None = None
    content_type: str | None = None
    body_encoding: str


class DLQReplayGuidance(BaseModel):
    recommended_action: str = "review_before_replay"
    warnings: list[str] = Field(default_factory=list)


class DLQDiagnosisBundle(BaseModel):
    failure: DLQFailureDiagnosis
    retry: DLQRetryDiagnosis
    ownership: DLQOwnershipDiagnosis
    envelope: DLQEnvelopeDiagnosis
    replay: DLQReplayGuidance = Field(default_factory=DLQReplayGuidance)


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
    diagnosis: DLQDiagnosisBundle | None = None


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
    diagnosis: DLQDiagnosisBundle | None = None


class DLQMessageList(BaseModel):
    items: list[DLQMessageSummary]
    next_cursor: str | None = None


class BrokerDLQMessage(BaseModel):
    queue_name: str
    message_key: str
    task_id: str | None = None
    correlation_id: str | None = None
    reason: str | None = None
    source_queue_name: str | None = None
    content_type: str | None = None
    body_encoding: str
    dead_lettered_at: datetime | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    body: Any
    raw_body_b64: str
    redelivered: bool | None = None


class BrokerDLQMessageList(BaseModel):
    items: list[BrokerDLQMessage]


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
    consumer_name: str | None = None,
    diagnosis: DLQDiagnosisBundle | None = None,
) -> DLQRecord:
    timestamp = dead_lettered_at or _utcnow()
    inspected_body, body_encoding, raw_body_b64 = inspect_dead_letter_body(body)
    resolved_diagnosis = diagnosis or build_dlq_diagnosis(
        source_queue_name=source_queue_name,
        retry_queue_name=retry_queue_name,
        task_id=task_id,
        correlation_id=correlation_id,
        reason=reason,
        exception_type=exception_type,
        retry_attempt=retry_attempt,
        max_retries=max_retries,
        content_type=content_type,
        inspected_body=inspected_body,
        body_encoding=body_encoding,
        consumer_name=consumer_name,
    )
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
        diagnosis=resolved_diagnosis,
    )


def build_dlq_diagnosis(
    *,
    source_queue_name: str,
    retry_queue_name: str,
    task_id: str | None,
    correlation_id: str | None,
    reason: str,
    exception_type: str | None,
    retry_attempt: int,
    max_retries: int,
    content_type: str | None,
    inspected_body: Any,
    body_encoding: str,
    consumer_name: str | None = None,
) -> DLQDiagnosisBundle:
    envelope = _diagnose_envelope(
        inspected_body,
        content_type=content_type,
        body_encoding=body_encoding,
    )
    warnings: list[str] = []
    if reason in {"malformed_json", "invalid_envelope", "unsupported_action", "dedup_conflict"}:
        warnings.append(f"Replay may fail again until '{reason}' is resolved.")
    if retry_attempt >= max_retries:
        warnings.append("Message reached the configured retry limit.")
    return DLQDiagnosisBundle(
        failure=DLQFailureDiagnosis(
            reason=reason,
            exception_type=exception_type,
            terminal_retry_attempt=retry_attempt,
        ),
        retry=DLQRetryDiagnosis(
            attempt=retry_attempt,
            max_retries=max_retries,
            source_queue_name=source_queue_name,
            retry_queue_name=retry_queue_name,
            policy_reason=reason,
        ),
        ownership=DLQOwnershipDiagnosis(
            consumer_name=consumer_name,
            task_id=task_id,
            correlation_id=correlation_id,
        ),
        envelope=envelope,
        replay=DLQReplayGuidance(warnings=warnings),
    )


def _diagnose_envelope(
    inspected_body: Any,
    *,
    content_type: str | None,
    body_encoding: str,
) -> DLQEnvelopeDiagnosis:
    task_type: str | None = None
    workflow_stage: str | None = None
    action: str | None = None
    if isinstance(inspected_body, dict):
        task_type = _string_or_none(inspected_body.get("task_type"))
        workflow_stage = _string_or_none(inspected_body.get("stage"))
        action = _string_or_none(inspected_body.get("action"))
    return DLQEnvelopeDiagnosis(
        task_type=task_type,
        workflow_stage=workflow_stage,
        action=action,
        content_type=content_type,
        body_encoding=body_encoding,
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "BrokerDLQMessage",
    "BrokerDLQMessageList",
    "DLQMessageDetail",
    "DLQMessageList",
    "DLQMessageSummary",
    "DLQQueueSummary",
    "DLQDiagnosisBundle",
    "DLQEnvelopeDiagnosis",
    "DLQFailureDiagnosis",
    "DLQOwnershipDiagnosis",
    "DLQRecord",
    "DLQRecordState",
    "DLQReplayGuidance",
    "DLQReplayResult",
    "DLQReplayConflict",
    "DLQRetryDiagnosis",
    "QueueInspectionResult",
    "build_dlq_diagnosis",
    "build_dlq_record",
    "inspect_dead_letter_body",
]
