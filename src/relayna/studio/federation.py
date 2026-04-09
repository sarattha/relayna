from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..api import (
    DLQ_MESSAGES_ROUTE_ID,
    EXECUTION_GRAPH_ROUTE_ID,
    STATUS_HISTORY_ROUTE_ID,
    STATUS_LATEST_ROUTE_ID,
    WORKFLOW_TOPOLOGY_ROUTE_ID,
    CapabilityDocument,
)
from .registry import ServiceNotFoundError, ServiceRecord, ServiceRegistryService, ServiceStatus

SERVICE_STATUS_PATH = "/status/{task_id}"
SERVICE_HISTORY_PATH = "/history"
SERVICE_WORKFLOW_TOPOLOGY_PATH = "/workflow/topology"
SERVICE_DLQ_MESSAGES_PATH = "/dlq/messages"
SERVICE_EXECUTION_GRAPH_PATH = "/executions/{task_id}/graph"


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
    service_name: str
    environment: str
    latest_status: dict[str, Any]
    detail_path: str


class StudioTaskSearchResponse(BaseModel):
    count: int
    items: list[StudioTaskSearchItem] = Field(default_factory=list)
    errors: list[FederatedError] = Field(default_factory=list)
    scanned_services: list[str] = Field(default_factory=list)


class StudioTaskDetailResponse(BaseModel):
    service: ServiceRecord
    service_id: str
    task_id: str
    latest_status: dict[str, Any] | None = None
    history: dict[str, Any] | None = None
    dlq_messages: dict[str, Any] | None = None
    execution_graph: dict[str, Any] | None = None
    errors: list[FederatedError] = Field(default_factory=list)


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

    async def search_tasks(self, task_id: str) -> StudioTaskSearchResponse:
        normalized_task_id = task_id.strip()
        services = [
            service
            for service in await self.registry_service.list_services()
            if service.status != ServiceStatus.DISABLED
        ]
        scanned_services = [service.service_id for service in services]
        semaphore = asyncio.Semaphore(max(1, self.search_max_concurrency))

        async def search_service(service: ServiceRecord) -> tuple[StudioTaskSearchItem | None, FederatedError | None]:
            async with semaphore:
                try:
                    latest_status = await self._search_latest_status(service, normalized_task_id)
                except StudioFederationError as exc:
                    if exc.code == "upstream_not_found":
                        return None, None
                    return None, exc.to_model()
                if latest_status is None:
                    return None, None
                return (
                    StudioTaskSearchItem(
                        service_id=service.service_id,
                        task_id=normalized_task_id,
                        service_name=service.name,
                        environment=service.environment,
                        latest_status=latest_status,
                        detail_path=f"/studio/tasks/{service.service_id}/{normalized_task_id}",
                    ),
                    None,
                )

        results = await asyncio.gather(*(search_service(service) for service in services))
        items = [item for item, _ in results if item is not None]
        errors = [error for _, error in results if error is not None]
        items.sort(key=lambda item: (item.environment, item.service_name.lower(), item.service_id))
        return StudioTaskSearchResponse(count=len(items), items=items, errors=errors, scanned_services=scanned_services)

    async def get_task_detail(self, service_id: str, task_id: str) -> StudioTaskDetailResponse:
        normalized_task_id = task_id.strip()
        service = await self._get_proxyable_service(service_id)

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
                latest_status = {
                    "service_id": service.service_id,
                    "task_id": normalized_task_id,
                    "event": _latest_history_event(events),
                }
            else:
                errors.append(latest_error.to_model())
        elif latest_error is not None:
            errors.append(latest_error.to_model())

        for payload, error in ((history, history_error), (dlq_messages, dlq_error), (execution_graph, graph_error)):
            if payload is None and error is not None:
                errors.append(error.to_model())

        return StudioTaskDetailResponse(
            service=service,
            service_id=service.service_id,
            task_id=normalized_task_id,
            latest_status=latest_status,
            history=history,
            dlq_messages=dlq_messages,
            execution_graph=execution_graph,
            errors=errors,
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
        return {
            "service_id": service.service_id,
            "task_id": task_id,
            "event": _latest_history_event(events),
        }

    async def _fetch_status(self, service: ServiceRecord, task_id: str) -> dict[str, Any]:
        path = SERVICE_STATUS_PATH.format(task_id=_quote_path_segment(task_id))
        return await self._request_json(
            service,
            capability_id=STATUS_LATEST_ROUTE_ID,
            path=path,
            missing_route_404=self._capability_document(service) is None,
        )

    async def _fetch_history(
        self,
        service: ServiceRecord,
        *,
        task_id: str | None = None,
        start_offset: str = "first",
        max_seconds: float | None = None,
        max_scan: int | None = None,
    ) -> dict[str, Any]:
        return await self._request_json(
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
        return await self._request_json(
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

    async def _fetch_execution_graph(self, service: ServiceRecord, task_id: str) -> dict[str, Any]:
        path = SERVICE_EXECUTION_GRAPH_PATH.format(task_id=_quote_path_segment(task_id))
        return await self._request_json(
            service,
            capability_id=EXECUTION_GRAPH_ROUTE_ID,
            path=path,
            missing_route_404=self._capability_document(service) is None,
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

    def _supports_capability(self, service: ServiceRecord, capability_id: str) -> bool | None:
        capability_document = self._capability_document(service)
        if capability_document is None:
            return None
        if capability_document.service_metadata.compatibility != "capabilities_v1":
            return None
        return capability_id in capability_document.supported_routes

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

    @router.get(f"{prefix}/tasks/search", response_model=StudioTaskSearchResponse)
    async def task_search(task_id: str = Query(min_length=1)) -> StudioTaskSearchResponse | JSONResponse:
        try:
            return await federation_service.search_tasks(task_id)
        except StudioFederationError as exc:
            return exc.to_response()

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}", response_model=StudioTaskDetailResponse)
    async def task_detail(service_id: str, task_id: str) -> StudioTaskDetailResponse | JSONResponse:
        try:
            return await federation_service.get_task_detail(service_id, task_id)
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


__all__ = [
    "FederatedError",
    "SERVICE_DLQ_MESSAGES_PATH",
    "SERVICE_EXECUTION_GRAPH_PATH",
    "SERVICE_HISTORY_PATH",
    "SERVICE_STATUS_PATH",
    "SERVICE_WORKFLOW_TOPOLOGY_PATH",
    "StudioFederationError",
    "StudioFederationService",
    "StudioTaskDetailResponse",
    "StudioTaskSearchItem",
    "StudioTaskSearchResponse",
    "create_federation_router",
]
