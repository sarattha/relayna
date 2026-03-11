from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from .history import StreamHistoryReader, StreamOffset
from .sse import SSEStatusStream


def sse_response(stream: AsyncIterator[bytes]) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


def create_status_router(
    *,
    sse_stream: SSEStatusStream,
    history_reader: StreamHistoryReader | None = None,
    events_path: str = "/events/{task_id}",
    history_path: str = "/history",
    require_stream: bool = True,
) -> APIRouter:
    router = APIRouter()

    @router.get(events_path)
    async def events(task_id: str) -> StreamingResponse:
        return sse_response(sse_stream.stream(task_id))

    if history_reader is not None:

        @router.get(history_path)
        async def history(
            task_id: str | None = None,
            start_offset: StreamOffset = Query(default="first"),
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
            return JSONResponse({"task_id": task_id, "count": len(events), "events": events})

    return router
