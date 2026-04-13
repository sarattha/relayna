from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from relayna.api import (
    DLQ_MESSAGES_ROUTE_ID,
    EXECUTION_GRAPH_ROUTE_ID,
    STATUS_HISTORY_ROUTE_ID,
    STATUS_LATEST_ROUTE_ID,
    WORKFLOW_TOPOLOGY_ROUTE_ID,
    CapabilityDocument,
)

from .identity import (
    JoinKind,
    JoinMode,
    StudioJoinWarning,
    StudioTaskJoin,
    StudioTaskPointer,
    StudioTaskRef,
    build_task_pointer,
    build_task_ref,
)
from .registry import ServiceNotFoundError, ServiceRecord, ServiceRegistryService, ServiceStatus

SERVICE_STATUS_PATH = "/status/{task_id}"
SERVICE_HISTORY_PATH = "/history"
SERVICE_WORKFLOW_TOPOLOGY_PATH = "/workflow/topology"
SERVICE_DLQ_MESSAGES_PATH = "/dlq/messages"
SERVICE_EXECUTION_GRAPH_PATH = "/executions/{task_id}/graph"
TASK_SEARCH_QUERY = Query(min_length=1)
JOIN_MODE_QUERY = Query(default=JoinMode.NONE)


def _default_async_client_factory(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_seconds)


class FederatedError(BaseModel):
    detail: str
    code: str
    service_id: str | None = None
    upstream_status: int | None = None
    retryable: bool = False


class StudioTaskSearchItem(BaseModel):
    service_id: str
    task_id: str
    task_ref: StudioTaskRef
    service_name: str
    environment: str
    latest_status: dict[str, Any]
    detail_path: str


class StudioJoinedTaskSearchItem(StudioTaskSearchItem):
    join_kind: JoinKind
    matched_value: str


class StudioTaskSearchResponse(BaseModel):
    count: int
    items: list[StudioTaskSearchItem] = Field(default_factory=list)
    joined_count: int = 0
    joined_items: list[StudioJoinedTaskSearchItem] = Field(default_factory=list)
    join_warnings: list[StudioJoinWarning] = Field(default_factory=list)
    errors: list[FederatedError] = Field(default_factory=list)
    scanned_services: list[str] = Field(default_factory=list)


class StudioTaskDetailResponse(BaseModel):
    service: ServiceRecord
    service_id: str
    task_id: str
    task_ref: StudioTaskRef
    latest_status: dict[str, Any] | None = None
    history: dict[str, Any] | None = None
    dlq_messages: dict[str, Any] | None = None
    execution_graph: dict[str, Any] | None = None
    joined_refs: list[StudioTaskJoin] = Field(default_factory=list)
    join_warnings: list[StudioJoinWarning] = Field(default_factory=list)
    errors: list[FederatedError] = Field(default_factory=list)


@dataclass(slots=True)
class _SearchTaskMatch:
    service: ServiceRecord
    item: StudioTaskSearchItem


@dataclass(slots=True)
class _TaskDetailBundle:
    service: ServiceRecord
    task_id: str
    task_ref: StudioTaskRef
    latest_status: dict[str, Any] | None
    history: dict[str, Any] | None
    dlq_messages: dict[str, Any] | None
    execution_graph: dict[str, Any] | None
    errors: list[FederatedError]


class StudioFederationError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        code: str,
        service_id: str | None = None,
        upstream_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.service_id = service_id
        self.upstream_status = upstream_status
        self.retryable = retryable

    def to_model(self) -> FederatedError:
        return FederatedError(
            detail=self.detail,
            code=self.code,
            service_id=self.service_id,
            upstream_status=self.upstream_status,
            retryable=self.retryable,
        )

    def to_response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.to_model().model_dump(mode="json"))


@dataclass(slots=True)
class StudioFederationService:
    registry_service: ServiceRegistryService
    http_client: httpx.AsyncClient
    search_max_concurrency: int = 8

    async def get_service_status(self, service_id: str, task_id: str) -> dict[str, Any]:
        service = await self._get_proxyable_service(service_id)
        return await self._fetch_status(service, task_id)

    async def get_service_history(
        self,
        service_id: str,
        *,
        task_id: str | None = None,
        start_offset: str = "first",
        max_seconds: float | None = None,
        max_scan: int | None = None,
    ) -> dict[str, Any]:
        service = await self._get_proxyable_service(service_id)
        return await self._fetch_history(
            service,
            task_id=task_id,
            start_offset=start_offset,
            max_seconds=max_seconds,
            max_scan=max_scan,
        )

    async def get_service_workflow_topology(self, service_id: str) -> dict[str, Any]:
        service = await self._get_proxyable_service(service_id)
        return await self._fetch_workflow_topology(service)

    async def get_service_dlq_messages(
        self,
        service_id: str,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        service = await self._get_proxyable_service(service_id)
        return await self._fetch_dlq_messages(
            service,
            queue_name=queue_name,
            task_id=task_id,
            reason=reason,
            source_queue_name=source_queue_name,
            state=state,
            cursor=cursor,
            limit=limit,
        )

    async def get_service_execution_graph(self, service_id: str, task_id: str) -> dict[str, Any]:
        service = await self._get_proxyable_service(service_id)
        return await self._fetch_execution_graph(service, task_id)

    async def search_tasks(self, task_id: str, *, join: JoinMode = JoinMode.NONE) -> StudioTaskSearchResponse:
        normalized_task_id = task_id.strip()
        services = await self._list_queryable_services()
        exact_matches, errors = await self._find_task_matches(
            normalized_task_id,
            services,
            include_errors=True,
        )
        items = [match.item for match in exact_matches]
        joined_items: list[StudioJoinedTaskSearchItem] = []
        join_warnings: list[StudioJoinWarning] = []
        if join != JoinMode.NONE:
            joined_items, join_warnings = await self._build_search_joins(exact_matches, services, join=join)
        scanned_services = [service.service_id for service in services]
        items.sort(key=lambda item: (item.environment, item.service_name.lower(), item.service_id))
        joined_items.sort(key=lambda item: (item.environment, item.service_name.lower(), item.service_id))
        return StudioTaskSearchResponse(
            count=len(items),
            items=items,
            joined_count=len(joined_items),
            joined_items=joined_items,
            join_warnings=join_warnings,
            errors=errors,
            scanned_services=scanned_services,
        )

    async def get_task_detail(
        self,
        service_id: str,
        task_id: str,
        *,
        join: JoinMode = JoinMode.NONE,
    ) -> StudioTaskDetailResponse:
        normalized_task_id = task_id.strip()
        service = await self._get_proxyable_service(service_id)
        bundle = await self._build_task_detail_bundle(service, normalized_task_id)
        joined_refs: list[StudioTaskJoin] = []
        join_warnings: list[StudioJoinWarning] = []
        if join != JoinMode.NONE:
            joined_refs, join_warnings = await self._build_detail_joins(
                bundle,
                await self._list_queryable_services(),
                join=join,
            )
        return StudioTaskDetailResponse(
            service=service,
            service_id=service.service_id,
            task_id=normalized_task_id,
            task_ref=bundle.task_ref,
            latest_status=bundle.latest_status,
            history=bundle.history,
            dlq_messages=bundle.dlq_messages,
            execution_graph=bundle.execution_graph,
            joined_refs=joined_refs,
            join_warnings=join_warnings,
            errors=bundle.errors,
        )

    async def _search_latest_status(self, service: ServiceRecord, task_id: str) -> dict[str, Any] | None:
        try:
            return await self._fetch_status(service, task_id)
        except StudioFederationError as exc:
            if exc.code != "unsupported_route":
                raise
        history = await self._fetch_history(service, task_id=task_id, start_offset="last", max_scan=1)
        events = history.get("events")
        if not isinstance(events, list) or not events:
            return None
        return self._normalize_status_payload(
            service,
            {
                "service_id": service.service_id,
                "task_id": task_id,
                "event": _latest_history_event(events),
            },
            requested_task_id=task_id,
        )

    async def _list_queryable_services(self) -> list[ServiceRecord]:
        return [
            service
            for service in await self.registry_service.list_services()
            if service.status != ServiceStatus.DISABLED
        ]

    async def _find_task_matches(
        self,
        task_id: str,
        services: Iterable[ServiceRecord],
        *,
        include_errors: bool,
    ) -> tuple[list[_SearchTaskMatch], list[FederatedError]]:
        normalized_task_id = task_id.strip()
        semaphore = asyncio.Semaphore(max(1, self.search_max_concurrency))

        async def search_service(service: ServiceRecord) -> tuple[_SearchTaskMatch | None, FederatedError | None]:
            async with semaphore:
                try:
                    latest_status = await self._search_latest_status(service, normalized_task_id)
                except StudioFederationError as exc:
                    if exc.code == "upstream_not_found":
                        return None, None
                    if not include_errors:
                        return None, None
                    return None, exc.to_model()
                if latest_status is None:
                    return None, None
                task_ref = _task_ref_from_payload(latest_status)
                resolved_task_id = _payload_task_id(latest_status) or normalized_task_id
                if task_ref is None:
                    task_ref = build_task_ref(service_id=service.service_id, task_id=resolved_task_id)
                return (
                    _SearchTaskMatch(
                        service=service,
                        item=StudioTaskSearchItem(
                            service_id=service.service_id,
                            task_id=resolved_task_id,
                            task_ref=task_ref,
                            service_name=service.name,
                            environment=service.environment,
                            latest_status=latest_status,
                            detail_path=f"/studio/tasks/{service.service_id}/{resolved_task_id}",
                        ),
                    ),
                    None,
                )

        results = await asyncio.gather(*(search_service(service) for service in services))
        matches = [match for match, _ in results if match is not None]
        errors = [error for _, error in results if error is not None]
        return matches, errors

    async def _build_task_detail_bundle(self, service: ServiceRecord, task_id: str) -> _TaskDetailBundle:
        normalized_task_id = task_id.strip()
        latest_task = asyncio.create_task(self._capture(self._fetch_status(service, normalized_task_id)))
        history_task = asyncio.create_task(
            self._capture(self._fetch_history(service, task_id=normalized_task_id, max_scan=200))
        )
        dlq_task = asyncio.create_task(
            self._capture(self._fetch_dlq_messages(service, task_id=normalized_task_id, limit=50))
        )
        graph_task = asyncio.create_task(self._capture(self._fetch_execution_graph(service, normalized_task_id)))

        latest_result, history_result, dlq_result, graph_result = await asyncio.gather(
            latest_task,
            history_task,
            dlq_task,
            graph_task,
        )

        errors: list[FederatedError] = []
        latest_status, latest_error = latest_result
        history, history_error = history_result
        dlq_messages, dlq_error = dlq_result
        execution_graph, graph_error = graph_result

        if latest_status is None and latest_error is not None and latest_error.code == "unsupported_route" and history:
            events = history.get("events")
            if isinstance(events, list) and events:
                latest_status = self._normalize_status_payload(
                    service,
                    {
                        "service_id": service.service_id,
                        "task_id": normalized_task_id,
                        "event": _latest_history_event(events),
                    },
                    requested_task_id=normalized_task_id,
                )
            else:
                errors.append(latest_error.to_model())
        elif latest_error is not None:
            errors.append(latest_error.to_model())

        for payload, error in ((history, history_error), (dlq_messages, dlq_error), (execution_graph, graph_error)):
            if payload is None and error is not None:
                errors.append(error.to_model())

        task_ref = self._build_primary_task_ref(
            service,
            task_id=normalized_task_id,
            latest_status=latest_status,
            history=history,
            execution_graph=execution_graph,
        )

        if latest_status is not None:
            latest_status = dict(latest_status) | {"task_ref": task_ref.model_dump(mode="json")}
        if history is not None:
            history = dict(history) | {"task_ref": task_ref.model_dump(mode="json"), "task_id": normalized_task_id}
        if execution_graph is not None:
            execution_graph = dict(execution_graph) | {"task_ref": task_ref.model_dump(mode="json")}

        return _TaskDetailBundle(
            service=service,
            task_id=normalized_task_id,
            task_ref=task_ref,
            latest_status=latest_status,
            history=history,
            dlq_messages=dlq_messages,
            execution_graph=execution_graph,
            errors=errors,
        )

    async def _build_detail_joins(
        self,
        bundle: _TaskDetailBundle,
        services: list[ServiceRecord],
        *,
        join: JoinMode,
    ) -> tuple[list[StudioTaskJoin], list[StudioJoinWarning]]:
        joined_refs: list[StudioTaskJoin] = []
        join_warnings: list[StudioJoinWarning] = []
        seen: set[tuple[str, str, str, str]] = set()

        for join_kind, matched_value in self._collect_join_candidates(
            bundle.task_ref, bundle.execution_graph, join=join
        ):
            match, warning = await self._resolve_join_candidate(
                services,
                source_task_ref=bundle.task_ref,
                join_kind=join_kind,
                matched_value=matched_value,
            )
            if warning is not None:
                join_warnings.append(warning)
            if match is None:
                continue
            key = (match.item.service_id, match.item.task_id, join_kind, matched_value)
            if key in seen:
                continue
            seen.add(key)
            joined_refs.append(
                StudioTaskJoin(
                    task_ref=match.item.task_ref,
                    join_kind=join_kind,
                    matched_value=matched_value,
                )
            )
        return joined_refs, _dedupe_join_warnings(join_warnings)

    async def _build_search_joins(
        self,
        exact_matches: list[_SearchTaskMatch],
        services: list[ServiceRecord],
        *,
        join: JoinMode,
    ) -> tuple[list[StudioJoinedTaskSearchItem], list[StudioJoinWarning]]:
        bundles = await asyncio.gather(
            *(self._build_task_detail_bundle(match.service, match.item.task_id) for match in exact_matches)
        )
        joined_items: list[StudioJoinedTaskSearchItem] = []
        join_warnings: list[StudioJoinWarning] = []
        seen: set[tuple[str, str, str, str]] = set()

        for bundle in bundles:
            for join_kind, matched_value in self._collect_join_candidates(
                bundle.task_ref, bundle.execution_graph, join=join
            ):
                match, warning = await self._resolve_join_candidate(
                    services,
                    source_task_ref=bundle.task_ref,
                    join_kind=join_kind,
                    matched_value=matched_value,
                )
                if warning is not None:
                    join_warnings.append(warning)
                if match is None:
                    continue
                key = (match.item.service_id, match.item.task_id, join_kind, matched_value)
                if key in seen:
                    continue
                seen.add(key)
                joined_items.append(
                    StudioJoinedTaskSearchItem(
                        **match.item.model_dump(mode="python"),
                        join_kind=join_kind,
                        matched_value=matched_value,
                    )
                )
        return joined_items, _dedupe_join_warnings(join_warnings)

    async def _resolve_join_candidate(
        self,
        services: list[ServiceRecord],
        *,
        source_task_ref: StudioTaskRef,
        join_kind: JoinKind,
        matched_value: str,
    ) -> tuple[_SearchTaskMatch | None, StudioJoinWarning | None]:
        candidate_matches, candidate_errors = await self._find_task_matches(
            matched_value,
            services,
            include_errors=False,
        )
        cross_service_matches = [
            match
            for match in candidate_matches
            if match.item.service_id != source_task_ref.service_id
            and (match.item.service_id, match.item.task_id) != (source_task_ref.service_id, source_task_ref.task_id)
        ]
        if candidate_errors:
            return None, StudioJoinWarning(
                code="incomplete_join_candidate_scan",
                detail=(
                    f"Skipped {join_kind} join for '{matched_value}' because one or more services could not be scanned."
                ),
                join_kind=join_kind,
                matched_value=matched_value,
            )
        if len(cross_service_matches) > 1:
            return None, StudioJoinWarning(
                code="ambiguous_join_candidate",
                detail=(f"Skipped {join_kind} join for '{matched_value}' because it matched multiple services."),
                join_kind=join_kind,
                matched_value=matched_value,
            )
        if not cross_service_matches:
            return None, None
        return cross_service_matches[0], None

    def _collect_join_candidates(
        self,
        task_ref: StudioTaskRef,
        execution_graph: dict[str, Any] | None,
        *,
        join: JoinMode,
    ) -> list[tuple[JoinKind, str]]:
        candidates: list[tuple[JoinKind, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(join_kind: JoinKind, value: str | None) -> None:
            normalized = _normalize_string(value)
            if normalized is None or normalized == task_ref.task_id:
                return
            key = (join_kind, normalized)
            if key in seen:
                return
            seen.add(key)
            candidates.append((join_kind, normalized))

        if join.includes_correlation and task_ref.correlation_id and task_ref.correlation_id != task_ref.task_id:
            add("correlation_id", task_ref.correlation_id)
        if join.includes_lineage:
            for pointer in [*task_ref.parent_refs, *task_ref.child_refs]:
                add("parent_task_id", pointer.task_id)
            for workflow_value in _workflow_lineage_values(execution_graph):
                add("workflow_lineage", workflow_value)
        return candidates

    async def _fetch_status(self, service: ServiceRecord, task_id: str) -> dict[str, Any]:
        path = SERVICE_STATUS_PATH.format(task_id=_quote_path_segment(task_id))
        payload = await self._request_json(
            service,
            capability_id=STATUS_LATEST_ROUTE_ID,
            path=path,
            missing_route_404=self._missing_route_404_enabled(service),
        )
        return self._normalize_status_payload(service, payload, requested_task_id=task_id)

    async def _fetch_history(
        self,
        service: ServiceRecord,
        *,
        task_id: str | None = None,
        start_offset: str = "first",
        max_seconds: float | None = None,
        max_scan: int | None = None,
    ) -> dict[str, Any]:
        payload = await self._request_json(
            service,
            capability_id=STATUS_HISTORY_ROUTE_ID,
            path=SERVICE_HISTORY_PATH,
            params={
                "task_id": task_id,
                "start_offset": start_offset,
                "max_seconds": max_seconds,
                "max_scan": max_scan,
            },
            missing_route_404=True,
        )
        return self._normalize_history_payload(service, payload, requested_task_id=task_id)

    async def _fetch_workflow_topology(self, service: ServiceRecord) -> dict[str, Any]:
        return await self._request_json(
            service,
            capability_id=WORKFLOW_TOPOLOGY_ROUTE_ID,
            path=SERVICE_WORKFLOW_TOPOLOGY_PATH,
            missing_route_404=True,
        )

    async def _fetch_dlq_messages(
        self,
        service: ServiceRecord,
        *,
        queue_name: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
        source_queue_name: str | None = None,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        payload = await self._request_json(
            service,
            capability_id=DLQ_MESSAGES_ROUTE_ID,
            path=SERVICE_DLQ_MESSAGES_PATH,
            params={
                "queue_name": queue_name,
                "task_id": task_id,
                "reason": reason,
                "source_queue_name": source_queue_name,
                "state": state,
                "cursor": cursor,
                "limit": limit,
            },
            missing_route_404=True,
        )
        return self._normalize_dlq_messages_payload(service, payload)

    async def _fetch_execution_graph(self, service: ServiceRecord, task_id: str) -> dict[str, Any]:
        path = SERVICE_EXECUTION_GRAPH_PATH.format(task_id=_quote_path_segment(task_id))
        payload = await self._request_json(
            service,
            capability_id=EXECUTION_GRAPH_ROUTE_ID,
            path=path,
            missing_route_404=self._missing_route_404_enabled(service),
        )
        return self._normalize_execution_graph_payload(service, payload, requested_task_id=task_id)

    def _normalize_status_payload(
        self,
        service: ServiceRecord,
        payload: dict[str, Any],
        *,
        requested_task_id: str | None,
    ) -> dict[str, Any]:
        normalized_task_id = self._payload_value(service, payload, "task_id") or requested_task_id
        event = payload.get("event")
        task_ref = build_task_ref(
            service_id=service.service_id,
            task_id=normalized_task_id or "",
            correlation_id=_event_correlation_id(service, event),
            parent_refs=_pointers_for_task_ids(service.service_id, _event_parent_task_ids(service, event)),
        )
        normalized = dict(payload) | {
            "service_id": service.service_id,
            "task_id": normalized_task_id,
            "task_ref": task_ref.model_dump(mode="json"),
        }
        return normalized

    def _normalize_history_payload(
        self,
        service: ServiceRecord,
        payload: dict[str, Any],
        *,
        requested_task_id: str | None,
    ) -> dict[str, Any]:
        normalized = dict(payload) | {"service_id": service.service_id}
        normalized_task_id = self._payload_value(service, payload, "task_id") or requested_task_id
        events: list[Any] = []
        for item in payload.get("events", []):
            if not isinstance(item, dict):
                events.append(item)
                continue
            event_task_id = self._payload_value(service, item, "task_id") or normalized_task_id
            event_payload = dict(item) | {"service_id": service.service_id}
            if event_task_id is not None:
                event_payload["task_id"] = event_task_id
                event_payload["task_ref"] = build_task_ref(
                    service_id=service.service_id,
                    task_id=event_task_id,
                    correlation_id=_event_correlation_id(service, item),
                    parent_refs=_pointers_for_task_ids(service.service_id, _event_parent_task_ids(service, item)),
                ).model_dump(mode="json")
            events.append(event_payload)
        normalized["events"] = events
        if normalized_task_id is not None:
            normalized["task_id"] = normalized_task_id
        return normalized

    def _normalize_dlq_messages_payload(self, service: ServiceRecord, payload: dict[str, Any]) -> dict[str, Any]:
        items: list[Any] = []
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                items.append(item)
                continue
            normalized_item = dict(item) | {"service_id": service.service_id}
            task_id = self._payload_value(service, item, "task_id") or _normalize_string(item.get("correlation_id"))
            correlation_id = _normalize_string(item.get("correlation_id"))
            if task_id is not None:
                normalized_item["task_id"] = task_id
                normalized_item["task_ref"] = build_task_ref(
                    service_id=service.service_id,
                    task_id=task_id,
                    correlation_id=correlation_id,
                ).model_dump(mode="json")
            items.append(normalized_item)
        return dict(payload) | {"service_id": service.service_id, "items": items}

    def _normalize_execution_graph_payload(
        self,
        service: ServiceRecord,
        payload: dict[str, Any],
        *,
        requested_task_id: str | None,
    ) -> dict[str, Any]:
        normalized_task_id = self._payload_value(service, payload, "task_id") or requested_task_id
        nodes: list[Any] = []
        for item in payload.get("nodes", []):
            if not isinstance(item, dict):
                nodes.append(item)
                continue
            normalized_item = dict(item) | {"service_id": service.service_id}
            node_task_id = _normalize_string(item.get("task_id"))
            if node_task_id is not None:
                annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
                normalized_item["task_ref"] = build_task_ref(
                    service_id=service.service_id,
                    task_id=node_task_id,
                    correlation_id=_normalize_string(annotations.get("correlation_id")),
                    parent_refs=_pointers_for_task_ids(
                        service.service_id,
                        [_normalize_string(annotations.get("parent_task_id"))],
                    ),
                ).model_dump(mode="json")
            nodes.append(normalized_item)
        normalized = dict(payload) | {
            "service_id": service.service_id,
            "task_id": normalized_task_id,
            "nodes": nodes,
        }
        if normalized_task_id is not None:
            normalized["task_ref"] = self._build_primary_task_ref(
                service,
                task_id=normalized_task_id,
                latest_status=None,
                history=None,
                execution_graph=normalized,
            ).model_dump(mode="json")
        return normalized

    def _build_primary_task_ref(
        self,
        service: ServiceRecord,
        *,
        task_id: str,
        latest_status: dict[str, Any] | None,
        history: dict[str, Any] | None,
        execution_graph: dict[str, Any] | None,
    ) -> StudioTaskRef:
        correlations: list[str] = []
        parent_refs: list[StudioTaskPointer] = []
        child_refs: list[StudioTaskPointer] = []

        latest_ref = _task_ref_from_payload(latest_status)
        if latest_ref is not None and latest_ref.correlation_id is not None:
            correlations.append(latest_ref.correlation_id)
            parent_refs.extend(latest_ref.parent_refs)

        for event in _history_events(history):
            event_ref = _task_ref_from_payload(event)
            if event_ref is None or event_ref.task_id != task_id:
                continue
            if event_ref.correlation_id is not None:
                correlations.append(event_ref.correlation_id)
            parent_refs.extend(event_ref.parent_refs)

        if execution_graph is not None:
            for child_task_id in _graph_related_task_ids(execution_graph):
                child_refs.append(build_task_pointer(service.service_id, child_task_id))
            for node in execution_graph.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_ref = _task_ref_from_payload(node)
                if node_ref is None:
                    continue
                if node_ref.task_id == task_id and node_ref.correlation_id is not None:
                    correlations.append(node_ref.correlation_id)
                    parent_refs.extend(node_ref.parent_refs)
                if node_ref.task_id != task_id and str(node.get("kind") or "") == "aggregation_child":
                    child_refs.append(build_task_pointer(service.service_id, node_ref.task_id))

        return build_task_ref(
            service_id=service.service_id,
            task_id=task_id,
            correlation_id=_first_distinct_string(correlations, exclude=task_id),
            parent_refs=parent_refs,
            child_refs=child_refs,
        )

    async def _get_proxyable_service(self, service_id: str) -> ServiceRecord:
        normalized_service_id = service_id.strip()
        try:
            service = await self.registry_service.get_service(normalized_service_id)
        except ServiceNotFoundError as exc:
            raise StudioFederationError(
                status_code=404,
                detail=str(exc),
                code="service_not_found",
                service_id=normalized_service_id,
            ) from exc

        if service.status == ServiceStatus.DISABLED:
            raise StudioFederationError(
                status_code=503,
                detail=f"Service '{service.service_id}' is disabled.",
                code="service_disabled",
                service_id=service.service_id,
            )
        if service.auth_mode != "internal_network":
            raise StudioFederationError(
                status_code=501,
                detail=(
                    f"Service '{service.service_id}' uses auth_mode '{service.auth_mode}', "
                    "which Studio federation does not support yet."
                ),
                code="unsupported_auth_mode",
                service_id=service.service_id,
            )
        return service

    async def _request_json(
        self,
        service: ServiceRecord,
        *,
        capability_id: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        missing_route_404: bool = False,
    ) -> dict[str, Any]:
        if self._supports_capability(service, capability_id) is False:
            raise self._unsupported_route_error(service, capability_id)

        try:
            response = await self.http_client.get(
                f"{service.base_url.rstrip('/')}{path}",
                params=self._map_query_params(service, params),
            )
        except httpx.TimeoutException as exc:
            raise StudioFederationError(
                status_code=504,
                detail=f"Relayna service '{service.service_id}' timed out while serving '{capability_id}'.",
                code="upstream_timeout",
                service_id=service.service_id,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise StudioFederationError(
                status_code=502,
                detail=f"Relayna service '{service.service_id}' could not be reached for '{capability_id}'.",
                code="upstream_transport_error",
                service_id=service.service_id,
                retryable=True,
            ) from exc

        if response.status_code in {401, 403}:
            raise StudioFederationError(
                status_code=502,
                detail=f"Relayna service '{service.service_id}' rejected Studio credentials for '{capability_id}'.",
                code="upstream_auth_failure",
                service_id=service.service_id,
                upstream_status=response.status_code,
            )

        if response.status_code == 404:
            if missing_route_404 and self._looks_like_route_not_found(response):
                raise self._unsupported_route_error(service, capability_id, upstream_status=404)
            raise StudioFederationError(
                status_code=404,
                detail=self._response_detail(response) or f"No upstream resource found for '{capability_id}'.",
                code="upstream_not_found",
                service_id=service.service_id,
                upstream_status=404,
            )

        if response.status_code in {405, 501}:
            raise self._unsupported_route_error(service, capability_id, upstream_status=response.status_code)

        if response.status_code >= 500:
            raise StudioFederationError(
                status_code=502,
                detail=(
                    f"Relayna service '{service.service_id}' returned {response.status_code} for '{capability_id}'."
                ),
                code="upstream_response_error",
                service_id=service.service_id,
                upstream_status=response.status_code,
                retryable=True,
            )

        if response.status_code >= 400:
            raise StudioFederationError(
                status_code=502,
                detail=(
                    self._response_detail(response)
                    or f"Relayna service '{service.service_id}' returned {response.status_code} for '{capability_id}'."
                ),
                code="upstream_response_error",
                service_id=service.service_id,
                upstream_status=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise StudioFederationError(
                status_code=502,
                detail=f"Relayna service '{service.service_id}' returned invalid JSON for '{capability_id}'.",
                code="invalid_json",
                service_id=service.service_id,
            ) from exc

        if not isinstance(payload, dict):
            raise StudioFederationError(
                status_code=502,
                detail=(
                    f"Relayna service '{service.service_id}' returned an invalid response shape for '{capability_id}'."
                ),
                code="invalid_response_shape",
                service_id=service.service_id,
            )

        return dict(payload) | {"service_id": service.service_id}

    async def _capture(self, awaitable) -> tuple[dict[str, Any] | None, StudioFederationError | None]:
        try:
            return await awaitable, None
        except StudioFederationError as exc:
            return None, exc

    def _payload_value(self, service: ServiceRecord, payload: Mapping[str, Any], field_name: str) -> str | None:
        value = _normalize_string(payload.get(field_name))
        if value is not None:
            return value
        capability_document = self._capability_document(service)
        if capability_document is None:
            return None
        alias = capability_document.alias_config_summary.payload_aliases.get(field_name)
        if not isinstance(alias, str):
            return None
        return _normalize_string(payload.get(alias))

    def _supports_capability(self, service: ServiceRecord, capability_id: str) -> bool | None:
        capability_document = self._capability_document(service)
        if capability_document is None:
            return None
        if capability_document.service_metadata.compatibility != "capabilities_v1":
            return None
        return capability_id in capability_document.supported_routes

    def _missing_route_404_enabled(self, service: ServiceRecord) -> bool:
        capability_document = self._capability_document(service)
        if capability_document is None:
            return True
        return capability_document.service_metadata.compatibility != "capabilities_v1"

    def _capability_document(self, service: ServiceRecord) -> CapabilityDocument | None:
        if not service.capabilities:
            return None
        try:
            return CapabilityDocument.model_validate(service.capabilities)
        except ValidationError:
            return None

    def _map_query_params(self, service: ServiceRecord, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if params is None:
            return {}
        capability_document = self._capability_document(service)
        aliases = capability_document.alias_config_summary.http_aliases if capability_document is not None else {}
        mapped: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            mapped[aliases.get(key, key)] = value
        return mapped

    def _unsupported_route_error(
        self,
        service: ServiceRecord,
        capability_id: str,
        *,
        upstream_status: int | None = None,
    ) -> StudioFederationError:
        return StudioFederationError(
            status_code=501,
            detail=f"Service '{service.service_id}' does not support Relayna route '{capability_id}'.",
            code="unsupported_route",
            service_id=service.service_id,
            upstream_status=upstream_status,
        )

    @staticmethod
    def _response_detail(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
        return None

    @classmethod
    def _looks_like_route_not_found(cls, response: httpx.Response) -> bool:
        detail = cls._response_detail(response)
        return detail is None or detail == "Not Found"


def create_federation_router(
    *,
    federation_service: StudioFederationService,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/services/{{service_id}}/status/{{task_id}}")
    async def service_status(service_id: str, task_id: str):
        try:
            payload = await federation_service.get_service_status(service_id, task_id)
        except StudioFederationError as exc:
            return exc.to_response()
        return JSONResponse(payload)

    @router.get(f"{prefix}/services/{{service_id}}/history")
    async def service_history(
        service_id: str,
        task_id: str | None = Query(default=None),
        start_offset: str = Query(default="first"),
        max_seconds: float | None = Query(default=None),
        max_scan: int | None = Query(default=None),
    ):
        try:
            payload = await federation_service.get_service_history(
                service_id,
                task_id=task_id,
                start_offset=start_offset,
                max_seconds=max_seconds,
                max_scan=max_scan,
            )
        except StudioFederationError as exc:
            return exc.to_response()
        return JSONResponse(payload)

    @router.get(f"{prefix}/services/{{service_id}}/workflow/topology")
    async def service_workflow_topology(service_id: str):
        try:
            payload = await federation_service.get_service_workflow_topology(service_id)
        except StudioFederationError as exc:
            return exc.to_response()
        return JSONResponse(payload)

    @router.get(f"{prefix}/services/{{service_id}}/dlq/messages")
    async def service_dlq_messages(
        service_id: str,
        queue_name: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        reason: str | None = Query(default=None),
        source_queue_name: str | None = Query(default=None),
        state: str | None = Query(default=None),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        try:
            payload = await federation_service.get_service_dlq_messages(
                service_id,
                queue_name=queue_name,
                task_id=task_id,
                reason=reason,
                source_queue_name=source_queue_name,
                state=state,
                cursor=cursor,
                limit=limit,
            )
        except StudioFederationError as exc:
            return exc.to_response()
        return JSONResponse(payload)

    @router.get(f"{prefix}/services/{{service_id}}/executions/{{task_id}}/graph")
    async def service_execution_graph(service_id: str, task_id: str):
        try:
            payload = await federation_service.get_service_execution_graph(service_id, task_id)
        except StudioFederationError as exc:
            return exc.to_response()
        return JSONResponse(payload)

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}", response_model=StudioTaskDetailResponse)
    async def task_detail(
        service_id: str,
        task_id: str,
        join: JoinMode = JOIN_MODE_QUERY,
    ) -> StudioTaskDetailResponse | JSONResponse:
        try:
            return await federation_service.get_task_detail(service_id, task_id, join=join)
        except StudioFederationError as exc:
            return exc.to_response()

    return router


def _latest_history_event(events: list[Any]) -> dict[str, Any]:
    latest = events[-1]
    if not isinstance(latest, dict):
        raise StudioFederationError(
            status_code=502,
            detail="Relayna history response returned an invalid event shape.",
            code="invalid_response_shape",
            retryable=False,
        )
    return latest


def _quote_path_segment(value: str) -> str:
    return quote(value.strip(), safe="")


def _normalize_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _first_distinct_string(values: Iterable[str | None], *, exclude: str | None = None) -> str | None:
    normalized_exclude = _normalize_string(exclude)
    for value in values:
        normalized = _normalize_string(value)
        if normalized is None:
            continue
        if normalized_exclude is not None and normalized == normalized_exclude:
            continue
        return normalized
    return None


def _pointers_for_task_ids(service_id: str, task_ids: Iterable[str | None]) -> list[StudioTaskPointer]:
    pointers: list[StudioTaskPointer] = []
    seen: set[tuple[str, str]] = set()
    for task_id in task_ids:
        normalized = _normalize_string(task_id)
        if normalized is None:
            continue
        key = (service_id, normalized)
        if key in seen:
            continue
        seen.add(key)
        pointers.append(build_task_pointer(service_id, normalized))
    return pointers


def _task_ref_from_payload(payload: Mapping[str, Any] | None) -> StudioTaskRef | None:
    if payload is None:
        return None
    task_ref = payload.get("task_ref")
    if not isinstance(task_ref, Mapping):
        return None
    try:
        return StudioTaskRef.model_validate(task_ref)
    except ValidationError:
        return None


def _payload_task_id(payload: Mapping[str, Any] | None) -> str | None:
    if payload is None:
        return None
    return _normalize_string(payload.get("task_id"))


def _event_correlation_id(service: ServiceRecord, event: Any) -> str | None:
    if not isinstance(event, Mapping):
        return None
    capability_document = None
    if service.capabilities:
        try:
            capability_document = CapabilityDocument.model_validate(service.capabilities)
        except ValidationError:
            capability_document = None
    if capability_document is not None:
        alias = capability_document.alias_config_summary.payload_aliases.get("correlation_id")
        if isinstance(alias, str):
            value = _normalize_string(event.get(alias))
            if value is not None:
                return value
    return _normalize_string(event.get("correlation_id"))


def _event_parent_task_ids(service: ServiceRecord, event: Any) -> list[str]:
    if not isinstance(event, Mapping):
        return []
    meta = event.get("meta")
    if not isinstance(meta, Mapping):
        return []
    capability_document = None
    if service.capabilities:
        try:
            capability_document = CapabilityDocument.model_validate(service.capabilities)
        except ValidationError:
            capability_document = None
    if capability_document is not None:
        alias = capability_document.alias_config_summary.payload_aliases.get("parent_task_id")
        if isinstance(alias, str):
            value = _normalize_string(meta.get(alias))
            if value is not None:
                return [value]
    parent_task_id = _normalize_string(meta.get("parent_task_id"))
    return [parent_task_id] if parent_task_id is not None else []


def _history_events(history: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if history is None:
        return []
    events = history.get("events")
    if not isinstance(events, list):
        return []
    return [item for item in events if isinstance(item, Mapping)]


def _graph_related_task_ids(payload: Mapping[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    related = payload.get("related_task_ids")
    if not isinstance(related, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in related:
        value = _normalize_string(item)
        if value is None or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _workflow_lineage_values(payload: Mapping[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in nodes:
        if not isinstance(item, Mapping) or str(item.get("kind") or "") != "workflow_message":
            continue
        annotations = item.get("annotations")
        if not isinstance(annotations, Mapping):
            continue
        candidate = _normalize_string(annotations.get("correlation_id"))
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        values.append(candidate)
    return values


def _dedupe_join_warnings(warnings: Iterable[StudioJoinWarning]) -> list[StudioJoinWarning]:
    normalized: list[StudioJoinWarning] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for warning in warnings:
        key = (warning.code, warning.detail, warning.join_kind, warning.matched_value)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(warning)
    return normalized


__all__ = [
    "FederatedError",
    "SERVICE_DLQ_MESSAGES_PATH",
    "SERVICE_EXECUTION_GRAPH_PATH",
    "SERVICE_HISTORY_PATH",
    "SERVICE_STATUS_PATH",
    "SERVICE_WORKFLOW_TOPOLOGY_PATH",
    "StudioFederationError",
    "StudioFederationService",
    "StudioJoinedTaskSearchItem",
    "StudioTaskDetailResponse",
    "StudioTaskSearchItem",
    "StudioTaskSearchResponse",
    "create_federation_router",
]
