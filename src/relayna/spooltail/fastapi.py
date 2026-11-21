from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from ..api.status_routes import _create_status_router
from ..status.stream import _sse_event

if TYPE_CHECKING:
    from relayna_spooltail import SpooltailClient  # ty: ignore[unresolved-import]


def create_spooltail_status_router(
    *,
    get_client: Callable[[], SpooltailClient],
    status_topic: str = "relayna.status",
) -> APIRouter:
    """Serve Relayna's latest-status and SSE routes with an application-owned native client."""
    source = _SpooltailStatusSource(get_client, status_topic)
    return _create_status_router(sse_stream=source, latest_status_store=source)


class _SpooltailStatusSource:
    def __init__(self, get_client: Callable[[], SpooltailClient], topic: str) -> None:
        self._get_client = get_client
        self._topic = topic

    async def get_latest(self, task_id: str) -> dict[str, Any] | None:
        record = await self._get_client().latest(self._topic, task_id)
        return record.event if record is not None else None

    async def stream(self, task_id: str, *, last_event_id: str | None = None) -> AsyncIterator[bytes]:
        from relayna_spooltail import CursorGapError, SubscriptionError  # ty: ignore[unresolved-import]

        subscription = self._get_client().subscribe(self._topic, task_id, after=last_event_id)
        try:
            async with aclosing(subscription):
                async for delivery in subscription:
                    yield _sse_event(delivery.data, event_type=delivery.kind)
        except CursorGapError as exc:
            yield _sse_event({"earliest_event_id": exc.earliest_event_id}, event_type="gap")
        except SubscriptionError as exc:
            yield _sse_event({"error": str(exc)}, event_type="error")
