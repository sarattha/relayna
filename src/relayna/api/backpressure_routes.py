from __future__ import annotations

from fastapi import APIRouter

from ..observability import RuntimePressureService, RuntimePressureSnapshot
from .capabilities_routes import RUNTIME_BACKPRESSURE_ROUTE_ID


def create_backpressure_router(
    *,
    pressure_service: RuntimePressureService,
    path: str = "/relayna/runtime/backpressure",
) -> APIRouter:
    router = APIRouter()

    @router.get(path, response_model=RuntimePressureSnapshot)
    async def runtime_backpressure() -> RuntimePressureSnapshot:
        return await pressure_service.snapshot()

    return router


__all__ = ["RUNTIME_BACKPRESSURE_ROUTE_ID", "create_backpressure_router"]
