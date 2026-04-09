from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ..contracts import ContractAliasConfig
from ..dlq import DLQRecordState, DLQReplayConflict, DLQService
from .capabilities_routes import (
    DLQ_CAPABILITY_ROUTE_IDS,
    DLQ_MESSAGE_DETAIL_ROUTE_ID,
    DLQ_MESSAGES_ROUTE_ID,
    DLQ_QUEUES_ROUTE_ID,
    DLQ_REPLAY_ROUTE_ID,
)
from .fastapi_lifespan import http_field_name, public_dlq_payload


def create_dlq_router(
    *,
    dlq_service: DLQService,
    queues_path: str = "/dlq/queues",
    broker_queues_path: str = "/broker/dlq/queues",
    messages_path: str = "/dlq/messages",
    message_path: str = "/dlq/messages/{dlq_id}",
    replay_path: str = "/dlq/messages/{dlq_id}/replay",
    broker_dlq_queue_names: Sequence[str] | None = None,
    alias_config: ContractAliasConfig | None = None,
) -> APIRouter:
    router = APIRouter()
    task_http_name = http_field_name("task_id", alias_config)

    @router.get(queues_path)
    async def queues() -> JSONResponse:
        items = await dlq_service.get_queue_summaries()
        return JSONResponse({"queues": [item.model_dump(mode="json") for item in items]})

    if broker_dlq_queue_names is not None:

        @router.get(broker_queues_path)
        async def broker_queues() -> JSONResponse:
            items = await dlq_service.get_broker_queue_summaries(broker_dlq_queue_names)
            return JSONResponse({"queues": [item.model_dump(mode="json") for item in items]})

    @router.get(messages_path)
    async def messages(
        queue_name: str | None = None,
        task_id: str | None = Query(default=None, alias=task_http_name),
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: DLQRecordState | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        payload = await dlq_service.list_messages(
            queue_name=queue_name,
            task_id=task_id,
            reason=reason,
            source_queue_name=source_queue_name,
            state=state,
            cursor=cursor,
            limit=limit,
        )
        return JSONResponse(public_dlq_payload(payload.model_dump(mode="json"), alias_config))

    @router.post(replay_path)
    async def replay(dlq_id: str, force: bool = Query(default=False)) -> JSONResponse:
        try:
            result = await dlq_service.replay_message(dlq_id, force=force)
        except DLQReplayConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail=f"No DLQ message found for dlq_id '{dlq_id}'.")
        return JSONResponse(result.model_dump(mode="json"))

    @router.get(message_path)
    async def message_detail(dlq_id: str) -> JSONResponse:
        result = await dlq_service.get_message_detail(dlq_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No DLQ message found for dlq_id '{dlq_id}'.")
        return JSONResponse(public_dlq_payload(result.model_dump(mode="json"), alias_config))

    return router


def create_replay_router(*args, **kwargs):
    return create_dlq_router(*args, **kwargs)


__all__ = [
    "DLQ_CAPABILITY_ROUTE_IDS",
    "DLQ_MESSAGES_ROUTE_ID",
    "DLQ_MESSAGE_DETAIL_ROUTE_ID",
    "DLQ_QUEUES_ROUTE_ID",
    "DLQ_REPLAY_ROUTE_ID",
    "create_dlq_router",
    "create_replay_router",
]
