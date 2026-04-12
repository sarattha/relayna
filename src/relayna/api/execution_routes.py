from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import JSONResponse

from ..contracts import ContractAliasConfig
from ..observability import ExecutionGraphService
from .capabilities_routes import EXECUTION_CAPABILITY_ROUTE_IDS, EXECUTION_GRAPH_ROUTE_ID
from .fastapi_lifespan import http_field_name, payload_field_name


def create_execution_router(
    *,
    execution_graph_service: ExecutionGraphService,
    graph_path: str = "/executions/{task_id}/graph",
    alias_config: ContractAliasConfig | None = None,
) -> APIRouter:
    router = APIRouter()
    task_http_name = http_field_name("task_id", alias_config)
    task_response_name = payload_field_name("task_id", alias_config)
    resolved_graph_path = graph_path.replace("{task_id}", "{" + task_http_name + "}")

    @router.get(resolved_graph_path)
    async def graph(task_id: str = Path(alias=task_http_name)) -> JSONResponse:
        payload = await execution_graph_service.get_graph(task_id)
        if payload is None:
            raise HTTPException(
                status_code=404, detail=f"No execution graph found for {task_response_name} '{task_id}'."
            )
        data = payload.model_dump(mode="json")
        if task_response_name != "task_id":
            data[task_response_name] = data.pop("task_id")
        return JSONResponse(data)

    return router


__all__ = ["EXECUTION_CAPABILITY_ROUTE_IDS", "EXECUTION_GRAPH_ROUTE_ID", "create_execution_router"]
