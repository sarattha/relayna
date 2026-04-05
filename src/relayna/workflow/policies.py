from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class StagePolicy:
    stage: str
    max_retries: int = 0
    timeout_seconds: float | None = None
    concurrency: int | None = None
    dedup_key_field: str | None = None


__all__ = ["StagePolicy"]
