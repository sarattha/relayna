"""Incident Inbox orchestration and its approved Studio-only routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ._incident_groups import _dimensions, _group_id, _resolve, _summarize, _timestamp
from ._incident_store import _IncidentStore
from .database import StudioDatabase
from .federation import StudioFederationError, StudioFederationService
from .registry import ServiceStatus


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    status: Literal["open", "acknowledged", "resolved"] | None = None
    owner: str | None = Field(default=None, max_length=320)
    note: str = Field(default="", max_length=4000)


class _Merge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    target_id: str = Field(min_length=1, max_length=64)
    target_revision: int = Field(ge=0)
    note: str = Field(default="", max_length=4000)


class _Split(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    failure_ids: list[str] = Field(min_length=1, max_length=1000)
    note: str = Field(default="", max_length=4000)


class _IncidentService:
    def __init__(self, federation: StudioFederationService, store: _IncidentStore, *, page_budget: int = 20) -> None:
        self.federation = federation
        self.store = store
        self.page_budget = page_budget

    async def scan(self, environment: str | None = None, service_id: str | None = None) -> dict[str, Any]:
        services = [
            service
            for service in await self.federation.registry_service.list_services()
            if service.status != ServiceStatus.DISABLED
            and (not environment or service.environment == environment)
            and (not service_id or service.service_id == service_id)
        ]
        semaphore = asyncio.Semaphore(min(8, max(1, self.federation.search_max_concurrency)))

        async def read(service: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            items: dict[str, dict[str, Any]] = {}
            coverage: dict[str, Any] = {
                "service_id": service.service_id,
                "environment": service.environment,
                "status": "truncated",
                "observed_failures": 0,
                "pages": 0,
                "detail": None,
            }
            cursor = None
            cursors: set[str] = set()
            try:
                # Includes semaphore wait so a large registry cannot multiply
                # the browser's latency budget by its service count.
                async with asyncio.timeout(12), semaphore:
                    for _ in range(self.page_budget):
                        async with asyncio.timeout(4):
                            payload = await self.federation.list_failed_tasks(
                                service_id=service.service_id,
                                cursor=cursor,
                                limit=50,
                            )
                        page = payload.get("items")
                        if not isinstance(page, list) or payload.get("errors"):
                            raise ValueError("Failure feed returned incomplete or malformed evidence.")
                        coverage["pages"] += 1
                        for item in page:
                            if (
                                not isinstance(item, dict)
                                or not isinstance(item.get("failure_id"), str)
                                or not item["failure_id"]
                            ):
                                raise ValueError("Failure feed returned a record without a failure ID.")
                            if any(
                                item.get(key) is not None and not isinstance(item[key], str)
                                for key in (
                                    "task_id",
                                    "queue_name",
                                    "source_queue_name",
                                    "stage",
                                    "error_type",
                                    "error_message",
                                    "failed_at",
                                )
                            ):
                                raise ValueError("Failure feed returned malformed evidence fields.")
                            # Keep only triage evidence; never copy bodies or payloads.
                            items[item["failure_id"]] = {
                                "failure_id": item["failure_id"],
                                "service_id": service.service_id,
                                "environment": service.environment,
                                **{
                                    key: item.get(key)
                                    for key in (
                                        "task_id",
                                        "queue_name",
                                        "source_queue_name",
                                        "stage",
                                        "error_type",
                                        "error_message",
                                        "failed_at",
                                    )
                                },
                            }
                        following = payload.get("next_cursor")
                        if following is None or following == "":
                            coverage["status"] = "complete"
                            break
                        if not isinstance(following, str) or following in cursors:
                            raise ValueError("Failure feed pagination did not advance.")
                        cursors.add(following)
                        cursor = following
                    if coverage["status"] != "complete":
                        coverage["detail"] = f"Scan budget reached ({self.page_budget} pages of up to 50 failures)."
            except (StudioFederationError, TimeoutError, ValueError) as exc:
                coverage["status"] = "unavailable"
                coverage["detail"] = str(exc) or "Failure scan timed out."
            coverage["observed_failures"] = len(items)
            return list(items.values()), coverage

        results = await asyncio.gather(*(read(service) for service in services))
        evidence = [item for items, _ in results for item in items]
        coverage = [item for _, item in results]
        return {
            "evidence": evidence,
            "coverage": coverage,
            "complete": all(item["status"] == "complete" for item in coverage),
            "observed_failures": len(evidence),
            "scanned_at": datetime.now(UTC).isoformat(),
            "count_scope": "bounded_live_scan",
        }

    async def build(self, environment: str | None = None, service_id: str | None = None) -> dict[str, Any]:
        scan = await self.scan(environment, service_id)
        discoveries = {}
        natural_ids = {}
        for item in scan["evidence"]:
            dims = _dimensions(item["environment"], item["service_id"], item)
            natural_id = _group_id(dims)
            discoveries[natural_id] = dims
            natural_ids[(item["environment"], item["service_id"], item["failure_id"])] = natural_id
        states, overrides = await self.store.snapshot(discoveries, environment=environment, service_id=service_id)
        manual_targets = {
            _resolve(key, states)
            for key, state in states.items()
            if state.get("merged_into") or key != _group_id(state["dimensions"])
        }
        for key, state in states.items():
            state["grouping"] = "manual" if key in manual_targets else "automatic"
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in scan["evidence"]:
            key = (item["environment"], item["service_id"], item["failure_id"])
            incident_id = _resolve(overrides.get(key, natural_ids[key]), states)
            groups.setdefault(incident_id, []).append(item)
        now = _timestamp(scan["scanned_at"])
        assert now is not None
        for items in groups.values():
            items.sort(
                key=lambda item: (
                    _timestamp(item.get("failed_at")) or datetime.min.replace(tzinfo=UTC),
                    item["failure_id"],
                ),
                reverse=True,
            )
        return {**scan, "states": states, "groups": groups, "natural_ids": natural_ids, "now": now}

    async def detail(self, incident_id: str) -> dict[str, Any]:
        state = await self.store.get(incident_id)
        snapshot = await self.build(state["environment"], state["service_id"])
        state = snapshot["states"][incident_id]
        evidence = snapshot["groups"].get(incident_id, [])
        return {
            **_summarize(state, evidence, snapshot["now"]),
            "evidence": evidence,
            "history": await self.store.history(incident_id),
            "history_limit": 100,
            **{key: snapshot[key] for key in ("coverage", "complete", "scanned_at", "count_scope")},
        }


def _create_incident_router(federation: StudioFederationService, database: StudioDatabase | None) -> APIRouter:
    router = APIRouter()
    service = _IncidentService(federation, _IncidentStore(database)) if database is not None else None

    def ready() -> _IncidentService:
        if service is None:
            raise HTTPException(503, "Incident Inbox requires Studio PostgreSQL persistence.")
        return service

    @router.get("/studio/incidents")
    async def list_incidents(
        environment: str | None = None,
        status: Literal["active", "all", "open", "acknowledged", "resolved"] = "active",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        snapshot = await ready().build(environment)
        items = [
            _summarize(state, snapshot["groups"].get(incident_id, []), snapshot["now"])
            for incident_id, state in snapshot["states"].items()
            if not state["merged_into"]
            and (status == "all" or status == "active" and state["status"] != "resolved" or state["status"] == status)
        ]
        items.sort(key=lambda item: (item["failure_count"], item["last_seen"] or "", item["incident_id"]), reverse=True)
        return {
            "items": items[offset : offset + limit],
            "total_incidents": len(items),
            "next_offset": offset + limit if offset + limit < len(items) else None,
            **{
                key: snapshot[key] for key in ("coverage", "complete", "observed_failures", "scanned_at", "count_scope")
            },
        }

    @router.get("/studio/incidents/{incident_id}")
    async def incident_detail(incident_id: str) -> dict[str, Any]:
        return await ready().detail(incident_id)

    @router.post("/studio/incidents/{incident_id}")
    async def update_incident(incident_id: str, payload: _Change) -> dict[str, Any]:
        changes = payload.model_dump(include={"owner", "status"}, exclude_unset=True)
        if "status" in changes and changes["status"] is None:
            raise HTTPException(422, "Status cannot be null.")
        if "owner" in changes:
            changes["owner"] = (changes["owner"] or "").strip() or None
        return await ready().store.change(incident_id, payload.revision, changes, payload.note)

    @router.post("/studio/incidents/{incident_id}/split")
    async def split_incident(incident_id: str, payload: _Split) -> dict[str, Any]:
        handler = ready()
        state = await handler.store.get(incident_id)
        snapshot = await handler.build(state["environment"], state["service_id"])
        observed = {fid: natural for (_env, _sid, fid), natural in snapshot["natural_ids"].items()}
        return await handler.store.split(incident_id, payload.revision, payload.failure_ids, observed, payload.note)

    @router.post("/studio/incidents/{incident_id}/merge")
    async def merge_incident(incident_id: str, payload: _Merge) -> dict[str, Any]:
        return await ready().store.merge(
            incident_id, payload.revision, payload.target_id, payload.target_revision, payload.note
        )

    return router
