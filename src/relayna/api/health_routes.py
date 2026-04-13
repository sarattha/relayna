from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter
from pydantic import BaseModel, Field


class WorkerHeartbeatSummary(BaseModel):
    worker_name: str
    running: bool
    last_heartbeat_at: datetime | None = None


class WorkerHeartbeatListResponse(BaseModel):
    reported_at: datetime
    workers: list[WorkerHeartbeatSummary] = Field(default_factory=list)


WorkerHeartbeatProvider = Callable[
    [],
    Awaitable[Iterable[WorkerHeartbeatSummary | dict[str, Any] | Any]]
    | Iterable[WorkerHeartbeatSummary | dict[str, Any] | Any],
]


async def _resolve_workers(provider: WorkerHeartbeatProvider) -> list[WorkerHeartbeatSummary]:
    result = provider()
    if inspect.isawaitable(result):
        result = await result
    iterable = cast(Iterable[WorkerHeartbeatSummary | dict[str, Any] | Any], result)
    workers = list(iterable)
    return [WorkerHeartbeatSummary.model_validate(item) for item in workers]


def create_worker_health_router(
    *,
    heartbeat_provider: WorkerHeartbeatProvider,
    prefix: str = "/relayna/health",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/workers", response_model=WorkerHeartbeatListResponse)
    async def worker_health() -> WorkerHeartbeatListResponse:
        return WorkerHeartbeatListResponse(
            reported_at=datetime.now(UTC), workers=await _resolve_workers(heartbeat_provider)
        )

    return router


__all__ = [
    "WorkerHeartbeatListResponse",
    "WorkerHeartbeatProvider",
    "WorkerHeartbeatSummary",
    "create_worker_health_router",
]
