from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .events import StudioEventIngestService
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


class StudioTracePathEvidence(BaseModel):
    source: Literal["graph_node", "graph_edge", "status_history", "studio_event", "span", "dlq", "latest_status"]
    source_id: str
    label: str
    timestamp: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StudioTracePathNode(BaseModel):
    id: str
    kind: str
    label: str
    task_id: str | None = None
    state: str | None = None
    queue_name: str | None = None
    stage: str | None = None
    attempt: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    evidence: list[StudioTracePathEvidence] = Field(default_factory=list)


class StudioTracePathEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    timestamp: str | None = None
    state: str | None = None
    evidence: list[StudioTracePathEvidence] = Field(default_factory=list)


class StudioTracePathLogMetadata(BaseModel):
    configured: bool = False
    provider: str | None = None
    source_label: str | None = None
    task_id_label: str | None = None
    correlation_id_label: str | None = None
    task_id: str
    correlation_id: str | None = None
    query: str | None = None
    from_time: str | None = None
    to_time: str | None = None


class StudioTracePathSummary(BaseModel):
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    graph_completeness: str | None = None
    trace_ids: list[str] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    span_count: int = 0
    event_count: int = 0
    dlq_count: int = 0
    live_state_counts: dict[str, int] = Field(default_factory=dict)


class StudioTracePathResponse(BaseModel):
    service_id: str
    task_id: str
    summary: StudioTracePathSummary
    nodes: list[StudioTracePathNode] = Field(default_factory=list)
    edges: list[StudioTracePathEdge] = Field(default_factory=list)
    spans: list[StudioTraceSpan] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    dlq_messages: list[dict[str, Any]] = Field(default_factory=list)
    log_metadata: StudioTracePathLogMetadata
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
        event_ingest_service: StudioEventIngestService | None = None,
    ) -> None:
        self._registry_service = registry_service
        self._providers = dict(providers)
        self._federation_service = federation_service
        self._log_query_service = log_query_service
        self._event_ingest_service = event_ingest_service

    async def query_task_traces(self, service_id: str, task_id: str) -> StudioTraceResponse:
        service = await self._registry_service.get_service(service_id)
        return await self._query_task_traces_for_service(service=service, task_id=task_id)

    async def _query_task_traces_for_service(
        self,
        *,
        service: ServiceRecord,
        task_id: str,
        detail_payload: Mapping[str, Any] | None = None,
    ) -> StudioTraceResponse:
        trace_ids, warnings = await self._discover_task_trace_ids(
            service,
            task_id,
            detail_payload=detail_payload,
        )
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

    async def query_task_trace_path(self, service_id: str, task_id: str) -> StudioTracePathResponse:
        service = await self._registry_service.get_service(service_id)
        normalized_task_id = task_id.strip()
        warnings: list[str] = []
        detail_payload: dict[str, Any] = {}
        if self._federation_service is None:
            warnings.append("No federation service is available for task detail.")
        else:
            try:
                detail = await self._federation_service.get_task_detail(service.service_id, normalized_task_id)
                detail_payload = detail.model_dump(mode="json")
            except StudioFederationError as exc:
                warnings.append(f"Studio could not inspect task detail for trace path evidence: {exc.detail}")

        try:
            trace_response = await self._query_task_traces_for_service(
                service=service,
                task_id=normalized_task_id,
                detail_payload=detail_payload,
            )
            warnings.extend(trace_response.warnings)
        except (StudioTraceConfigError, StudioTraceProviderError) as exc:
            fallback_trace_ids: set[str] = set()
            _collect_trace_ids(detail_payload, fallback_trace_ids)
            trace_response = StudioTraceResponse(
                service_id=service.service_id,
                task_id=normalized_task_id,
                trace_ids=sorted(fallback_trace_ids),
            )
            warnings.append(f"Studio could not enrich the task trace path with provider spans: {exc}")

        event_items: list[dict[str, Any]] = []
        if self._event_ingest_service is not None:
            try:
                event_response = await self._event_ingest_service.list_task_events(
                    service.service_id,
                    normalized_task_id,
                    limit=200,
                )
                event_items = [item.model_dump(mode="json") for item in event_response.items]
            except Exception:
                warnings.append("Studio could not inspect task event history for trace path evidence.")
        else:
            warnings.append("No Studio event store is available for task timeline evidence.")

        return _build_trace_path_response(
            service=service,
            task_id=normalized_task_id,
            detail_payload=detail_payload,
            trace_response=trace_response,
            event_items=event_items,
            warnings=warnings,
        )

    async def _discover_task_trace_ids(
        self,
        service: ServiceRecord,
        task_id: str,
        *,
        detail_payload: Mapping[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        trace_ids: set[str] = set()
        if detail_payload is not None:
            _collect_trace_ids(detail_payload, trace_ids)
        elif self._federation_service is not None:
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


def _build_trace_path_response(
    *,
    service: ServiceRecord,
    task_id: str,
    detail_payload: Mapping[str, Any],
    trace_response: StudioTraceResponse,
    event_items: list[dict[str, Any]],
    warnings: list[str],
) -> StudioTracePathResponse:
    graph = _mapping_or_empty(detail_payload.get("execution_graph"))
    nodes = _trace_path_nodes_from_graph(graph, task_id=task_id)
    edges = _trace_path_edges_from_graph(graph)
    nodes_by_id = {node.id: node for node in nodes}
    dlq_messages = _dlq_messages_from_detail(detail_payload)

    if not nodes:
        root_node = StudioTracePathNode(
            id=f"task:{task_id}",
            kind="task",
            label=task_id,
            task_id=task_id,
            evidence=[
                StudioTracePathEvidence(
                    source="latest_status",
                    source_id=task_id,
                    label="task detail",
                    payload=_json_object(detail_payload.get("latest_status")),
                )
            ],
        )
        nodes.append(root_node)
        nodes_by_id[root_node.id] = root_node
        _append_history_status_nodes(
            nodes=nodes,
            edges=edges,
            nodes_by_id=nodes_by_id,
            task_id=task_id,
            history=_mapping_or_empty(detail_payload.get("history")),
        )

    _attach_dlq_messages(nodes=nodes, edges=edges, nodes_by_id=nodes_by_id, dlq_messages=dlq_messages, task_id=task_id)
    _attach_events(nodes=nodes, event_items=event_items, fallback_task_id=task_id)
    _attach_spans(nodes=nodes, edges=edges, nodes_by_id=nodes_by_id, spans=trace_response.spans, task_id=task_id)

    nodes = sorted(nodes, key=lambda item: (_timestamp_sort_key(item.started_at), item.id))
    edges = sorted(edges, key=lambda item: (_timestamp_sort_key(item.timestamp), item.id))
    summary = _trace_path_summary(
        graph=graph,
        nodes=nodes,
        edges=edges,
        spans=trace_response.spans,
        events=event_items,
        dlq_messages=dlq_messages,
        trace_ids=trace_response.trace_ids,
    )
    return StudioTracePathResponse(
        service_id=service.service_id,
        task_id=task_id,
        summary=summary,
        nodes=nodes,
        edges=edges,
        spans=trace_response.spans,
        events=event_items,
        dlq_messages=dlq_messages,
        log_metadata=_trace_path_log_metadata(
            service=service,
            task_id=task_id,
            detail_payload=detail_payload,
            summary=summary,
        ),
        warnings=_dedupe_strings(warnings),
    )


def _trace_path_nodes_from_graph(graph: Mapping[str, Any], *, task_id: str) -> list[StudioTracePathNode]:
    nodes: list[StudioTracePathNode] = []
    for item in _list_of_mappings(graph.get("nodes")):
        annotations = _mapping_or_empty(item.get("annotations"))
        node_id = _string(item.get("id")) or f"node:{len(nodes) + 1}"
        node_task_id = _string(item.get("task_id")) or task_id
        timestamp = _string(item.get("timestamp"))
        node = StudioTracePathNode(
            id=node_id,
            kind=_string(item.get("kind")) or "node",
            label=_string(item.get("label")) or node_id,
            task_id=node_task_id,
            state=_string(item.get("state")),
            queue_name=_first_string(
                annotations.get("queue_name"),
                annotations.get("source_queue_name"),
                annotations.get("retry_queue_name"),
            ),
            stage=_string(annotations.get("stage")),
            attempt=_int_or_none(annotations.get("retry_attempt")),
            trace_id=_string(annotations.get("trace_id")),
            span_id=_string(annotations.get("span_id")),
            parent_span_id=_string(annotations.get("parent_span_id")),
            started_at=timestamp,
            ended_at=_string(item.get("updated_at")) if _string(item.get("updated_at")) != timestamp else None,
            evidence=[
                StudioTracePathEvidence(
                    source="graph_node",
                    source_id=node_id,
                    label=_string(item.get("kind")) or "graph node",
                    timestamp=timestamp,
                    payload=dict(item),
                )
            ],
        )
        nodes.append(node)
    return nodes


def _trace_path_edges_from_graph(graph: Mapping[str, Any]) -> list[StudioTracePathEdge]:
    edges: list[StudioTracePathEdge] = []
    for index, item in enumerate(_list_of_mappings(graph.get("edges")), start=1):
        source = _string(item.get("source"))
        target = _string(item.get("target"))
        if source is None or target is None:
            continue
        edge_id = f"{source}->{target}:{index}"
        timestamp = _string(item.get("timestamp"))
        edges.append(
            StudioTracePathEdge(
                id=edge_id,
                source=source,
                target=target,
                kind=_string(item.get("kind")) or "related",
                timestamp=timestamp,
                state=_string(item.get("state")),
                evidence=[
                    StudioTracePathEvidence(
                        source="graph_edge",
                        source_id=edge_id,
                        label=_string(item.get("kind")) or "graph edge",
                        timestamp=timestamp,
                        payload=dict(item),
                    )
                ],
            )
        )
    return edges


def _append_history_status_nodes(
    *,
    nodes: list[StudioTracePathNode],
    edges: list[StudioTracePathEdge],
    nodes_by_id: dict[str, StudioTracePathNode],
    task_id: str,
    history: Mapping[str, Any],
) -> None:
    root_id = f"task:{task_id}"
    previous_id = root_id
    for index, item in enumerate(_list_of_mappings(history.get("events")), start=1):
        status = _string(item.get("status")) or "status"
        timestamp = _string(item.get("timestamp"))
        node_id = f"status:{task_id}:{index}"
        node = StudioTracePathNode(
            id=node_id,
            kind="status_event",
            label=status,
            task_id=task_id,
            state=_state_from_status(status),
            trace_id=_string(item.get("trace_id")),
            span_id=_string(item.get("span_id")),
            started_at=timestamp,
            evidence=[
                StudioTracePathEvidence(
                    source="status_history",
                    source_id=_string(item.get("event_id")) or node_id,
                    label=f"status.{status}",
                    timestamp=timestamp,
                    payload=dict(item),
                )
            ],
        )
        nodes.append(node)
        nodes_by_id[node_id] = node
        edge_id = f"{previous_id}->{node_id}:status"
        edges.append(
            StudioTracePathEdge(
                id=edge_id,
                source=previous_id,
                target=node_id,
                kind="published_status",
                timestamp=timestamp,
            )
        )
        previous_id = node_id


def _attach_events(
    *,
    nodes: list[StudioTracePathNode],
    event_items: list[dict[str, Any]],
    fallback_task_id: str,
) -> None:
    for item in event_items:
        payload = _mapping_or_empty(item.get("payload"))
        timestamp = _string(item.get("timestamp")) or _string(item.get("ingested_at"))
        event_type = _string(item.get("event_type")) or "event"
        evidence = StudioTracePathEvidence(
            source="studio_event",
            source_id=_string(item.get("dedupe_key")) or _string(item.get("event_id")) or event_type,
            label=event_type,
            timestamp=timestamp,
            payload=dict(item),
        )
        target = _best_node_for_event(
            nodes,
            task_id=_string(item.get("task_id")) or fallback_task_id,
            event_type=event_type,
            payload=payload,
        )
        target.evidence.append(evidence)
        if target.trace_id is None:
            target.trace_id = _string(payload.get("trace_id"))
        if target.span_id is None:
            target.span_id = _string(payload.get("span_id"))


def _attach_dlq_messages(
    *,
    nodes: list[StudioTracePathNode],
    edges: list[StudioTracePathEdge],
    nodes_by_id: dict[str, StudioTracePathNode],
    dlq_messages: list[dict[str, Any]],
    task_id: str,
) -> None:
    for index, item in enumerate(dlq_messages, start=1):
        timestamp = _string(item.get("dead_lettered_at"))
        evidence = StudioTracePathEvidence(
            source="dlq",
            source_id=_string(item.get("dlq_id")) or f"dlq:{task_id}:{index}",
            label=_string(item.get("reason")) or _string(item.get("queue_name")) or "DLQ",
            timestamp=timestamp,
            payload=dict(item),
        )
        target = _best_node_for_dlq(nodes, item)
        if target is not None:
            target.evidence.append(evidence)
            target.state = target.state or "dead_lettered"
            continue
        node_id = f"dlq:{task_id}:trace-path:{index}"
        node = StudioTracePathNode(
            id=node_id,
            kind="dlq_record",
            label=evidence.label,
            task_id=_string(item.get("task_id")) or task_id,
            state="dead_lettered",
            queue_name=_string(item.get("queue_name")),
            attempt=_int_or_none(item.get("retry_attempt")),
            started_at=timestamp,
            evidence=[evidence],
        )
        nodes.append(node)
        nodes_by_id[node_id] = node
        source = _latest_non_dlq_node(nodes, task_id=task_id, before=timestamp)
        if source is not None:
            edges.append(
                StudioTracePathEdge(
                    id=f"{source.id}->{node_id}:dlq",
                    source=source.id,
                    target=node_id,
                    kind="dead_lettered_to",
                    timestamp=timestamp,
                    state="blocked",
                )
            )


def _attach_spans(
    *,
    nodes: list[StudioTracePathNode],
    edges: list[StudioTracePathEdge],
    nodes_by_id: dict[str, StudioTracePathNode],
    spans: list[StudioTraceSpan],
    task_id: str,
) -> None:
    span_node_ids: dict[str, str] = {}
    for span in spans:
        evidence = StudioTracePathEvidence(
            source="span",
            source_id=span.span_id,
            label=span.name,
            timestamp=span.start_time,
            payload=span.model_dump(mode="json"),
        )
        target = _best_node_for_span(nodes, span, fallback_task_id=task_id)
        if target is None:
            node_id = f"span:{span.span_id}"
            target = StudioTracePathNode(
                id=node_id,
                kind="span",
                label=span.name,
                task_id=_span_task_id(span) or task_id,
                queue_name=_span_queue_name(span),
                stage=_span_stage(span),
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                started_at=span.start_time,
                ended_at=span.end_time,
                duration_ms=span.duration_ms,
            )
            nodes.append(target)
            nodes_by_id[node_id] = target
        target.evidence.append(evidence)
        target.trace_id = target.trace_id or span.trace_id
        target.span_id = target.span_id or span.span_id
        target.parent_span_id = target.parent_span_id or span.parent_span_id
        target.started_at = _earliest_timestamp(target.started_at, span.start_time)
        target.ended_at = _latest_timestamp(target.ended_at, span.end_time)
        target.duration_ms = _duration_ms(target.started_at, target.ended_at) or target.duration_ms or span.duration_ms
        span_node_ids[span.span_id] = target.id

    existing_edge_keys = {(edge.source, edge.target, edge.kind) for edge in edges}
    for span in spans:
        if not span.parent_span_id:
            continue
        source = span_node_ids.get(span.parent_span_id)
        target = span_node_ids.get(span.span_id)
        if source is None or target is None or source == target:
            continue
        key = (source, target, "span_child")
        if key in existing_edge_keys:
            continue
        existing_edge_keys.add(key)
        edges.append(
            StudioTracePathEdge(
                id=f"{source}->{target}:span:{span.span_id}",
                source=source,
                target=target,
                kind="span_child",
                timestamp=span.start_time,
                state="traversed",
            )
        )


def _best_node_for_event(
    nodes: list[StudioTracePathNode],
    *,
    task_id: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> StudioTracePathNode:
    queue_name = _string(payload.get("queue_name"))
    stage = _string(payload.get("stage"))
    lowered = event_type.lower()
    candidates = [node for node in nodes if node.task_id == task_id]
    if not candidates:
        candidates = nodes
    if "deadletter" in lowered or "dead_letter" in lowered or "dlq" in lowered:
        dlq = _first_node(candidates, kind="dlq_record", queue_name=queue_name)
        if dlq is not None:
            return dlq
    if "status." in lowered:
        status = _first_node(candidates, kind="status_event")
        if status is not None:
            return status
    if stage:
        stage_node = _first_node(candidates, stage=stage)
        if stage_node is not None:
            return stage_node
    if queue_name:
        queue_node = _first_node(candidates, queue_name=queue_name)
        if queue_node is not None:
            return queue_node
    return candidates[0]


def _best_node_for_dlq(nodes: list[StudioTracePathNode], item: Mapping[str, Any]) -> StudioTracePathNode | None:
    task_id = _string(item.get("task_id"))
    queue_name = _string(item.get("queue_name"))
    candidates = [node for node in nodes if node.kind == "dlq_record" and (task_id is None or node.task_id == task_id)]
    if queue_name:
        queue_match = _first_node(candidates, queue_name=queue_name)
        if queue_match is not None:
            return queue_match
    return candidates[0] if candidates else None


def _best_node_for_span(
    nodes: list[StudioTracePathNode],
    span: StudioTraceSpan,
    *,
    fallback_task_id: str,
) -> StudioTracePathNode | None:
    span_task_id = _span_task_id(span) or fallback_task_id
    queue_name = _span_queue_name(span)
    stage = _span_stage(span)
    candidates = [node for node in nodes if node.task_id == span_task_id]
    if stage:
        if "consumer" in span.name.lower() or span.kind == "SPAN_KIND_CONSUMER":
            stage_node = _first_node(candidates, kind="stage_attempt", stage=stage)
            if stage_node is not None:
                return stage_node
        stage_node = _first_node(candidates, stage=stage)
        if stage_node is not None:
            return stage_node
    if queue_name:
        queue_node = _first_node(candidates, queue_name=queue_name)
        if queue_node is not None:
            return queue_node
    if "consumer" in span.name.lower() or span.kind == "SPAN_KIND_CONSUMER":
        attempt = _first_node(candidates, kind="task_attempt") or _first_node(candidates, kind="stage_attempt")
        if attempt is not None:
            return attempt
    if "publish" in span.name.lower() or span.kind == "SPAN_KIND_PRODUCER":
        message = _first_node(candidates, kind="workflow_message")
        if message is not None:
            return message
    return None


def _trace_path_summary(
    *,
    graph: Mapping[str, Any],
    nodes: list[StudioTracePathNode],
    edges: list[StudioTracePathEdge],
    spans: list[StudioTraceSpan],
    events: list[dict[str, Any]],
    dlq_messages: list[dict[str, Any]],
    trace_ids: list[str],
) -> StudioTracePathSummary:
    graph_summary = _mapping_or_empty(graph.get("summary"))
    started_at = _first_string(
        graph_summary.get("started_at"),
        _earliest_timestamp(*(node.started_at for node in nodes)),
        _earliest_timestamp(*(span.start_time for span in spans)),
    )
    ended_at = _first_string(
        graph_summary.get("ended_at"),
        _latest_timestamp(*(node.ended_at or node.started_at for node in nodes)),
        _latest_timestamp(*(span.end_time for span in spans)),
    )
    return StudioTracePathSummary(
        status=_string(graph_summary.get("status")) or _latest_status_from_nodes(nodes),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=_float_or_none(graph_summary.get("duration_ms")) or _duration_ms(started_at, ended_at),
        graph_completeness=_string(graph_summary.get("graph_completeness")),
        trace_ids=trace_ids,
        node_count=len(nodes),
        edge_count=len(edges),
        span_count=len(spans),
        event_count=len(events),
        dlq_count=len(dlq_messages),
        live_state_counts=_int_counts(graph_summary.get("live_state_counts")),
    )


def _trace_path_log_metadata(
    *,
    service: ServiceRecord,
    task_id: str,
    detail_payload: Mapping[str, Any],
    summary: StudioTracePathSummary,
) -> StudioTracePathLogMetadata:
    task_ref = _mapping_or_empty(detail_payload.get("task_ref"))
    correlation_id = _string(task_ref.get("correlation_id"))
    config = service.log_config
    query_parts = [task_id]
    if correlation_id and correlation_id != task_id:
        query_parts.append(correlation_id)
    query = " OR ".join(query_parts) if config is not None else None
    return StudioTracePathLogMetadata(
        configured=config is not None,
        provider=getattr(config, "provider", None) if config is not None else None,
        source_label=getattr(config, "source_label", None) if config is not None else None,
        task_id_label=getattr(config, "task_id_label", None) if config is not None else None,
        correlation_id_label=getattr(config, "correlation_id_label", None) if config is not None else None,
        task_id=task_id,
        correlation_id=correlation_id,
        query=query,
        from_time=summary.started_at,
        to_time=summary.ended_at,
    )


def _dlq_messages_from_detail(detail_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    dlq_payload = _mapping_or_empty(detail_payload.get("dlq_messages"))
    return [dict(item) for item in _list_of_mappings(dlq_payload.get("items"))]


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


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _first_string(*values: Any) -> str | None:
    for value in values:
        normalized = _string(value)
        if normalized is not None:
            return normalized
    return None


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        parsed = _int_or_none(item)
        if parsed is not None:
            counts[str(key)] = parsed
    return counts


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _timestamp_sort_key(value: str | None) -> str:
    return _string(value) or ""


def _earliest_timestamp(*values: str | None) -> str | None:
    timestamps = sorted(value for value in (_string(item) for item in values) if value)
    return timestamps[0] if timestamps else None


def _latest_timestamp(*values: str | None) -> str | None:
    timestamps = sorted(value for value in (_string(item) for item in values) if value)
    return timestamps[-1] if timestamps else None


def _duration_ms(started_at: str | None, ended_at: str | None) -> float | None:
    start = _parse_iso_timestamp(started_at)
    end = _parse_iso_timestamp(ended_at)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() * 1000


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    normalized = _string(value)
    if normalized is None:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _state_from_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"processing", "running", "started", "planning"}:
        return "running"
    if normalized in {"retrying", "manual_retrying"}:
        return "retrying"
    if normalized in {"completed", "complete", "succeeded", "success"}:
        return "succeeded"
    if normalized in {"failed", "failure", "error"}:
        return "failed"
    if normalized in {"dead_lettered", "dlq", "dead-lettered"}:
        return "dead_lettered"
    if normalized in {"queued", "pending", "waiting"}:
        return "queued"
    return "unknown"


def _latest_status_from_nodes(nodes: list[StudioTracePathNode]) -> str | None:
    status_nodes = [node for node in nodes if node.kind == "status_event"]
    if not status_nodes:
        return None
    latest = sorted(status_nodes, key=lambda item: (_timestamp_sort_key(item.started_at), item.id))[-1]
    return latest.label


def _first_node(
    nodes: list[StudioTracePathNode],
    *,
    kind: str | None = None,
    queue_name: str | None = None,
    stage: str | None = None,
) -> StudioTracePathNode | None:
    for node in nodes:
        if kind is not None and node.kind != kind:
            continue
        if queue_name is not None and node.queue_name != queue_name:
            continue
        if stage is not None and node.stage != stage:
            continue
        return node
    return None


def _latest_non_dlq_node(
    nodes: list[StudioTracePathNode],
    *,
    task_id: str,
    before: str | None,
) -> StudioTracePathNode | None:
    candidates = [
        node
        for node in nodes
        if node.task_id == task_id
        and node.kind != "dlq_record"
        and _timestamp_sort_key(node.started_at) <= _timestamp_sort_key(before)
    ]
    if not candidates:
        candidates = [node for node in nodes if node.task_id == task_id and node.kind != "dlq_record"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (_timestamp_sort_key(item.started_at), item.id))[-1]


def _span_task_id(span: StudioTraceSpan) -> str | None:
    return _first_string(span.attributes.get("relayna.task_id"), span.attributes.get("task_id"))


def _span_queue_name(span: StudioTraceSpan) -> str | None:
    return _first_string(
        span.attributes.get("messaging.destination.name"),
        span.attributes.get("messaging.source.name"),
        span.attributes.get("queue_name"),
    )


def _span_stage(span: StudioTraceSpan) -> str | None:
    return _first_string(span.attributes.get("relayna.stage"), span.attributes.get("stage"))


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

    @router.get(f"{prefix}/tasks/{{service_id}}/{{task_id}}/trace-path", response_model=StudioTracePathResponse)
    async def task_trace_path(service_id: str, task_id: str) -> StudioTracePathResponse:
        try:
            return await trace_query_service.query_task_trace_path(service_id, task_id)
        except ServiceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StudioTraceConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StudioTraceProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


__all__ = [
    "StudioTraceConfigError",
    "StudioTracePathEdge",
    "StudioTracePathEvidence",
    "StudioTracePathLogMetadata",
    "StudioTracePathNode",
    "StudioTracePathResponse",
    "StudioTracePathSummary",
    "StudioTraceProviderError",
    "StudioTraceQueryService",
    "StudioTraceResponse",
    "StudioTraceSpan",
    "TempoTraceProvider",
    "create_studio_traces_router",
]
