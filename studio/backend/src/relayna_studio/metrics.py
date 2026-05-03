from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .federation import StudioFederationError, StudioFederationService, StudioTaskDetailResponse
from .registry import (
    OutboundUrlPolicyError,
    PrometheusMetricsConfig,
    ServiceNotFoundError,
    ServiceRecord,
    ServiceRegistryService,
    StudioOutboundUrlPolicy,
)


class StudioMetricGroup(StrEnum):
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    CPU_REQUESTS = "cpu_requests"
    CPU_LIMITS = "cpu_limits"
    MEMORY_REQUESTS = "memory_requests"
    MEMORY_LIMITS = "memory_limits"
    RESTARTS = "restarts"
    OOM_KILLED = "oom_killed"
    POD_PHASE = "pod_phase"
    READINESS = "readiness"
    NETWORK_RECEIVE = "network_receive"
    NETWORK_TRANSMIT = "network_transmit"
    TASKS_STARTED_RATE = "tasks_started_rate"
    TASKS_FAILED_RATE = "tasks_failed_rate"
    TASKS_RETRIED_RATE = "tasks_retried_rate"
    TASKS_DLQ_RATE = "tasks_dlq_rate"
    TASK_DURATION_P95 = "task_duration_p95"
    ACTIVE_TASKS = "active_tasks"
    WORKER_HEARTBEAT = "worker_heartbeat"
    QUEUE_PUBLISH_RATE = "queue_publish_rate"
    STATUS_EVENTS_RATE = "status_events_rate"
    OBSERVATION_EVENTS_RATE = "observation_events_rate"


_DEFAULT_GROUPS = tuple(StudioMetricGroup)
_TASK_APPROXIMATION_WARNING = (
    "Task-window Kubernetes pod/container metrics are approximate for long-running workers that process many tasks."
)


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _parse_iso_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO timestamp") from exc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _escape_promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class StudioMetricPoint(BaseModel):
    timestamp: str
    value: float | None


class StudioMetricSeries(BaseModel):
    metric: StudioMetricGroup
    unit: str
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[StudioMetricPoint] = Field(default_factory=list)


class StudioMetricsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    service_id: str
    task_id: str | None = None
    from_time: str = Field(alias="from")
    to_time: str = Field(alias="to")
    step_seconds: int
    approximate: bool = False
    warnings: list[str] = Field(default_factory=list)
    series: list[StudioMetricSeries] = Field(default_factory=list)


class StudioMetricsQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_time: str | None = Field(default=None, alias="from")
    to_time: str | None = Field(default=None, alias="to")
    step_seconds: int | None = Field(default=None, alias="step", ge=5, le=3600)
    groups: list[StudioMetricGroup] = Field(default_factory=list)

    @field_validator("from_time", "to_time", mode="before")
    @classmethod
    def _normalize_time_bounds(cls, value: Any) -> str | None:
        normalized = _normalize_optional_string(value)
        if normalized is None:
            return None
        return _iso(_parse_iso_timestamp(normalized))

    @model_validator(mode="after")
    def _validate_time_bounds(self) -> StudioMetricsQuery:
        if self.from_time is not None and self.to_time is not None:
            if _parse_iso_timestamp(self.from_time) > _parse_iso_timestamp(self.to_time):
                raise ValueError("from must be earlier than or equal to to")
        return self


class StudioMetricsProviderError(Exception):
    pass


class StudioMetricsProviderNotConfiguredError(Exception):
    pass


class StudioMetricsConfigError(Exception):
    pass


class StudioMetricsProvider(Protocol):
    provider_name: str

    async def query_metrics(
        self,
        *,
        service: ServiceRecord,
        config: PrometheusMetricsConfig,
        query: StudioMetricsQuery,
        task_id: str | None = None,
        approximate: bool = False,
        warnings: list[str] | None = None,
    ) -> StudioMetricsResponse: ...


class PrometheusMetricsProvider:
    provider_name = "prometheus"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        query_path: str = "/api/v1/query_range",
        outbound_policy: StudioOutboundUrlPolicy | None = None,
    ) -> None:
        normalized_path = query_path.strip() or "/api/v1/query_range"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        self._http_client = http_client
        self._query_path = normalized_path
        self._outbound_policy = outbound_policy or StudioOutboundUrlPolicy()

    async def query_metrics(
        self,
        *,
        service: ServiceRecord,
        config: PrometheusMetricsConfig,
        query: StudioMetricsQuery,
        task_id: str | None = None,
        approximate: bool = False,
        warnings: list[str] | None = None,
    ) -> StudioMetricsResponse:
        now = datetime.now(UTC)
        start = _parse_iso_timestamp(query.from_time) if query.from_time else now - timedelta(hours=1)
        end = _parse_iso_timestamp(query.to_time) if query.to_time else now
        step_seconds = query.step_seconds or config.step_seconds
        groups = query.groups or list(_DEFAULT_GROUPS)
        series: list[StudioMetricSeries] = []
        for group in groups:
            series.extend(
                await self._query_group(
                    service=service,
                    config=config,
                    group=group,
                    start=start,
                    end=end,
                    step_seconds=step_seconds,
                )
            )
        return StudioMetricsResponse.model_validate(
            {
                "service_id": service.service_id,
                "task_id": task_id,
                "from": _iso(start),
                "to": _iso(end),
                "step_seconds": step_seconds,
                "approximate": approximate,
                "warnings": warnings or [],
                "series": series,
            }
        )

    async def _query_group(
        self,
        *,
        service: ServiceRecord,
        config: PrometheusMetricsConfig,
        group: StudioMetricGroup,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> list[StudioMetricSeries]:
        promql = self._build_query(service=service, config=config, group=group)
        params = {
            "query": promql,
            "start": str(start.timestamp()),
            "end": str(end.timestamp()),
            "step": str(step_seconds),
        }
        url = f"{config.base_url.rstrip('/')}{self._query_path}"
        try:
            self._outbound_policy.validate_url(config.base_url, label="Prometheus metrics_config.base_url")
            response = await self._http_client.get(url, params=params, headers={"Accept": "application/json"})
        except OutboundUrlPolicyError as exc:
            raise StudioMetricsProviderError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise StudioMetricsProviderError(f"Prometheus query failed for service '{service.service_id}'.") from exc

        if response.status_code != 200:
            raise StudioMetricsProviderError(
                f"Prometheus query for service '{service.service_id}' returned unexpected status "
                f"{response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise StudioMetricsProviderError(
                f"Prometheus query for service '{service.service_id}' returned invalid JSON."
            ) from exc
        return self._normalize_response(group=group, payload=payload)

    def _build_query(self, *, service: ServiceRecord, config: PrometheusMetricsConfig, group: StudioMetricGroup) -> str:
        selector = self._selector(config)
        container_selector = self._selector(
            config,
            extra={config.container_label: ("!=", ""), config.pod_label: ("=~", ".+")},
        )
        runtime_service_label_value = (
            config.runtime_service_label_value or config.service_selector_labels.get("service") or service.service_id
        )
        runtime_selector = f'service="{_escape_promql_string(runtime_service_label_value)}"'
        if group == StudioMetricGroup.CPU_USAGE:
            return f"rate(container_cpu_usage_seconds_total{{{container_selector}}}[5m])"
        if group == StudioMetricGroup.MEMORY_USAGE:
            return f"container_memory_working_set_bytes{{{container_selector}}}"
        if group == StudioMetricGroup.CPU_REQUESTS:
            return f'kube_pod_container_resource_requests{{{container_selector},resource="cpu"}}'
        if group == StudioMetricGroup.CPU_LIMITS:
            return f'kube_pod_container_resource_limits{{{container_selector},resource="cpu"}}'
        if group == StudioMetricGroup.MEMORY_REQUESTS:
            return f'kube_pod_container_resource_requests{{{container_selector},resource="memory"}}'
        if group == StudioMetricGroup.MEMORY_LIMITS:
            return f'kube_pod_container_resource_limits{{{container_selector},resource="memory"}}'
        if group == StudioMetricGroup.RESTARTS:
            return f"kube_pod_container_status_restarts_total{{{container_selector}}}"
        if group == StudioMetricGroup.OOM_KILLED:
            return f'kube_pod_container_status_last_terminated_reason{{{container_selector},reason="OOMKilled"}}'
        if group == StudioMetricGroup.POD_PHASE:
            return f"kube_pod_status_phase{{{selector}}}"
        if group == StudioMetricGroup.READINESS:
            return f"kube_pod_container_status_ready{{{container_selector}}}"
        if group == StudioMetricGroup.NETWORK_RECEIVE:
            return f"rate(container_network_receive_bytes_total{{{selector}}}[5m])"
        if group == StudioMetricGroup.NETWORK_TRANSMIT:
            return f"rate(container_network_transmit_bytes_total{{{selector}}}[5m])"
        if group == StudioMetricGroup.TASKS_STARTED_RATE:
            return f"sum(rate(relayna_tasks_started_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.TASKS_FAILED_RATE:
            return f"sum(rate(relayna_tasks_failed_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.TASKS_RETRIED_RATE:
            return f"sum(rate(relayna_tasks_retried_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.TASKS_DLQ_RATE:
            return f"sum(rate(relayna_tasks_dlq_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.TASK_DURATION_P95:
            return (
                "histogram_quantile(0.95, "
                f"sum by (le) (rate(relayna_task_duration_seconds_bucket{{{runtime_selector}}}[5m])))"
            )
        if group == StudioMetricGroup.ACTIVE_TASKS:
            return f"sum(relayna_worker_active_tasks{{{runtime_selector}}})"
        if group == StudioMetricGroup.WORKER_HEARTBEAT:
            return f"max(relayna_worker_heartbeat_timestamp{{{runtime_selector}}})"
        if group == StudioMetricGroup.QUEUE_PUBLISH_RATE:
            return f"sum(rate(relayna_queue_publish_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.STATUS_EVENTS_RATE:
            return f"sum(rate(relayna_status_events_published_total{{{runtime_selector}}}[5m]))"
        if group == StudioMetricGroup.OBSERVATION_EVENTS_RATE:
            return f"sum(rate(relayna_observation_events_total{{{runtime_selector}}}[5m]))"
        raise StudioMetricsConfigError(f"Unsupported metric group '{group}'.")

    def _selector(
        self,
        config: PrometheusMetricsConfig,
        *,
        extra: Mapping[str, tuple[str, str]] | None = None,
    ) -> str:
        labels: dict[str, tuple[str, str]] = {config.namespace_label: ("=", config.namespace)}
        labels.update({key: ("=", value) for key, value in config.service_selector_labels.items()})
        if extra:
            labels.update(extra)
        return ",".join(
            f'{key}{operator}"{_escape_promql_string(value)}"' for key, (operator, value) in sorted(labels.items())
        )

    def _normalize_response(self, *, group: StudioMetricGroup, payload: Mapping[str, Any]) -> list[StudioMetricSeries]:
        if payload.get("status") != "success":
            raise StudioMetricsProviderError("Prometheus query returned an unsuccessful response.")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise StudioMetricsProviderError("Prometheus query returned an invalid payload.")
        if data.get("resultType") != "matrix":
            raise StudioMetricsProviderError("Prometheus query returned an invalid result type.")
        result = data.get("result")
        if not isinstance(result, list):
            raise StudioMetricsProviderError("Prometheus query returned an invalid result set.")
        return [self._normalize_series(group=group, item=item) for item in result if isinstance(item, Mapping)]

    def _normalize_series(self, *, group: StudioMetricGroup, item: Mapping[str, Any]) -> StudioMetricSeries:
        metric = item.get("metric")
        values = item.get("values")
        if not isinstance(metric, Mapping) or not isinstance(values, list):
            raise StudioMetricsProviderError("Prometheus query returned an invalid metric series.")
        labels = {str(key): str(value) for key, value in metric.items()}
        points: list[StudioMetricPoint] = []
        for raw_point in values:
            if not isinstance(raw_point, list | tuple) or len(raw_point) != 2:
                raise StudioMetricsProviderError("Prometheus query returned an invalid sample.")
            raw_timestamp, raw_value = raw_point
            try:
                timestamp = datetime.fromtimestamp(float(raw_timestamp), tz=UTC)
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise StudioMetricsProviderError("Prometheus query returned an invalid sample value.") from exc
            points.append(StudioMetricPoint(timestamp=_iso(timestamp), value=value))
        return StudioMetricSeries(metric=group, unit=_metric_unit(group), labels=labels, points=points)


class StudioMetricsQueryService:
    def __init__(
        self,
        *,
        registry_service: ServiceRegistryService,
        providers: Mapping[str, StudioMetricsProvider],
        federation_service: StudioFederationService | None = None,
    ) -> None:
        self._registry_service = registry_service
        self._providers = dict(providers)
        self._federation_service = federation_service

    async def query_service_metrics(self, service_id: str, query: StudioMetricsQuery) -> StudioMetricsResponse:
        service = await self._registry_service.get_service(service_id)
        return await self._query_metrics(service=service, query=query)

    async def query_task_metrics(
        self,
        service_id: str,
        task_id: str,
        query: StudioMetricsQuery,
    ) -> StudioMetricsResponse:
        service = await self._registry_service.get_service(service_id)
        scoped_query, warnings = await self._with_task_window(service_id, task_id.strip(), query)
        if _TASK_APPROXIMATION_WARNING not in warnings:
            warnings.insert(0, _TASK_APPROXIMATION_WARNING)
        return await self._query_metrics(
            service=service,
            query=scoped_query,
            task_id=task_id.strip(),
            approximate=True,
            warnings=warnings,
        )

    async def _query_metrics(
        self,
        *,
        service: ServiceRecord,
        query: StudioMetricsQuery,
        task_id: str | None = None,
        approximate: bool = False,
        warnings: list[str] | None = None,
    ) -> StudioMetricsResponse:
        config = service.metrics_config
        if config is None:
            raise StudioMetricsProviderNotConfiguredError(
                f"Service '{service.service_id}' does not have metrics_config."
            )
        provider = self._providers.get(config.provider)
        if provider is None:
            raise StudioMetricsConfigError(
                f"Unsupported metrics provider '{config.provider}' for service '{service.service_id}'."
            )
        return await provider.query_metrics(
            service=service,
            config=config,
            query=query,
            task_id=task_id,
            approximate=approximate,
            warnings=warnings,
        )

    async def _with_task_window(
        self,
        service_id: str,
        task_id: str,
        query: StudioMetricsQuery,
    ) -> tuple[StudioMetricsQuery, list[str]]:
        if query.from_time or query.to_time:
            return query, []
        if self._federation_service is None:
            now = datetime.now(UTC)
            return (
                query.model_copy(update={"from_time": _iso(now - timedelta(hours=1)), "to_time": _iso(now)}),
                ["Studio could not derive a task metrics window because federation is unavailable."],
            )
        warnings: list[str] = []
        try:
            detail = await self._federation_service.get_task_detail(service_id, task_id)
            start, end = _derive_task_window(detail)
        except StudioFederationError as exc:
            start = end = None
            warnings.append(f"Studio could not derive a task metrics window: {exc.detail}")
        if start is None:
            now = datetime.now(UTC)
            start = now - timedelta(hours=1)
            end = now
            warnings.append("Studio could not derive a task metrics window from Relayna task lifecycle data.")
        elif end is None:
            end = datetime.now(UTC)
        service = await self._registry_service.get_service(service_id)
        padding = timedelta(
            seconds=service.metrics_config.task_window_padding_seconds if service.metrics_config else 120
        )
        return query.model_copy(update={"from_time": _iso(start - padding), "to_time": _iso(end + padding)}), warnings


def _derive_task_window(detail: StudioTaskDetailResponse) -> tuple[datetime | None, datetime | None]:
    graph = detail.execution_graph or {}
    summary = graph.get("summary") if isinstance(graph, Mapping) else None
    start = None
    end = None
    if isinstance(summary, Mapping):
        raw_start = summary.get("started_at")
        raw_end = summary.get("ended_at")
        start = _timestamp_from_value(raw_start)
        end = _timestamp_from_value(raw_end)
    history = detail.history or {}
    events = history.get("events") if isinstance(history, Mapping) else None
    if isinstance(events, list):
        timestamps = [
            parsed
            for event in events
            if isinstance(event, Mapping)
            for parsed in [_timestamp_from_record(event)]
            if parsed is not None
        ]
        if timestamps:
            start = start or min(timestamps)
            end = end or max(timestamps)
    return start, end


def _timestamp_from_record(record: Mapping[str, Any]) -> datetime | None:
    for key in ("timestamp", "event_timestamp", "created_at", "updated_at", "ingested_at"):
        parsed = _timestamp_from_value(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _timestamp_from_value(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _parse_iso_timestamp(value)
    except ValueError:
        return None


def _metric_unit(group: StudioMetricGroup) -> str:
    if group in {StudioMetricGroup.CPU_USAGE, StudioMetricGroup.CPU_REQUESTS, StudioMetricGroup.CPU_LIMITS}:
        return "cores"
    if group in {
        StudioMetricGroup.MEMORY_USAGE,
        StudioMetricGroup.MEMORY_REQUESTS,
        StudioMetricGroup.MEMORY_LIMITS,
    }:
        return "bytes"
    if group in {StudioMetricGroup.NETWORK_RECEIVE, StudioMetricGroup.NETWORK_TRANSMIT}:
        return "bytes_per_second"
    if group in {
        StudioMetricGroup.RESTARTS,
        StudioMetricGroup.OOM_KILLED,
        StudioMetricGroup.ACTIVE_TASKS,
    }:
        return "count"
    if group in {
        StudioMetricGroup.TASKS_STARTED_RATE,
        StudioMetricGroup.TASKS_FAILED_RATE,
        StudioMetricGroup.TASKS_RETRIED_RATE,
        StudioMetricGroup.TASKS_DLQ_RATE,
        StudioMetricGroup.QUEUE_PUBLISH_RATE,
        StudioMetricGroup.STATUS_EVENTS_RATE,
        StudioMetricGroup.OBSERVATION_EVENTS_RATE,
    }:
        return "per_second"
    if group == StudioMetricGroup.TASK_DURATION_P95:
        return "seconds"
    if group == StudioMetricGroup.WORKER_HEARTBEAT:
        return "unix_seconds"
    return "state"


def create_studio_metrics_router(
    *,
    metrics_query_service: StudioMetricsQueryService,
    prefix: str = "/studio",
) -> APIRouter:
    router = APIRouter()

    def _build_query(
        *,
        from_time: str | None,
        to_time: str | None,
        step: int | None,
        group: list[StudioMetricGroup],
    ) -> StudioMetricsQuery:
        try:
            return StudioMetricsQuery.model_validate({"from": from_time, "to": to_time, "step": step, "groups": group})
        except ValidationError as exc:
            sanitized_errors: list[dict[str, Any]] = []
            for item in exc.errors(include_url=False):
                normalized = dict(item)
                context = normalized.get("ctx")
                if isinstance(context, dict) and "error" in context:
                    normalized["ctx"] = {**context, "error": str(context["error"])}
                sanitized_errors.append(normalized)
            raise HTTPException(status_code=422, detail=sanitized_errors) from exc

    @router.get(f"{prefix}/services/{{service_id}}/metrics", response_model=StudioMetricsResponse)
    async def service_metrics(
        service_id: str,
        from_time: str | None = Query(default=None, alias="from"),
        to_time: str | None = Query(default=None, alias="to"),
        step: int | None = Query(default=None, ge=5, le=3600),
        group: list[StudioMetricGroup] = Query(default_factory=list),  # noqa: B008
    ) -> StudioMetricsResponse:
        request_query = _build_query(from_time=from_time, to_time=to_time, step=step, group=group)
        try:
            return await metrics_query_service.query_service_metrics(service_id, request_query)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioMetricsProviderNotConfiguredError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except StudioMetricsConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioMetricsProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/metrics", response_model=StudioMetricsResponse)
    async def task_metrics(
        service_id: str,
        task_id: str,
        from_time: str | None = Query(default=None, alias="from"),
        to_time: str | None = Query(default=None, alias="to"),
        step: int | None = Query(default=None, ge=5, le=3600),
        group: list[StudioMetricGroup] = Query(default_factory=list),  # noqa: B008
    ) -> StudioMetricsResponse:
        request_query = _build_query(from_time=from_time, to_time=to_time, step=step, group=group)
        try:
            return await metrics_query_service.query_task_metrics(service_id, task_id, request_query)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioMetricsProviderNotConfiguredError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except StudioMetricsConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioMetricsProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


__all__ = [
    "PrometheusMetricsProvider",
    "StudioMetricGroup",
    "StudioMetricPoint",
    "StudioMetricSeries",
    "StudioMetricsConfigError",
    "StudioMetricsProvider",
    "StudioMetricsProviderError",
    "StudioMetricsProviderNotConfiguredError",
    "StudioMetricsQuery",
    "StudioMetricsQueryService",
    "StudioMetricsResponse",
    "create_studio_metrics_router",
]
