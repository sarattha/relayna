from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .federation import StudioFederationError, StudioFederationService
from .logs import StudioLogQuery, StudioLogQueryService
from .registry import (
    OutboundUrlPolicyError,
    ServiceNotFoundError,
    ServiceRecord,
    ServiceRegistryService,
    StudioOutboundUrlPolicy,
    TempoTraceConfig,
)


class StudioTraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str | None = None
    service: str | None = None
    source: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    backend_url: str | None = None


class StudioTraceResponse(BaseModel):
    service_id: str
    task_id: str
    trace_ids: list[str] = Field(default_factory=list)
    spans: list[StudioTraceSpan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class StudioTraceProviderError(Exception):
    pass


class StudioTraceConfigError(Exception):
    pass


class StudioTraceProvider(Protocol):
    provider_name: str

    async def query_trace(
        self,
        *,
        service: ServiceRecord,
        config: TempoTraceConfig,
        trace_id: str,
    ) -> list[StudioTraceSpan]: ...


class TempoTraceProvider:
    provider_name = "tempo"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        outbound_policy: StudioOutboundUrlPolicy | None = None,
    ) -> None:
        self._http_client = http_client
        self._outbound_policy = outbound_policy or StudioOutboundUrlPolicy()

    async def query_trace(
        self,
        *,
        service: ServiceRecord,
        config: TempoTraceConfig,
        trace_id: str,
    ) -> list[StudioTraceSpan]:
        if not _is_trace_id(trace_id):
            raise StudioTraceConfigError("trace_id must be 32 lowercase hexadecimal characters.")
        path = config.query_path.replace("{trace_id}", quote(trace_id, safe=""))
        url = f"{config.base_url.rstrip('/')}{path}"
        backend_url = f"{(config.public_base_url or _browser_safe_base_url(config.base_url)).rstrip('/')}{path}"
        headers = {"Accept": "application/json"}
        if config.tenant_id:
            headers["X-Scope-OrgID"] = config.tenant_id
        try:
            self._outbound_policy.validate_url(config.base_url, label="Tempo trace_config.base_url")
            response = await self._http_client.get(url, headers=headers)
        except OutboundUrlPolicyError as exc:
            raise StudioTraceProviderError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise StudioTraceProviderError(f"Tempo query failed for service '{service.service_id}'.") from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise StudioTraceProviderError(
                f"Tempo query for service '{service.service_id}' returned unexpected status {response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise StudioTraceProviderError(
                f"Tempo query for service '{service.service_id}' returned invalid JSON."
            ) from exc
        return self._normalize_response(payload=payload, backend_url=backend_url)

    def _normalize_response(self, *, payload: Any, backend_url: str) -> list[StudioTraceSpan]:
        if not isinstance(payload, Mapping):
            raise StudioTraceProviderError("Tempo query returned an invalid payload.")
        trace_payload = payload.get("trace")
        if isinstance(trace_payload, Mapping):
            payload = trace_payload
        batches = payload.get("batches") or payload.get("resourceSpans") or payload.get("resource_spans") or []
        if not isinstance(batches, list):
            raise StudioTraceProviderError("Tempo query returned an invalid span batch list.")
        spans: list[StudioTraceSpan] = []
        for batch in batches:
            if not isinstance(batch, Mapping):
                continue
            resource = batch.get("resource")
            resource_attributes = _attributes(resource.get("attributes", []) if isinstance(resource, Mapping) else [])
            service_name = _string(resource_attributes.get("service.name") or resource_attributes.get("service"))
            scope_spans = (
                batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or batch.get("scope_spans")
            )
            if not isinstance(scope_spans, list):
                continue
            for scope in scope_spans:
                if not isinstance(scope, Mapping):
                    continue
                raw_spans = scope.get("spans")
                if not isinstance(raw_spans, list):
                    continue
                for raw_span in raw_spans:
                    if isinstance(raw_span, Mapping):
                        spans.append(_normalize_span(raw_span, service=service_name, backend_url=backend_url))
        return sorted(spans, key=lambda item: item.start_time or "")


class StudioTraceQueryService:
    def __init__(
        self,
        *,
        registry_service: ServiceRegistryService,
        providers: Mapping[str, StudioTraceProvider],
        federation_service: StudioFederationService | None = None,
        log_query_service: StudioLogQueryService | None = None,
    ) -> None:
        self._registry_service = registry_service
        self._providers = dict(providers)
        self._federation_service = federation_service
        self._log_query_service = log_query_service

    async def query_task_traces(self, service_id: str, task_id: str) -> StudioTraceResponse:
        service = await self._registry_service.get_service(service_id)
        trace_ids, warnings = await self._discover_task_trace_ids(service, task_id)
        config = service.trace_config
        if config is None:
            warnings.append("No trace provider configured for this service.")
            return StudioTraceResponse(
                service_id=service.service_id,
                task_id=task_id,
                trace_ids=trace_ids,
                warnings=warnings,
            )
        if not trace_ids:
            warnings.append("No trace identifiers were found in task events or logs.")
            return StudioTraceResponse(service_id=service.service_id, task_id=task_id, trace_ids=[], warnings=warnings)
        provider = self._providers.get(config.provider)
        if provider is None:
            raise StudioTraceConfigError(
                f"Unsupported trace provider '{config.provider}' for service '{service.service_id}'."
            )
        spans: list[StudioTraceSpan] = []
        for trace_id in trace_ids:
            spans.extend(await provider.query_trace(service=service, config=config, trace_id=trace_id))
        return StudioTraceResponse(
            service_id=service.service_id,
            task_id=task_id,
            trace_ids=trace_ids,
            spans=spans,
            warnings=warnings,
        )

    async def _discover_task_trace_ids(self, service: ServiceRecord, task_id: str) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        trace_ids: set[str] = set()
        if self._federation_service is not None:
            try:
                detail = await self._federation_service.get_task_detail(service.service_id, task_id)
                _collect_trace_ids(detail.model_dump(mode="json"), trace_ids)
            except StudioFederationError as exc:
                warnings.append(f"Studio could not inspect task detail for trace identifiers: {exc.detail}")
        if self._log_query_service is not None and service.log_config is not None:
            try:
                logs = await self._log_query_service.query_task_logs(
                    service.service_id,
                    task_id,
                    StudioLogQuery(task_id=task_id, limit=100),
                )
                _collect_trace_ids(logs.model_dump(mode="json"), trace_ids)
            except Exception:
                warnings.append("Studio could not inspect task logs for trace identifiers.")
        return sorted(trace_ids), warnings


def _collect_trace_ids(value: Any, output: set[str]) -> None:
    if isinstance(value, Mapping):
        raw = value.get("trace_id")
        if isinstance(raw, str) and _is_trace_id(raw):
            output.add(raw)
        for item in value.values():
            _collect_trace_ids(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_trace_ids(item, output)
    elif isinstance(value, str) and '"trace_id"' in value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        _collect_trace_ids(parsed, output)


def _is_trace_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _browser_safe_base_url(value: str) -> str:
    return value.replace("://host.docker.internal", "://localhost", 1)


def _attributes(items: Any) -> dict[str, Any]:
    if isinstance(items, Mapping):
        return dict(items)
    if not isinstance(items, list):
        return {}
    parsed: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = _string(item.get("key"))
        if not key:
            continue
        parsed[key] = _attribute_value(item.get("value"))
    return parsed


def _attribute_value(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "arrayValue", "kvlistValue"):
        if key in value:
            return value[key]
    return value


def _normalize_span(raw: Mapping[str, Any], *, service: str | None, backend_url: str) -> StudioTraceSpan:
    attributes = _attributes(raw.get("attributes", []))
    start_ns = _int_or_none(raw.get("startTimeUnixNano") or raw.get("start_time_unix_nano"))
    end_ns = _int_or_none(raw.get("endTimeUnixNano") or raw.get("end_time_unix_nano"))
    duration_ms = None
    if start_ns is not None and end_ns is not None and end_ns >= start_ns:
        duration_ms = (end_ns - start_ns) / 1_000_000
    return StudioTraceSpan(
        trace_id=_tempo_id_to_hex(raw.get("traceId") or raw.get("trace_id"), byte_length=16),
        span_id=_tempo_id_to_hex(raw.get("spanId") or raw.get("span_id"), byte_length=8),
        parent_span_id=_tempo_optional_id_to_hex(raw.get("parentSpanId") or raw.get("parent_span_id"), byte_length=8),
        name=_string(raw.get("name")) or "unnamed span",
        kind=_string(raw.get("kind")),
        service=service,
        source=_string(attributes.get("service.name") or attributes.get("messaging.system") or service),
        start_time=_ns_to_iso(start_ns),
        end_time=_ns_to_iso(end_ns),
        duration_ms=duration_ms,
        attributes=attributes,
        backend_url=backend_url,
    )


def _tempo_optional_id_to_hex(value: Any, *, byte_length: int) -> str | None:
    normalized = _tempo_id_to_hex(value, byte_length=byte_length)
    return normalized or None


def _tempo_id_to_hex(value: Any, *, byte_length: int) -> str:
    raw = _string(value)
    if raw is None:
        return ""
    lower = raw.lower()
    if len(lower) == byte_length * 2 and all(char in "0123456789abcdef" for char in lower):
        return lower
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return raw
    if len(decoded) != byte_length:
        return raw
    return decoded.hex()


def _ns_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    seconds = value / 1_000_000_000
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def create_studio_traces_router(
    *,
    trace_query_service: StudioTraceQueryService,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/traces", response_model=StudioTraceResponse)
    async def task_traces(service_id: str, task_id: str) -> StudioTraceResponse:
        try:
            return await trace_query_service.query_task_traces(service_id, task_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioTraceConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioTraceProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


__all__ = [
    "StudioTraceConfigError",
    "StudioTraceProviderError",
    "StudioTraceQueryService",
    "StudioTraceResponse",
    "StudioTraceSpan",
    "TempoTraceProvider",
    "create_studio_traces_router",
]
