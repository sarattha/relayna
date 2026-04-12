from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JoinMode(StrEnum):
    NONE = "none"
    CORRELATION = "correlation"
    LINEAGE = "lineage"
    ALL = "all"

    @property
    def includes_correlation(self) -> bool:
        return self in {JoinMode.CORRELATION, JoinMode.ALL}

    @property
    def includes_lineage(self) -> bool:
        return self in {JoinMode.LINEAGE, JoinMode.ALL}


class StudioTaskPointer(BaseModel):
    model_config = ConfigDict(frozen=True)

    service_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class StudioTaskRef(BaseModel):
    service_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    correlation_id: str | None = None
    parent_refs: list[StudioTaskPointer] = Field(default_factory=list)
    child_refs: list[StudioTaskPointer] = Field(default_factory=list)


JoinKind = Literal["correlation_id", "parent_task_id", "workflow_lineage"]


class StudioTaskJoin(BaseModel):
    task_ref: StudioTaskRef
    join_kind: JoinKind
    matched_value: str = Field(min_length=1)


class StudioJoinWarning(BaseModel):
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    join_kind: JoinKind | None = None
    matched_value: str | None = None


def build_task_ref(
    *,
    service_id: str,
    task_id: str,
    correlation_id: str | None = None,
    parent_refs: Iterable[StudioTaskPointer] = (),
    child_refs: Iterable[StudioTaskPointer] = (),
) -> StudioTaskRef:
    return StudioTaskRef(
        service_id=service_id.strip(),
        task_id=task_id.strip(),
        correlation_id=_normalize_optional_string(correlation_id),
        parent_refs=_dedupe_pointers(parent_refs),
        child_refs=_dedupe_pointers(child_refs),
    )


def build_task_pointer(service_id: str, task_id: str) -> StudioTaskPointer:
    return StudioTaskPointer(service_id=service_id.strip(), task_id=task_id.strip())


def _dedupe_pointers(items: Iterable[StudioTaskPointer]) -> list[StudioTaskPointer]:
    seen: set[tuple[str, str]] = set()
    normalized: list[StudioTaskPointer] = []
    for item in items:
        key = (item.service_id, item.task_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "JoinKind",
    "JoinMode",
    "StudioJoinWarning",
    "StudioTaskJoin",
    "StudioTaskPointer",
    "StudioTaskRef",
    "build_task_pointer",
    "build_task_ref",
]
