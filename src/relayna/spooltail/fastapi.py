from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ..api.capabilities_routes import STATUS_CAPABILITY_ROUTE_IDS, relayna_version
from ..api.schemas import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata
from ..api.status_routes import _create_status_router
from ..status.stream import _sse_event
from .contracts import spooltail_task_event

if TYPE_CHECKING:
    from relayna_spooltail import SpooltailClient  # ty: ignore[unresolved-import]

_CAPABILITY_PATH = "/relayna/capabilities"
_HISTORY_FEATURE = "spooltail.task_history.v1"
_RFC3339 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)


class _TaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,255}$")
    payload: dict[str, Any]
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str | None = None
    task_type: str | None = None
    correlation_id: str | None = None
    spec_version: Literal["1.0"] = "1.0"

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value: Any) -> datetime:
        if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
            raise ValueError("created_at must be an RFC 3339 timestamp with a timezone")
        return datetime.fromisoformat(value.upper())


def create_spooltail_status_router(
    *,
    get_client: Callable[[], SpooltailClient],
    status_topic: str = "relayna.status",
    task_topic: str | None = None,
    service: str | None = None,
    cancel_topic: str | None = None,
    broker_version: str | None = None,
) -> APIRouter:
    """Serve broker-backed status and optionally accept tasks for a configured service."""
    if cancel_topic is not None and (not cancel_topic or not task_topic or cancel_topic in {task_topic, status_topic}):
        raise ValueError("Spooltail cancellation requires submission and a distinct cancellation topic")
    source = _SpooltailStatusSource(get_client, status_topic)
    router = _create_status_router(sse_stream=source, latest_status_store=source)

    @router.get("/relayna/broker")
    async def broker_stats() -> dict[str, Any]:
        stats = await get_client().stats()
        snapshot = {
            "broker": "spooltail",
            "payload_reads": stats.payload_reads,
            "recovered_frames": stats.recovered_frames,
        }
        if broker_version:
            snapshot["version"] = broker_version
        return snapshot

    @router.get("/history/{task_id}")
    async def task_history(task_id: str, after: str | None = None) -> dict[str, Any]:
        from relayna_spooltail import CursorGapError  # ty: ignore[unresolved-import]

        try:
            records = await get_client().history(status_topic, task_id, after=after)
        except CursorGapError as exc:
            raise HTTPException(status_code=410, detail={"earliest_event_id": exc.earliest_event_id}) from exc
        return {"task_id": task_id, "count": len(records), "events": [record.event for record in records]}

    @router.get(_CAPABILITY_PATH, response_model=CapabilityDocument)
    async def capabilities() -> CapabilityDocument:
        return CapabilityDocument(
            relayna_version=relayna_version(),
            topology_kind="spooltail",
            alias_config_summary=AliasConfigSummary(),
            supported_routes=list(STATUS_CAPABILITY_ROUTE_IDS),
            feature_flags=[_HISTORY_FEATURE, "spooltail.broker.v1"]
            + (["spooltail.cancel.v1"] if cancel_topic is not None else []),
            service_metadata=CapabilityServiceMetadata(
                service_title=service,
                capability_path=_CAPABILITY_PATH,
                discovery_source="live",
                compatibility="capabilities_v1",
            ),
        )

    if task_topic is not None:
        if not task_topic or task_topic == status_topic or not service:
            raise ValueError("Spooltail submission requires a distinct task topic and a configured service")

        @router.post("/tasks", status_code=202)
        async def submit_task(task: _TaskSubmission) -> dict[str, str]:
            if task.service is not None and task.service != service:
                raise HTTPException(status_code=422, detail="Task service does not match this endpoint")
            event = spooltail_task_event(
                task.model_dump(exclude_none=True) | {"service": service, "created_at": task.created_at.astimezone(UTC)}
            )
            await _publish_submission(
                get_client(), task_topic, event, timestamp_supplied="created_at" in task.model_fields_set
            )
            return {"task_id": task.task_id}

        if cancel_topic is not None:

            @router.post("/tasks/{task_id}/cancel", status_code=202)
            async def cancel_task(task_id: str) -> dict[str, str]:
                from grpc.aio import AioRpcError  # ty: ignore[unresolved-import]

                client = get_client()
                try:
                    status = await source.get_latest(task_id)
                    if status is not None and str(status.get("status")).lower() in {"completed", "failed", "cancelled"}:
                        raise HTTPException(status_code=409, detail="Task is already terminal")
                    if await client.latest(task_topic, task_id) is None:
                        raise HTTPException(status_code=404, detail="Task was not found")
                    await client.publish(cancel_topic, {"task_id": task_id, "event_id": "cancel"})
                except AioRpcError as exc:
                    raise HTTPException(status_code=503, detail="Spooltail could not confirm cancellation") from exc
                return {"task_id": task_id}

    return router


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
