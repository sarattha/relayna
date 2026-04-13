from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StatusSnapshot(BaseModel):
    task_id: str
    latest: dict[str, Any] | None = None


class StatusHistoryResponse(BaseModel):
    task_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)


class StatusStreamEnvelope(BaseModel):
    event: str = "status"
    payload: dict[str, Any]


__all__ = ["StatusHistoryResponse", "StatusSnapshot", "StatusStreamEnvelope"]
