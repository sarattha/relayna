from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .registry import (
    LokiLogConfig,
    OutboundUrlPolicyError,
    ServiceNotFoundError,
    ServiceRecord,
    ServiceRegistryService,
    StudioOutboundUrlPolicy,
)

_LOKI_CURSOR_PREFIX = "loki:"


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


def _parse_page_cursor(value: str) -> tuple[str, int]:
    normalized = value.strip()
    if normalized.startswith(_LOKI_CURSOR_PREFIX):
        remainder = normalized[len(_LOKI_CURSOR_PREFIX) :]
        timestamp_value, separator, skip_value = remainder.partition(":")
        if not separator or not timestamp_value.isdigit() or not skip_value.isdigit():
            raise ValueError("before must be an ISO timestamp or Loki nanosecond cursor")
        return timestamp_value, int(skip_value)
    return _parse_cursor_timestamp(normalized), 0


def _encode_page_cursor(*, cursor: str, skip_count: int) -> str:
    if skip_count <= 0:
        return cursor
    return f"{_LOKI_CURSOR_PREFIX}{cursor}:{skip_count}"


class StudioLogEntry(BaseModel):
    service_id: str
    task_id: str | None = None
    correlation_id: str | None = None
    timestamp: str
    level: str | None = None
    source: str
    message: str
    fields: dict[str, Any] = Field(default_factory=dict)


class StudioLogListResponse(BaseModel):
    count: int
    items: list[StudioLogEntry] = Field(default_factory=list)
    next_cursor: str | None = None


class StudioLogQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str | None = None
    correlation_id: str | None = None
    level: str | None = None
    source: str | None = None
    query: str | None = None
    before: str | None = None
    from_time: str | None = Field(default=None, alias="from")
    to_time: str | None = Field(default=None, alias="to")
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("task_id", "correlation_id", "level", "source", "query", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: Any) -> str | None:
        return _normalize_optional_string(value)

    @field_validator("before", mode="before")
    @classmethod
    def _normalize_before(cls, value: Any) -> str | None:
        normalized = _normalize_optional_string(value)
        if normalized is None:
            return None
        parsed_cursor, skip_count = _parse_page_cursor(normalized)
        return _encode_page_cursor(cursor=parsed_cursor, skip_count=skip_count)

    @field_validator("from_time", "to_time", mode="before")
    @classmethod
    def _normalize_time_bounds(cls, value: Any) -> str | None:
        normalized = _normalize_optional_string(value)
        if normalized is None:
            return None
        return _parse_cursor_timestamp(normalized)

    @model_validator(mode="after")
    def _validate_time_bounds(self) -> StudioLogQuery:
        if self.from_time is not None and self.to_time is not None:
            if int(_iso_to_loki_ns(self.from_time)) > int(_iso_to_loki_ns(self.to_time)):
                raise ValueError("from must be earlier than or equal to to")
        return self


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

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        query_path: str = "/loki/api/v1/query_range",
        outbound_policy: StudioOutboundUrlPolicy | None = None,
    ) -> None:
        normalized_path = query_path.strip() or "/loki/api/v1/query_range"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        self._http_client = http_client
        self._query_path = normalized_path
        self._outbound_policy = outbound_policy or StudioOutboundUrlPolicy()

    async def query_logs(
        self,
        *,
        service: ServiceRecord,
        config: LokiLogConfig,
        query: StudioLogQuery,
    ) -> StudioLogListResponse:
        before_cursor: str | None = None
        skip_boundary_count = 0
        if query.before is not None:
            before_cursor, skip_boundary_count = _parse_page_cursor(query.before)
        request_query = self._build_query(config=config, query=query)
        params = {
            "query": request_query,
            "limit": str(query.limit + skip_boundary_count + 1),
            "direction": "backward",
        }
        if query.from_time is not None:
            params["start"] = _iso_to_loki_ns(query.from_time)
        if query.to_time is not None:
            params["end"] = _iso_to_loki_ns(query.to_time)
        if before_cursor is not None:
            params["end"] = _iso_to_loki_ns(before_cursor)
        headers = {"Accept": "application/json"}
        if config.tenant_id:
            headers["X-Scope-OrgID"] = config.tenant_id
        url = f"{config.base_url.rstrip('/')}{self._query_path}"
        try:
            self._outbound_policy.validate_url(config.base_url, label="Loki log_config.base_url")
            response = await self._http_client.get(url, params=params, headers=headers)
        except OutboundUrlPolicyError as exc:
            raise StudioLogProviderError(str(exc)) from exc
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
        if before_cursor is not None and skip_boundary_count > 0:
            entries = self._skip_boundary_entries(entries, cursor=before_cursor, skip_count=skip_boundary_count)
        next_cursor: str | None = None
        if len(entries) > query.limit:
            last_visible = entries[query.limit - 1]
            boundary_cursor = cast(str | None, last_visible.fields.get("loki_cursor"))
            if boundary_cursor is None:
                next_cursor = None
            else:
                has_hidden_boundary_entry = any(
                    item.fields.get("loki_cursor") == boundary_cursor for item in entries[query.limit :]
                )
                boundary_skip_count = (
                    sum(1 for item in entries[: query.limit] if item.fields.get("loki_cursor") == boundary_cursor)
                    if has_hidden_boundary_entry
                    else 0
                )
                next_cursor = _encode_page_cursor(cursor=boundary_cursor, skip_count=boundary_skip_count)
            entries = entries[: query.limit]

        return StudioLogListResponse(count=len(entries), items=entries, next_cursor=next_cursor)

    def _build_query(self, *, config: LokiLogConfig, query: StudioLogQuery) -> str:
        label_filters = dict(config.service_selector_labels)
        if query.source:
            if not config.source_label:
                raise StudioLogConfigError("Log source filtering requires log_config.source_label for this service.")
            label_filters[config.source_label] = query.source
        if config.task_match_mode == "label" and config.task_id_label and query.task_id:
            label_filters[config.task_id_label] = query.task_id
        if config.correlation_id_label and query.correlation_id:
            label_filters[config.correlation_id_label] = query.correlation_id
        if config.level_label and query.level:
            label_filters[config.level_label] = query.level
        selector = ",".join(f'{key}="{_escape_logql_string(value)}"' for key, value in sorted(label_filters.items()))
        expression = f"{{{selector}}}"
        task_filter = self._build_task_filter(config=config, query=query)
        if task_filter is not None:
            operator, rendered_filter = task_filter
            expression = f'{expression} {operator} "{_escape_logql_string(rendered_filter)}"'
        if query.query:
            expression = f'{expression} |= "{_escape_logql_string(query.query)}"'
        return expression

    def _build_task_filter(
        self,
        *,
        config: LokiLogConfig,
        query: StudioLogQuery,
    ) -> tuple[str, str] | None:
        if not query.task_id:
            return None
        if config.task_match_mode == "label":
            if config.task_id_label:
                return None
            return None
        template = config.task_match_template or "{task_id}"
        rendered_filter = template.replace("{task_id}", query.task_id)
        if config.task_match_mode == "contains":
            return ("|=", rendered_filter)
        if config.task_match_mode == "regex":
            return ("|~", rendered_filter)
        raise StudioLogConfigError(f"Unsupported task_match_mode '{config.task_match_mode}'.")

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
                source = (
                    normalized_labels.get(config.source_label or "", "unknown") if config.source_label else "unknown"
                )
                entries_with_cursor.append(
                    (
                        cursor,
                        StudioLogEntry(
                            service_id=service.service_id,
                            task_id=normalized_labels.get(config.task_id_label or ""),
                            correlation_id=normalized_labels.get(config.correlation_id_label or ""),
                            timestamp=_loki_ns_to_iso(cursor),
                            level=normalized_labels.get(config.level_label or ""),
                            source=source,
                            message=line,
                            fields=fields,
                        ),
                    )
                )

        entries_with_cursor.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in entries_with_cursor]

    def _skip_boundary_entries(
        self, entries: list[StudioLogEntry], *, cursor: str, skip_count: int
    ) -> list[StudioLogEntry]:
        if skip_count <= 0:
            return entries
        remaining = skip_count
        filtered: list[StudioLogEntry] = []
        for entry in entries:
            if remaining > 0 and entry.fields.get("loki_cursor") == cursor:
                remaining -= 1
                continue
            filtered.append(entry)
        return filtered


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
        source: str | None,
        query: str | None,
        before: str | None,
        from_time: str | None,
        to_time: str | None,
        limit: int,
    ) -> StudioLogQuery:
        try:
            return StudioLogQuery(
                task_id=task_id,
                correlation_id=correlation_id,
                level=level,
                source=source,
                query=query,
                before=before,
                from_time=from_time,
                to_time=to_time,
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
        source: str | None = None,
        query: str | None = None,
        before: str | None = None,
        from_time: str | None = Query(default=None, alias="from"),
        to_time: str | None = Query(default=None, alias="to"),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> StudioLogListResponse:
        request_query = _build_query(
            task_id=task_id,
            correlation_id=correlation_id,
            level=level,
            source=source,
            query=query,
            before=before,
            from_time=from_time,
            to_time=to_time,
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
        source: str | None = None,
        query: str | None = None,
        before: str | None = None,
        from_time: str | None = Query(default=None, alias="from"),
        to_time: str | None = Query(default=None, alias="to"),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> StudioLogListResponse:
        request_query = _build_query(
            task_id=task_id,
            correlation_id=correlation_id,
            level=level,
            source=source,
            query=query,
            before=before,
            from_time=from_time,
            to_time=to_time,
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
