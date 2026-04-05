from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StageSummaryResponse(BaseModel):
    name: str
    queue: str
    binding_keys: list[str] = Field(default_factory=list)
    publish_routing_key: str


class WorkflowRouteSummary(BaseModel):
    name: str
    routing_key: str
    target_stage: str


class TopologyGraphResponse(BaseModel):
    workflow_exchange: str | None = None
    status_queue: str
    stages: list[StageSummaryResponse] = Field(default_factory=list)
    entry_routes: list[WorkflowRouteSummary] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


__all__ = ["StageSummaryResponse", "TopologyGraphResponse", "WorkflowRouteSummary"]
