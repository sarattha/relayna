from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class WorkflowEnvelope(BaseModel):
    """Canonical stage-to-stage workflow message shape for transport."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    correlation_id: str | None = None
    stage: str
    origin_stage: str | None = None
    action: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = Field(default=None, ge=0, le=255)
    spec_version: str = "1.0"

    def as_transport_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data.setdefault("correlation_id", self.correlation_id or self.task_id)
        return data


__all__ = ["WorkflowEnvelope"]
