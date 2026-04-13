from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..contracts import ContractAliasConfig, public_output_aliases
from ..status.history import StreamHistoryReader, StreamOffset
from ..status.store import RedisStatusStore
from ..status.stream import EventAdapter, SSEStatusStream
from .capabilities_routes import (
    STATUS_CAPABILITY_ROUTE_IDS,
    STATUS_EVENTS_ROUTE_ID,
    STATUS_HISTORY_ROUTE_ID,
    STATUS_LATEST_ROUTE_ID,
)
from .fastapi_lifespan import http_field_name, payload_field_name, sse_response

HISTORY_START_OFFSET_QUERY = Query(default="first")


def create_status_router(
    *,
    sse_stream: SSEStatusStream,
    history_reader: StreamHistoryReader | None = None,
    latest_status_store: RedisStatusStore | None = None,
    latest_output_adapter: EventAdapter | None = None,
    events_path: str = "/events/{task_id}",
    history_path: str = "/history",
    status_path: str = "/status/{task_id}",
    require_stream: bool = True,
    alias_config: ContractAliasConfig | None = None,
    latest_retry_attempts: int = 5,
    latest_retry_delay_seconds: float = 0.05,
) -> APIRouter:
    router = APIRouter()
    task_http_name = http_field_name("task_id", alias_config)
    resolved_events_path = events_path.replace("{task_id}", "{" + task_http_name + "}")
    resolved_status_path = status_path.replace("{task_id}", "{" + task_http_name + "}")
    task_response_name = payload_field_name("task_id", alias_config)

    @router.get(resolved_events_path)
    async def events(
        task_id: str = Path(alias=task_http_name),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        return sse_response(sse_stream.stream(task_id, last_event_id=last_event_id))

    if latest_status_store is not None:
        output_adapter = latest_output_adapter or (lambda data: data)

        @router.get(resolved_status_path)
        async def latest_status(task_id: str = Path(alias=task_http_name)) -> JSONResponse:
            event = None
            for attempt in range(max(1, latest_retry_attempts)):
                event = await latest_status_store.get_latest(task_id)
                if event is not None and _status_value(event) in {"completed", "failed"}:
                    break
                if event is not None and attempt == latest_retry_attempts - 1:
                    break
                if event is None and attempt == latest_retry_attempts - 1:
                    break
                await asyncio.sleep(latest_retry_delay_seconds)
            if event is None:
                raise HTTPException(status_code=404, detail=f"No status found for {task_response_name} '{task_id}'.")
            return JSONResponse(
                {
                    task_response_name: task_id,
                    "event": public_output_aliases(output_adapter(dict(event)), alias_config),
                }
            )

    if history_reader is not None:

        @router.get(history_path)
        async def history(
            task_id: str | None = Query(default=None, alias=task_http_name),
            start_offset: StreamOffset = HISTORY_START_OFFSET_QUERY,
            max_seconds: float | None = Query(default=None),
            max_scan: int | None = Query(default=None),
        ) -> JSONResponse:
            try:
                if all(param is None for param in [max_seconds, max_scan, task_id]):
                    raise HTTPException(
                        status_code=422,
                        detail="Set either max_seconds, max_scan, or task_id to limit the scope of the history query.",
                    )

                events = await history_reader.replay(
                    task_id=task_id,
                    start_offset=start_offset,
                    max_seconds=max_seconds,
                    max_scan=max_scan,
                    require_stream=require_stream,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            payload: dict[str, Any] = {
                "count": len(events),
                "events": [public_output_aliases(dict(event), alias_config) for event in events],
            }
            if task_id is not None:
                payload[task_response_name] = task_id
            return JSONResponse(payload)

    return router


def _status_value(data: dict[str, Any]) -> str | None:
    status = data.get("status")
    if isinstance(status, str):
        return status.lower()
    if status is None:
        return None
    return str(status).lower()


__all__ = [
    "STATUS_CAPABILITY_ROUTE_IDS",
    "STATUS_EVENTS_ROUTE_ID",
    "STATUS_HISTORY_ROUTE_ID",
    "STATUS_LATEST_ROUTE_ID",
    "create_status_router",
    "sse_response",
]
