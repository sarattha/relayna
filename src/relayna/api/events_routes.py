from __future__ import annotations

from fastapi import APIRouter, Query

from ..observability import RedisServiceEventFeedStore, RelaynaServiceEventFeedResponse
from .capabilities_routes import EVENTS_CAPABILITY_ROUTE_IDS, EVENTS_FEED_ROUTE_ID


def create_events_router(
    *,
    service_event_store: RedisServiceEventFeedStore,
    feed_path: str = "/events/feed",
) -> APIRouter:
    router = APIRouter()

    @router.get(feed_path, response_model=RelaynaServiceEventFeedResponse)
    async def feed(
        after: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> RelaynaServiceEventFeedResponse:
        return await service_event_store.get_feed(after=after, limit=limit)

    return router


__all__ = ["EVENTS_CAPABILITY_ROUTE_IDS", "EVENTS_FEED_ROUTE_ID", "create_events_router"]
