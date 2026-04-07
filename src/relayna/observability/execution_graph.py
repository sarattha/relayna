from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..topology import (
    RelaynaTopology,
    RoutedTasksSharedStatusShardedAggregationTopology,
    RoutedTasksSharedStatusTopology,
    SharedStatusWorkflowTopology,
    SharedTasksSharedStatusShardedAggregationTopology,
    SharedTasksSharedStatusTopology,
)
from .store import RedisObservationStore

if TYPE_CHECKING:
    from ..dlq.service import DLQService
    from ..status.store import RedisStatusStore


class ExecutionGraphNode(BaseModel):
    id: str
    kind: str
    task_id: str | None = None
    label: str | None = None
    timestamp: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class ExecutionGraphEdge(BaseModel):
    source: str
    target: str
    kind: str
    timestamp: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class ExecutionGraphSummary(BaseModel):
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    graph_completeness: str = "partial"


class ExecutionGraph(BaseModel):
    task_id: str
    topology_kind: str
    summary: ExecutionGraphSummary
    nodes: list[ExecutionGraphNode] = Field(default_factory=list)
    edges: list[ExecutionGraphEdge] = Field(default_factory=list)
    annotations: dict[str, Any] = Field(default_factory=dict)
    related_task_ids: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class _PendingRetryLink:
    task_id: str
    retry_node_id: str
    retry_timestamp: str | None


@dataclass(slots=True)
class ExecutionGraphService:
    topology: RelaynaTopology
    status_store: RedisStatusStore
    observation_store: RedisObservationStore | None = None
    dlq_service: DLQService | None = None

    async def get_graph(self, task_id: str) -> ExecutionGraph | None:
        status_histories: dict[str, list[dict[str, Any]]] = {}
        observation_histories: dict[str, list[dict[str, Any]]] = {}
        related_task_ids = await self.status_store.get_child_task_ids(task_id)
        target_task_ids = [task_id, *[item for item in related_task_ids if item != task_id]]

        for current_task_id in target_task_ids:
            history = await self.status_store.get_history(current_task_id)
            if history:
                status_histories[current_task_id] = history
            if self.observation_store is not None:
                observations = await self.observation_store.get_history(current_task_id)
                if observations:
                    observation_histories[current_task_id] = observations

        dlq_records: dict[str, list[dict[str, Any]]] = {}
        if self.dlq_service is not None:
            for current_task_id in target_task_ids:
                payload = await self.dlq_service.list_messages(task_id=current_task_id, limit=200)
                if payload.items:
                    dlq_records[current_task_id] = [item.model_dump(mode="json") for item in payload.items]

        if not status_histories and not observation_histories and not dlq_records:
            return None

        return build_execution_graph(
            topology=self.topology,
            task_id=task_id,
            status_histories=status_histories,
            observation_histories=observation_histories,
            dlq_records=dlq_records,
            related_task_ids=related_task_ids,
        )


def build_execution_graph(
    *,
    topology: RelaynaTopology,
    task_id: str,
    status_histories: dict[str, list[dict[str, Any]]],
    observation_histories: dict[str, list[dict[str, Any]]],
    dlq_records: dict[str, list[dict[str, Any]]],
    related_task_ids: list[str] | tuple[str, ...] = (),
) -> ExecutionGraph:
    topology_kind = _topology_kind(topology)
    node_map: dict[str, ExecutionGraphNode] = {}
    edges: list[ExecutionGraphEdge] = []
    all_timestamps: list[str] = []
    related_ids = sorted({item for item in related_task_ids if item and item != task_id})
    task_node_ids: dict[str, str] = {}
    attempts_by_task: dict[str, list[ExecutionGraphNode]] = {}
    stage_attempts: list[ExecutionGraphNode] = []
    workflow_message_nodes: dict[str, ExecutionGraphNode] = {}
    task_types: set[str] = set()
    pending_retry_links: list[_PendingRetryLink] = []

    def add_node(node: ExecutionGraphNode) -> ExecutionGraphNode:
        existing = node_map.get(node.id)
        if existing is not None:
            existing.annotations.update(node.annotations)
            if existing.label is None:
                existing.label = node.label
            if existing.timestamp is None:
                existing.timestamp = node.timestamp
            if existing.task_id is None:
                existing.task_id = node.task_id
            return existing
        node_map[node.id] = node
        if node.timestamp:
            all_timestamps.append(node.timestamp)
        task_type = node.annotations.get("task_type")
        if isinstance(task_type, str) and task_type.strip():
            task_types.add(task_type.strip())
        return node

    def add_edge(edge: ExecutionGraphEdge) -> None:
        if edge.timestamp:
            all_timestamps.append(edge.timestamp)
        edges.append(edge)

    def add_task_node(current_task_id: str) -> str:
        if current_task_id in task_node_ids:
            return task_node_ids[current_task_id]
        node_kind = "task" if current_task_id == task_id else "aggregation_child"
        node = add_node(
            ExecutionGraphNode(
                id=f"{node_kind}:{current_task_id}",
                kind=node_kind,
                task_id=current_task_id,
                label=current_task_id,
            )
        )
        task_node_ids[current_task_id] = node.id
        return node.id

    add_task_node(task_id)
    for related_task_id in related_ids:
        add_task_node(related_task_id)
        add_edge(
            ExecutionGraphEdge(
                source=task_node_ids[related_task_id],
                target=task_node_ids[task_id],
                kind="aggregated_into",
            )
        )

    for current_task_id, observations in observation_histories.items():
        for item in _sort_by_timestamp(observations):
            event_type = str(item.get("event_type") or "")
            event_timestamp = _coerce_timestamp(item.get("timestamp"))
            if event_type in {"TaskMessageReceived", "AggregationMessageReceived"}:
                attempt_nodes = attempts_by_task.setdefault(current_task_id, [])
                attempt_id = f"task-attempt:{current_task_id}:{len(attempt_nodes) + 1}"
                attempt_node = add_node(
                    ExecutionGraphNode(
                        id=attempt_id,
                        kind="task_attempt",
                        task_id=current_task_id,
                        label=f"{current_task_id} attempt {len(attempt_nodes) + 1}",
                        timestamp=event_timestamp,
                        annotations={
                            "queue_name": item.get("queue_name"),
                            "correlation_id": item.get("correlation_id"),
                            "retry_attempt": item.get("retry_attempt", 0),
                            "task_type": item.get("task_type"),
                            "parent_task_id": item.get("parent_task_id"),
                            "consumer_name": item.get("consumer_name"),
                        },
                    )
                )
                attempt_nodes.append(attempt_node)
                add_edge(
                    ExecutionGraphEdge(
                        source=task_node_ids[current_task_id],
                        target=attempt_node.id,
                        kind="received_by",
                        timestamp=event_timestamp,
                    )
                )
                continue

            if event_type == "WorkflowMessagePublished":
                message_id = str(item.get("message_id") or "").strip()
                if not message_id:
                    continue
                workflow_message_nodes[message_id] = add_node(
                    ExecutionGraphNode(
                        id=f"workflow-message:{message_id}",
                        kind="workflow_message",
                        task_id=current_task_id,
                        label=str(item.get("stage") or message_id),
                        timestamp=event_timestamp,
                        annotations={
                            "message_id": message_id,
                            "stage": item.get("stage"),
                            "origin_stage": item.get("origin_stage"),
                            "routing_key": item.get("routing_key"),
                            "queue_name": item.get("queue_name"),
                            "correlation_id": item.get("correlation_id"),
                        },
                    )
                )
                source_stage = str(item.get("origin_stage") or "").strip()
                if source_stage:
                    source_attempt = _latest_stage_attempt(stage_attempts, source_stage, event_timestamp)
                    if source_attempt is not None:
                        add_edge(
                            ExecutionGraphEdge(
                                source=source_attempt.id,
                                target=workflow_message_nodes[message_id].id,
                                kind="stage_transitioned_to",
                                timestamp=event_timestamp,
                            )
                        )
                elif task_node_ids.get(current_task_id) is not None:
                    add_edge(
                        ExecutionGraphEdge(
                            source=task_node_ids[current_task_id],
                            target=workflow_message_nodes[message_id].id,
                            kind="received_by",
                            timestamp=event_timestamp,
                        )
                    )
                continue

            if event_type == "WorkflowMessageReceived":
                message_id = str(item.get("message_id") or "").strip()
                if message_id:
                    message_node = workflow_message_nodes.get(message_id)
                    if message_node is None:
                        message_node = add_node(
                            ExecutionGraphNode(
                                id=f"workflow-message:{message_id}",
                                kind="workflow_message",
                                task_id=current_task_id,
                                label=str(item.get("stage") or message_id),
                                timestamp=event_timestamp,
                                annotations={
                                    "message_id": message_id,
                                    "stage": item.get("stage"),
                                    "origin_stage": item.get("origin_stage"),
                                    "routing_key": item.get("routing_key"),
                                    "queue_name": item.get("queue_name"),
                                    "correlation_id": item.get("correlation_id"),
                                },
                            )
                        )
                        workflow_message_nodes[message_id] = message_node
                        add_edge(
                            ExecutionGraphEdge(
                                source=task_node_ids[current_task_id],
                                target=message_node.id,
                                kind="received_by",
                                timestamp=event_timestamp,
                            )
                        )
                else:
                    message_node = None
                stage_name = str(item.get("stage") or "unknown")
                attempt_index = (
                    len([node for node in stage_attempts if node.annotations.get("stage") == stage_name]) + 1
                )
                stage_attempt = add_node(
                    ExecutionGraphNode(
                        id=f"stage-attempt:{current_task_id}:{stage_name}:{attempt_index}",
                        kind="stage_attempt",
                        task_id=current_task_id,
                        label=f"{stage_name} attempt {attempt_index}",
                        timestamp=event_timestamp,
                        annotations={
                            "stage": stage_name,
                            "message_id": message_id or None,
                            "origin_stage": item.get("origin_stage"),
                            "queue_name": item.get("queue_name"),
                            "correlation_id": item.get("correlation_id"),
                            "retry_attempt": item.get("retry_attempt", 0),
                            "consumer_name": item.get("consumer_name"),
                        },
                    )
                )
                stage_attempts.append(stage_attempt)
                if message_node is not None:
                    add_edge(
                        ExecutionGraphEdge(
                            source=message_node.id,
                            target=stage_attempt.id,
                            kind="entered_stage",
                            timestamp=event_timestamp,
                        )
                    )
                continue

            if event_type in {"ConsumerRetryScheduled", "AggregationRetryScheduled"}:
                retry_count = len(
                    [
                        edge
                        for edge in edges
                        if edge.kind == "retried_as" and edge.annotations.get("task_id") == current_task_id
                    ]
                )
                retry_id = f"retry:{current_task_id}:{retry_count + 1}"
                retry_node = add_node(
                    ExecutionGraphNode(
                        id=retry_id,
                        kind="retry",
                        task_id=current_task_id,
                        label=f"retry {item.get('retry_attempt', 0)}",
                        timestamp=event_timestamp,
                        annotations={
                            "queue_name": item.get("queue_name"),
                            "source_queue_name": item.get("source_queue_name"),
                            "retry_attempt": item.get("retry_attempt"),
                            "max_retries": item.get("max_retries"),
                            "reason": item.get("reason"),
                            "task_id": current_task_id,
                        },
                    )
                )
                source_attempt = _latest_attempt(attempts_by_task.get(current_task_id, []), event_timestamp)
                if source_attempt is not None:
                    add_edge(
                        ExecutionGraphEdge(
                            source=source_attempt.id,
                            target=retry_node.id,
                            kind="retried_as",
                            timestamp=event_timestamp,
                            annotations={"task_id": current_task_id},
                        )
                    )
                pending_retry_links.append(
                    _PendingRetryLink(
                        task_id=current_task_id,
                        retry_node_id=retry_node.id,
                        retry_timestamp=event_timestamp,
                    )
                )
                continue

            if event_type in {"ConsumerDeadLetterPublished", "AggregationDeadLetterPublished"}:
                dlq_count = len(
                    [
                        node
                        for node in node_map.values()
                        if node.kind == "dlq_record" and node.task_id == current_task_id
                    ]
                )
                dlq_node = add_node(
                    ExecutionGraphNode(
                        id=f"dlq:{current_task_id}:{dlq_count + 1}",
                        kind="dlq_record",
                        task_id=current_task_id,
                        label=str(item.get("reason") or "dlq"),
                        timestamp=event_timestamp,
                        annotations={
                            "queue_name": item.get("queue_name"),
                            "source_queue_name": item.get("source_queue_name"),
                            "retry_attempt": item.get("retry_attempt"),
                            "max_retries": item.get("max_retries"),
                            "reason": item.get("reason"),
                        },
                    )
                )
                source_attempt = _latest_attempt(attempts_by_task.get(current_task_id, []), event_timestamp)
                if source_attempt is not None:
                    add_edge(
                        ExecutionGraphEdge(
                            source=source_attempt.id,
                            target=dlq_node.id,
                            kind="dead_lettered_to",
                            timestamp=event_timestamp,
                        )
                    )

    for current_task_id in sorted({task_id, *related_ids, *status_histories.keys(), *observation_histories.keys()}):
        for status_index, status_event in enumerate(
            _sort_by_timestamp(status_histories.get(current_task_id, [])), start=1
        ):
            status_timestamp = _coerce_timestamp(status_event.get("timestamp"))
            status_node = add_node(
                ExecutionGraphNode(
                    id=f"status:{current_task_id}:{status_index}",
                    kind="status_event",
                    task_id=current_task_id,
                    label=str(status_event.get("status") or "status"),
                    timestamp=status_timestamp,
                    annotations={
                        "status": status_event.get("status"),
                        "message": status_event.get("message"),
                        "meta": dict(status_event.get("meta") or {}),
                    },
                )
            )
            if isinstance(topology, SharedStatusWorkflowTopology):
                source_stage_attempt = _latest_stage_attempt(stage_attempts, None, status_timestamp)
                source_id = (
                    source_stage_attempt.id if source_stage_attempt is not None else task_node_ids[current_task_id]
                )
            else:
                source_attempt = _latest_attempt(attempts_by_task.get(current_task_id, []), status_timestamp)
                source_id = source_attempt.id if source_attempt is not None else task_node_ids[current_task_id]
            add_edge(
                ExecutionGraphEdge(
                    source=source_id,
                    target=status_node.id,
                    kind="published_status",
                    timestamp=status_timestamp,
                )
            )

            if str(status_event.get("status") or "").lower() == "manual_retrying":
                source_attempt = _latest_attempt(attempts_by_task.get(current_task_id, []), status_timestamp)
                target_attempt = _next_attempt(attempts_by_task.get(current_task_id, []), status_timestamp)
                if source_attempt is not None and target_attempt is not None:
                    add_edge(
                        ExecutionGraphEdge(
                            source=source_attempt.id,
                            target=target_attempt.id,
                            kind="manual_retry_to",
                            timestamp=status_timestamp,
                        )
                    )

    for pending_retry in pending_retry_links:
        next_attempt = _next_attempt(
            attempts_by_task.get(pending_retry.task_id, []),
            pending_retry.retry_timestamp,
        )
        if next_attempt is None:
            continue
        add_edge(
            ExecutionGraphEdge(
                source=pending_retry.retry_node_id,
                target=next_attempt.id,
                kind="received_by",
                timestamp=next_attempt.timestamp,
            )
        )

    for current_task_id, records in dlq_records.items():
        for index, record in enumerate(records, start=1):
            node_id = f"dlq:{current_task_id}:record:{index}"
            if node_id in node_map:
                continue
            dlq_node = add_node(
                ExecutionGraphNode(
                    id=node_id,
                    kind="dlq_record",
                    task_id=current_task_id,
                    label=str(record.get("reason") or record.get("queue_name") or "dlq"),
                    timestamp=_coerce_timestamp(record.get("dead_lettered_at")),
                    annotations=dict(record),
                )
            )
            source_attempt = _latest_attempt(attempts_by_task.get(current_task_id, []), dlq_node.timestamp)
            add_edge(
                ExecutionGraphEdge(
                    source=source_attempt.id if source_attempt is not None else task_node_ids[current_task_id],
                    target=dlq_node.id,
                    kind="dead_lettered_to",
                    timestamp=dlq_node.timestamp,
                )
            )

    summary = _build_summary(
        task_id=task_id,
        status_history=status_histories.get(task_id, []),
        all_timestamps=all_timestamps,
        has_observations=bool(observation_histories),
    )
    annotations: dict[str, Any] = {}
    if task_types:
        annotations["task_types"] = sorted(task_types)

    return ExecutionGraph(
        task_id=task_id,
        topology_kind=topology_kind,
        summary=summary,
        nodes=sorted(node_map.values(), key=lambda item: (_coerce_timestamp(item.timestamp), item.id)),
        edges=sorted(edges, key=lambda item: (_coerce_timestamp(item.timestamp), item.kind, item.source, item.target)),
        annotations=annotations,
        related_task_ids=related_ids,
    )


def execution_graph_mermaid(graph: ExecutionGraph) -> str:
    node_ids = _build_mermaid_node_ids((node.id for node in graph.nodes), prefix="node")
    lines = ["flowchart LR"]

    for node in graph.nodes:
        mermaid_id = node_ids[node.id]
        label_parts = [node.label or node.id]
        kind = node.kind.strip()
        if kind:
            label_parts.append(kind)
        timestamp = _coerce_timestamp(node.timestamp)
        if timestamp:
            label_parts.append(timestamp)
        label = "\\n".join(_escape_mermaid_label(item) for item in label_parts if item)
        lines.append(f'    {mermaid_id}["{label}"]')

    for edge in graph.edges:
        source = node_ids.get(edge.source)
        target = node_ids.get(edge.target)
        if source is None or target is None:
            continue
        edge_label = _escape_mermaid_label(edge.kind)
        lines.append(f"    {source} -->|{edge_label}| {target}")

    return "\n".join(lines)


def _build_summary(
    *,
    task_id: str,
    status_history: list[dict[str, Any]],
    all_timestamps: list[str],
    has_observations: bool,
) -> ExecutionGraphSummary:
    latest_root_status = None
    ended_at = None
    if status_history:
        ordered_statuses = _sort_by_timestamp(status_history)
        latest_root_status = str(ordered_statuses[-1].get("status") or "").strip() or None
        ended_at = _coerce_timestamp(ordered_statuses[-1].get("timestamp"))

    timestamps = sorted(timestamp for timestamp in (_coerce_timestamp(item) for item in all_timestamps) if timestamp)
    started_at = timestamps[0] if timestamps else None
    if ended_at is None and timestamps:
        ended_at = timestamps[-1]
    duration_ms = None
    if started_at and ended_at:
        duration_ms = int((_parse_timestamp(ended_at) - _parse_timestamp(started_at)).total_seconds() * 1000)
    return ExecutionGraphSummary(
        status=latest_root_status,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        graph_completeness="full" if has_observations else "partial",
    )


def _sort_by_timestamp(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (_coerce_timestamp(item.get("timestamp")), str(item.get("event_id", ""))))


def _latest_attempt(attempts: list[ExecutionGraphNode], timestamp: str | None) -> ExecutionGraphNode | None:
    if not attempts:
        return None
    if timestamp is None:
        return attempts[-1]
    eligible = [item for item in attempts if _coerce_timestamp(item.timestamp) <= timestamp]
    return eligible[-1] if eligible else attempts[0]


def _next_attempt(attempts: list[ExecutionGraphNode], timestamp: str | None) -> ExecutionGraphNode | None:
    if timestamp is None:
        return None
    eligible = [item for item in attempts if _coerce_timestamp(item.timestamp) > timestamp]
    return eligible[0] if eligible else None


def _latest_stage_attempt(
    attempts: list[ExecutionGraphNode], stage: str | None, timestamp: str | None
) -> ExecutionGraphNode | None:
    eligible = [
        item
        for item in attempts
        if (stage is None or item.annotations.get("stage") == stage)
        and (timestamp is None or _coerce_timestamp(item.timestamp) <= timestamp)
    ]
    if eligible:
        return eligible[-1]
    return None


def _coerce_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _topology_kind(topology: RelaynaTopology) -> str:
    if isinstance(topology, SharedStatusWorkflowTopology):
        return "shared_status_workflow"
    if isinstance(topology, RoutedTasksSharedStatusShardedAggregationTopology):
        return "routed_tasks_shared_status_sharded_aggregation"
    if isinstance(topology, SharedTasksSharedStatusShardedAggregationTopology):
        return "shared_tasks_shared_status_sharded_aggregation"
    if isinstance(topology, RoutedTasksSharedStatusTopology):
        return "routed_tasks_shared_status"
    if isinstance(topology, SharedTasksSharedStatusTopology):
        return "shared_tasks_shared_status"
    return type(topology).__name__


def _build_mermaid_node_ids(names: Iterable[str], *, prefix: str) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        base = f"{prefix}_{_sanitize_mermaid_identifier(name)}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        identifiers[name] = candidate
        used.add(candidate)
    return identifiers


def _sanitize_mermaid_identifier(name: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_")
    return sanitized or "node"


def _escape_mermaid_label(value: str) -> str:
    return str(value).replace('"', '\\"')


__all__ = [
    "ExecutionGraph",
    "ExecutionGraphEdge",
    "ExecutionGraphNode",
    "ExecutionGraphService",
    "ExecutionGraphSummary",
    "build_execution_graph",
    "execution_graph_mermaid",
]
