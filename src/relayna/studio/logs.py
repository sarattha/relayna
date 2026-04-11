from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError, field_validator

from .registry import LokiLogConfig, ServiceNotFoundError, ServiceRecord, ServiceRegistryService


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _parse_cursor_timestamp(value: str) -> str:
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("before must be an ISO timestamp or Loki nanosecond cursor") from exc
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso_to_loki_ns(value: str) -> str:
    if value.isdigit():
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return str(int(parsed.timestamp() * 1_000_000_000))


def _loki_ns_to_iso(value: str) -> str:
    normalized = str(value).strip()
    seconds, nanos = divmod(int(normalized), 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanos // 1000)
    return timestamp.isoformat().replace("+00:00", "Z")


def _escape_logql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class StudioLogEntry(BaseModel):
    service_id: str
    task_id: str | None = None
    correlation_id: str | None = None
    timestamp: str
    level: str | None = None
    message: str
    fields: dict[str, Any] = Field(default_factory=dict)


class StudioLogListResponse(BaseModel):
    count: int
    items: list[StudioLogEntry] = Field(default_factory=list)
    next_cursor: str | None = None


class StudioLogQuery(BaseModel):
    task_id: str | None = None
    correlation_id: str | None = None
    level: str | None = None
    query: str | None = None
    before: str | None = None
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("task_id", "correlation_id", "level", "query", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("before", mode="before")
    @classmethod
    def _normalize_before(cls, value: Any) -> str | None:
        normalized = _normalize_optional_string(value)
        if normalized is None:
            return None
        return _parse_cursor_timestamp(normalized)


class StudioLogProviderError(Exception):
    pass


class StudioLogProviderNotConfiguredError(Exception):
    pass


class StudioLogConfigError(Exception):
    pass


class StudioLogProvider(Protocol):
    provider_name: str

    async def query_logs(
        self,
        *,
        service: ServiceRecord,
        config: LokiLogConfig,
        query: StudioLogQuery,
    ) -> StudioLogListResponse: ...


class LokiLogProvider:
    provider_name = "loki"

    def __init__(self, *, http_client: httpx.AsyncClient, query_path: str = "/loki/api/v1/query_range") -> None:
        normalized_path = query_path.strip() or "/loki/api/v1/query_range"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        self._http_client = http_client
        self._query_path = normalized_path

    async def query_logs(
        self,
        *,
        service: ServiceRecord,
        config: LokiLogConfig,
        query: StudioLogQuery,
    ) -> StudioLogListResponse:
        request_query = self._build_query(config=config, query=query)
        params = {
            "query": request_query,
            "limit": str(query.limit + 1),
            "direction": "backward",
        }
        if query.before is not None:
            params["end"] = _iso_to_loki_ns(query.before)
        headers = {"Accept": "application/json"}
        if config.tenant_id:
            headers["X-Scope-OrgID"] = config.tenant_id
        url = f"{config.base_url.rstrip('/')}{self._query_path}"
        try:
            response = await self._http_client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise StudioLogProviderError(f"Loki query failed for service '{service.service_id}'.") from exc

        if response.status_code != 200:
            raise StudioLogProviderError(
                f"Loki query for service '{service.service_id}' returned unexpected status {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise StudioLogProviderError(
                f"Loki query for service '{service.service_id}' returned invalid JSON."
            ) from exc

        entries = self._normalize_response(service=service, config=config, payload=payload)
        next_cursor: str | None = None
        if len(entries) > query.limit:
            last_visible = entries[query.limit - 1]
            next_cursor = cast(str | None, last_visible.fields.get("loki_cursor"))
            entries = entries[: query.limit]

        return StudioLogListResponse(count=len(entries), items=entries, next_cursor=next_cursor)

    def _build_query(self, *, config: LokiLogConfig, query: StudioLogQuery) -> str:
        label_filters = dict(config.service_selector_labels)
        if config.task_id_label and query.task_id:
            label_filters[config.task_id_label] = query.task_id
        if config.correlation_id_label and query.correlation_id:
            label_filters[config.correlation_id_label] = query.correlation_id
        if config.level_label and query.level:
            label_filters[config.level_label] = query.level
        selector = ",".join(f'{key}="{_escape_logql_string(value)}"' for key, value in sorted(label_filters.items()))
        expression = f"{{{selector}}}"
        if query.query:
            expression = f'{expression} |= "{_escape_logql_string(query.query)}"'
        return expression

    def _normalize_response(
        self,
        *,
        service: ServiceRecord,
        config: LokiLogConfig,
        payload: Any,
    ) -> list[StudioLogEntry]:
        if not isinstance(payload, Mapping) or payload.get("status") != "success":
            raise StudioLogProviderError(f"Loki query for service '{service.service_id}' returned an invalid payload.")
        data = payload.get("data")
        if not isinstance(data, Mapping) or data.get("resultType") != "streams":
            raise StudioLogProviderError(
                f"Loki query for service '{service.service_id}' returned an invalid result set."
            )
        result = data.get("result")
        if not isinstance(result, list):
            raise StudioLogProviderError(
                f"Loki query for service '{service.service_id}' returned an invalid stream list."
            )

        entries_with_cursor: list[tuple[str, StudioLogEntry]] = []
        for stream_item in result:
            if not isinstance(stream_item, Mapping):
                continue
            labels = stream_item.get("stream")
            values = stream_item.get("values")
            if not isinstance(labels, Mapping) or not isinstance(values, list):
                continue
            normalized_labels = {str(key): str(value) for key, value in labels.items()}
            for raw_value in values:
                if not isinstance(raw_value, list | tuple) or len(raw_value) < 2:
                    continue
                cursor = str(raw_value[0]).strip()
                line = str(raw_value[1])
                if not cursor.isdigit():
                    continue
                fields = {
                    "labels": normalized_labels,
                    "loki_cursor": cursor,
                }
                entries_with_cursor.append(
                    (
                        cursor,
                        StudioLogEntry(
                            service_id=service.service_id,
                            task_id=normalized_labels.get(config.task_id_label or ""),
                            correlation_id=normalized_labels.get(config.correlation_id_label or ""),
                            timestamp=_loki_ns_to_iso(cursor),
                            level=normalized_labels.get(config.level_label or ""),
                            message=line,
                            fields=fields,
                        ),
                    )
                )

        entries_with_cursor.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in entries_with_cursor]


class StudioLogQueryService:
    def __init__(
        self,
        *,
        registry_service: ServiceRegistryService,
        providers: Mapping[str, StudioLogProvider],
    ) -> None:
        self._registry_service = registry_service
        self._providers = dict(providers)

    async def query_service_logs(self, service_id: str, query: StudioLogQuery) -> StudioLogListResponse:
        service = await self._registry_service.get_service(service_id)
        return await self._query_logs(service=service, query=query)

    async def query_task_logs(self, service_id: str, task_id: str, query: StudioLogQuery) -> StudioLogListResponse:
        service = await self._registry_service.get_service(service_id)
        scoped_query = query.model_copy(update={"task_id": task_id.strip()})
        return await self._query_logs(service=service, query=scoped_query)

    async def _query_logs(self, *, service: ServiceRecord, query: StudioLogQuery) -> StudioLogListResponse:
        config = service.log_config
        if config is None:
            raise StudioLogProviderNotConfiguredError(f"Service '{service.service_id}' does not have log_config.")
        provider = self._providers.get(config.provider)
        if provider is None:
            raise StudioLogConfigError(
                f"Unsupported log provider '{config.provider}' for service '{service.service_id}'."
            )
        return await provider.query_logs(service=service, config=config, query=query)


def create_studio_logs_router(
    *,
    log_query_service: StudioLogQueryService,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    def _build_query(
        *,
        task_id: str | None,
        correlation_id: str | None,
        level: str | None,
        query: str | None,
        before: str | None,
        limit: int,
    ) -> StudioLogQuery:
        try:
            return StudioLogQuery(
                task_id=task_id,
                correlation_id=correlation_id,
                level=level,
                query=query,
                before=before,
                limit=limit,
            )
        except ValidationError as exc:
            sanitized_errors: list[dict[str, Any]] = []
            for item in exc.errors(include_url=False):
                normalized = dict(item)
                context = normalized.get("ctx")
                if isinstance(context, dict) and "error" in context:
                    normalized["ctx"] = {**context, "error": str(context["error"])}
                sanitized_errors.append(normalized)
            raise HTTPException(status_code=422, detail=sanitized_errors) from exc

    @router.get(f"{prefix}/services/{{service_id}}/logs", response_model=StudioLogListResponse)
    async def service_logs(
        service_id: str,
        task_id: str | None = None,
        correlation_id: str | None = None,
        level: str | None = None,
        query: str | None = None,
        before: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> StudioLogListResponse:
        request_query = _build_query(
            task_id=task_id,
            correlation_id=correlation_id,
            level=level,
            query=query,
            before=before,
            limit=limit,
        )
        try:
            return await log_query_service.query_service_logs(service_id, request_query)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioLogProviderNotConfiguredError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except StudioLogConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioLogProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/logs", response_model=StudioLogListResponse)
    async def task_logs(
        service_id: str,
        task_id: str,
        correlation_id: str | None = None,
        level: str | None = None,
        query: str | None = None,
        before: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> StudioLogListResponse:
        request_query = _build_query(
            task_id=task_id,
            correlation_id=correlation_id,
            level=level,
            query=query,
            before=before,
            limit=limit,
        )
        try:
            return await log_query_service.query_task_logs(service_id, task_id, request_query)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioLogProviderNotConfiguredError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except StudioLogConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioLogProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


__all__ = [
    "LokiLogProvider",
    "StudioLogConfigError",
    "StudioLogEntry",
    "StudioLogListResponse",
    "StudioLogProvider",
    "StudioLogProviderError",
    "StudioLogProviderNotConfiguredError",
    "StudioLogQuery",
    "StudioLogQueryService",
    "create_studio_logs_router",
]
