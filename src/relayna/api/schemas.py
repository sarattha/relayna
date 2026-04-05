from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..contracts import ActionSchema


class StageSummaryResponse(BaseModel):
    id: str | None = None
    name: str
    queue: str
    binding_keys: list[str] = Field(default_factory=list)
    publish_routing_key: str
    queue_arguments: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    role: str | None = None
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    sla_ms: int | None = None
    accepted_actions: list[ActionSchema] = Field(default_factory=list)
    produced_actions: list[ActionSchema] = Field(default_factory=list)
    allowed_next_stages: list[str] = Field(default_factory=list)
    terminal: bool = False
    timeout_seconds: float | None = None
    max_retries: int | None = None
    retry_delay_ms: int | None = None
    max_inflight: int | None = None
    dedup_key_fields: list[str] = Field(default_factory=list)


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
