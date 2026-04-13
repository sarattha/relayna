from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
    priority: int | None = Field(default=None, ge=0, le=255)
    spec_version: str = "1.0"


class BatchTaskEnvelope(BaseModel):
    """Batch transport wrapper for task messages."""

    model_config = ConfigDict(extra="allow")

    batch_id: str
    tasks: list[TaskEnvelope] = Field(min_length=1)
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    spec_version: str = "1.0"


__all__ = ["BatchTaskEnvelope", "TaskEnvelope"]
