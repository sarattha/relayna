from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class TaskEnvelope(BaseModel):
    """Canonical task message shape for transport."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str | None = None
    task_type: str | None = None
    correlation_id: str | None = None
    spec_version: str = "1.0"


class StatusEventEnvelope(BaseModel):
    """Canonical status event shape used by shared infrastructure."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    status: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    correlation_id: str | None = None
    event_id: str | None = None
    service: str | None = None
    spec_version: str = "1.0"

    def as_transport_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data.setdefault("correlation_id", self.correlation_id or self.task_id)
        return ensure_status_event_id(data)


@dataclass(slots=True)
class TerminalStatusSet:
    """Defines statuses that should terminate stream subscriptions."""

    statuses: set[str] = field(default_factory=lambda: {"completed", "failed"})

    def is_terminal(self, status: str | None) -> bool:
        if not status:
            return False
        return status.lower() in {value.lower() for value in self.statuses}


def _to_mutable_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    return dict(event.items())


def normalize_event_aliases(event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalizes external aliases (e.g. documentId) into canonical keys."""

    data = _to_mutable_dict(event)
    task_id = data.get("task_id") or data.get("documentId")
    if task_id:
        data["task_id"] = str(task_id)
    if "documentId" not in data and task_id:
        data["documentId"] = str(task_id)
    return data


def denormalize_document_aliases(event: Mapping[str, Any]) -> dict[str, Any]:
    """Ensures document-oriented clients continue seeing documentId."""

    data = _to_mutable_dict(event)
    task_id = data.get("task_id") or data.get("documentId")
    if task_id:
        data["documentId"] = str(task_id)
    return data


def ensure_status_event_id(event: Mapping[str, Any]) -> dict[str, Any]:
    """Ensures status events have a deterministic event_id when one is not supplied."""

    data = _to_mutable_dict(event)
    existing = data.get("event_id")
    if isinstance(existing, str):
        existing = existing.strip()
        if existing:
            data["event_id"] = existing
            return data

    canonical = dict(data)
    canonical.pop("event_id", None)
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default).encode(
            "utf-8"
        )
    ).hexdigest()
    data["event_id"] = digest
    return data


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
