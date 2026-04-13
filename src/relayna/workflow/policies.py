from __future__ import annotations

from dataclasses import dataclass

from ..topology.workflow import WorkflowStage


@dataclass(slots=True, frozen=True)
class StagePolicy:
    stage: str
    max_retries: int = 0
    timeout_seconds: float | None = None
    concurrency: int | None = None
    dedup_key_field: str | None = None

    @classmethod
    def from_stage(cls, stage: WorkflowStage) -> StagePolicy:
        dedup_key_field = stage.dedup_key_fields[0] if stage.dedup_key_fields else None
        return cls(
            stage=stage.name,
            max_retries=stage.max_retries or 0,
            timeout_seconds=stage.timeout_seconds,
            concurrency=stage.max_inflight,
            dedup_key_field=dedup_key_field,
        )


__all__ = ["StagePolicy"]
