from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..topology.workflow import SharedStatusWorkflowTopology
from ..topology.workflow_contract import serialize_workflow_stage
from ..topology.workflow_graph import export_workflow_graph
from .capabilities_routes import WORKFLOW_CAPABILITY_ROUTE_IDS, WORKFLOW_STAGES_ROUTE_ID, WORKFLOW_TOPOLOGY_ROUTE_ID


def create_workflow_router(
    *,
    topology: SharedStatusWorkflowTopology,
    base_path: str = "/workflow",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{base_path}/topology")
    async def topology_graph() -> JSONResponse:
        return JSONResponse(export_workflow_graph(topology))

    @router.get(f"{base_path}/stages")
    async def stages() -> JSONResponse:
        return JSONResponse(
            {
                "stages": [
                    serialize_workflow_stage(
                        topology.workflow_stage(stage),
                        queue_arguments=topology.workflow_queue_arguments(stage),
                    )
                    for stage in topology.workflow_stage_names()
                ]
            }
        )

    return router


__all__ = [
    "WORKFLOW_CAPABILITY_ROUTE_IDS",
    "WORKFLOW_STAGES_ROUTE_ID",
    "WORKFLOW_TOPOLOGY_ROUTE_ID",
    "create_workflow_router",
]
