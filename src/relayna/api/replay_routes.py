from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from ..contracts import ContractAliasConfig
from ..dlq import (
    DLQRecordState,
    DLQReplayConflict,
    DLQService,
    FailedTaskInvestigationRequest,
    FailedTaskInvestigationStatus,
    FailedTaskRetryRejected,
    FailedTaskRetryRequest,
)
from .capabilities_routes import (
    BROKER_DLQ_MESSAGES_ROUTE_ID,
    DLQ_CAPABILITY_ROUTE_IDS,
    DLQ_MESSAGE_DETAIL_ROUTE_ID,
    DLQ_MESSAGES_ROUTE_ID,
    DLQ_QUEUES_ROUTE_ID,
    DLQ_REPLAY_ROUTE_ID,
    FAILED_TASK_DELETE_ROUTE_ID,
    FAILED_TASK_DETAIL_ROUTE_ID,
    FAILED_TASK_INVESTIGATE_ROUTE_ID,
    FAILED_TASK_RETRY_ROUTE_ID,
    FAILED_TASK_UNINVESTIGATE_ROUTE_ID,
    FAILED_TASKS_ROUTE_ID,
)
from .fastapi_lifespan import http_field_name, public_dlq_payload


def create_dlq_router(
    *,
    dlq_service: DLQService,
    queues_path: str = "/dlq/queues",
    broker_queues_path: str = "/broker/dlq/queues",
    broker_messages_path: str = "/broker/dlq/messages",
    messages_path: str = "/dlq/messages",
    message_path: str = "/dlq/messages/{dlq_id}",
    replay_path: str = "/dlq/messages/{dlq_id}/replay",
    failed_tasks_path: str = "/failed-tasks",
    failed_task_path: str = "/failed-tasks/{failure_id}",
    failed_task_investigate_path: str = "/failed-tasks/{failure_id}/mark-investigated",
    failed_task_uninvestigate_path: str = "/failed-tasks/{failure_id}/mark-uninvestigated",
    failed_task_retry_path: str = "/failed-tasks/{failure_id}/retry",
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

    if broker_dlq_queue_names is not None and dlq_service.supports_broker_message_reads:

        @router.get(broker_messages_path)
        async def broker_messages(
            queue_name: str | None = None,
            task_id: str | None = Query(default=None, alias=task_http_name),
            limit: int = Query(default=50, ge=1, le=200),
        ) -> JSONResponse:
            try:
                payload = await dlq_service.list_broker_messages(
                    broker_dlq_queue_names,
                    queue_name=queue_name,
                    task_id=task_id,
                    limit=limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return JSONResponse(public_dlq_payload(payload.model_dump(mode="json"), alias_config))

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

    @router.get(failed_tasks_path)
    async def failed_tasks(
        service_name: str | None = None,
        queue_name: str | None = None,
        dlq_name: str | None = None,
        error_type: str | None = None,
        status: str | None = None,
        task_id: str | None = Query(default=None, alias=task_http_name),
        worker_id: str | None = None,
        investigation_status: FailedTaskInvestigationStatus | None = None,
        failed_from: datetime | None = None,
        failed_to: datetime | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        payload = await dlq_service.list_failed_tasks(
            service_name=service_name,
            queue_name=queue_name,
            dlq_name=dlq_name,
            error_type=error_type,
            status=status,
            task_id=task_id,
            worker_id=worker_id,
            investigation_status=investigation_status,
            failed_from=failed_from,
            failed_to=failed_to,
            cursor=cursor,
            limit=limit,
        )
        return JSONResponse(public_dlq_payload(payload.model_dump(mode="json"), alias_config))

    @router.get(failed_task_path)
    async def failed_task_detail(failure_id: str) -> JSONResponse:
        result = await dlq_service.get_failed_task_detail(failure_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No failed task found for failure_id '{failure_id}'.")
        return JSONResponse(public_dlq_payload(result.model_dump(mode="json"), alias_config))

    @router.post(failed_task_investigate_path)
    async def mark_failed_task_investigated(
        failure_id: str, request: FailedTaskInvestigationRequest | None = None
    ) -> JSONResponse:
        result = await dlq_service.mark_failed_task_investigated(
            failure_id,
            investigated_by=request.investigated_by if request else None,
            note=request.note if request else None,
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"No failed task found for failure_id '{failure_id}'.")
        return JSONResponse(public_dlq_payload(result.model_dump(mode="json"), alias_config))

    @router.post(failed_task_uninvestigate_path)
    async def mark_failed_task_uninvestigated(failure_id: str) -> JSONResponse:
        result = await dlq_service.mark_failed_task_uninvestigated(failure_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No failed task found for failure_id '{failure_id}'.")
        return JSONResponse(public_dlq_payload(result.model_dump(mode="json"), alias_config))

    @router.post(failed_task_retry_path)
    async def retry_failed_task(failure_id: str, request: FailedTaskRetryRequest | None = None) -> JSONResponse:
        try:
            result = await dlq_service.retry_failed_task(failure_id, request)
        except FailedTaskRetryRejected as exc:
            raise HTTPException(status_code=409, detail={"error": exc.code, "message": str(exc)}) from exc
        if result is None:
            raise HTTPException(status_code=404, detail=f"No failed task found for failure_id '{failure_id}'.")
        return JSONResponse(result.model_dump(mode="json"))

    @router.delete(failed_task_path)
    async def delete_failed_task(failure_id: str) -> Response:
        deleted = await dlq_service.delete_failed_task(failure_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No failed task found for failure_id '{failure_id}'.")
        return Response(status_code=204)

    return router


def create_replay_router(*args, **kwargs):
    return create_dlq_router(*args, **kwargs)


__all__ = [
    "DLQ_CAPABILITY_ROUTE_IDS",
    "DLQ_MESSAGES_ROUTE_ID",
    "DLQ_MESSAGE_DETAIL_ROUTE_ID",
    "DLQ_QUEUES_ROUTE_ID",
    "DLQ_REPLAY_ROUTE_ID",
    "BROKER_DLQ_MESSAGES_ROUTE_ID",
    "FAILED_TASKS_ROUTE_ID",
    "FAILED_TASK_DELETE_ROUTE_ID",
    "FAILED_TASK_DETAIL_ROUTE_ID",
    "FAILED_TASK_INVESTIGATE_ROUTE_ID",
    "FAILED_TASK_RETRY_ROUTE_ID",
    "FAILED_TASK_UNINVESTIGATE_ROUTE_ID",
    "create_dlq_router",
    "create_replay_router",
]
