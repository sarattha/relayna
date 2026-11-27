from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from relayna_spooltail import SpooltailClient  # ty: ignore[unresolved-import]


async def _publish_submission(
    client: SpooltailClient, topic: str, event: dict[str, Any], *, timestamp_supplied: bool
) -> None:
    from grpc import StatusCode  # ty: ignore[unresolved-import]
    from grpc.aio import AioRpcError  # ty: ignore[unresolved-import]
    from relayna_spooltail import ConflictError  # ty: ignore[unresolved-import]

    try:
        try:
            await client.publish(topic, event)
            return
        except ConflictError:
            # The broker atomically chooses the first work event, including across API processes.
            committed = await client.latest(topic, event["task_id"])
            if committed is not None:
                candidate = event if timestamp_supplied else event | {"created_at": committed.event.get("created_at")}
                if json.dumps(candidate, sort_keys=True) == json.dumps(committed.event, sort_keys=True):
                    return
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Task payload must be valid JSON") from exc
    except AioRpcError as exc:
        if exc.code() == StatusCode.INVALID_ARGUMENT:
            raise HTTPException(status_code=422, detail="Spooltail rejected the task envelope") from exc
        raise HTTPException(status_code=503, detail="Spooltail could not confirm task submission") from exc

    raise HTTPException(status_code=409, detail="Task ID already has a different submission")
