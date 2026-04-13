from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .aliases import normalize_event_aliases
from .compatibility import ensure_status_event_id


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


__all__ = ["StatusEventEnvelope", "TerminalStatusSet", "ensure_status_event_id", "normalize_event_aliases"]
