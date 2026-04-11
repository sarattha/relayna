from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

from ..contracts import ContractAliasConfig
from ..topology import RelaynaTopology, topology_kind
from .schemas import AliasConfigSummary, CapabilityDocument, CapabilityServiceMetadata

STATUS_EVENTS_ROUTE_ID = "status.events"
STATUS_LATEST_ROUTE_ID = "status.latest"
STATUS_HISTORY_ROUTE_ID = "status.history"
STATUS_CAPABILITY_ROUTE_IDS = (STATUS_EVENTS_ROUTE_ID, STATUS_LATEST_ROUTE_ID, STATUS_HISTORY_ROUTE_ID)
DLQ_QUEUES_ROUTE_ID = "dlq.queues"
DLQ_MESSAGES_ROUTE_ID = "dlq.messages"
DLQ_MESSAGE_DETAIL_ROUTE_ID = "dlq.message_detail"
DLQ_REPLAY_ROUTE_ID = "dlq.replay"
DLQ_CAPABILITY_ROUTE_IDS = (
    DLQ_QUEUES_ROUTE_ID,
    DLQ_MESSAGES_ROUTE_ID,
    DLQ_MESSAGE_DETAIL_ROUTE_ID,
    DLQ_REPLAY_ROUTE_ID,
)
WORKFLOW_TOPOLOGY_ROUTE_ID = "workflow.topology"
WORKFLOW_STAGES_ROUTE_ID = "workflow.stages"
WORKFLOW_CAPABILITY_ROUTE_IDS = (WORKFLOW_TOPOLOGY_ROUTE_ID, WORKFLOW_STAGES_ROUTE_ID)
EXECUTION_GRAPH_ROUTE_ID = "execution.graph"
EXECUTION_CAPABILITY_ROUTE_IDS = (EXECUTION_GRAPH_ROUTE_ID,)
EVENTS_FEED_ROUTE_ID = "events.feed"
EVENTS_CAPABILITY_ROUTE_IDS = (EVENTS_FEED_ROUTE_ID,)
HEALTH_WORKERS_ROUTE_ID = "health.workers"
HEALTH_CAPABILITY_ROUTE_IDS = (HEALTH_WORKERS_ROUTE_ID,)

ALL_CAPABILITY_ROUTE_IDS = frozenset(
    {
        STATUS_EVENTS_ROUTE_ID,
        STATUS_LATEST_ROUTE_ID,
        STATUS_HISTORY_ROUTE_ID,
        DLQ_QUEUES_ROUTE_ID,
        DLQ_MESSAGES_ROUTE_ID,
        DLQ_MESSAGE_DETAIL_ROUTE_ID,
        DLQ_REPLAY_ROUTE_ID,
        WORKFLOW_TOPOLOGY_ROUTE_ID,
        WORKFLOW_STAGES_ROUTE_ID,
        EXECUTION_GRAPH_ROUTE_ID,
        EVENTS_FEED_ROUTE_ID,
        HEALTH_WORKERS_ROUTE_ID,
    }
)


def merge_capability_route_ids(*groups: Iterable[str]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for route_id in group:
            if route_id not in ALL_CAPABILITY_ROUTE_IDS:
                raise ValueError(f"Unsupported capability route id '{route_id}'.")
            if route_id in seen:
                continue
            seen.add(route_id)
            merged.append(route_id)
    return tuple(merged)


def build_alias_config_summary(alias_config: ContractAliasConfig | None) -> AliasConfigSummary:
    if alias_config is None:
        return AliasConfigSummary()
    payload_aliases = dict(alias_config.field_aliases)
    http_aliases = dict(alias_config.http_aliases)
    return AliasConfigSummary(
        aliasing_enabled=bool(payload_aliases or http_aliases),
        payload_aliases=payload_aliases,
        http_aliases=http_aliases,
    )


def relayna_version() -> str:
    try:
        return version("relayna")
    except PackageNotFoundError:
        return "0.0.0"


def build_capability_document(
    *,
    topology: RelaynaTopology,
    supported_routes: Iterable[str],
    alias_config: ContractAliasConfig | None = None,
    feature_flags: Iterable[str] | None = None,
    service_title: str | None = None,
    capability_path: str = "/relayna/capabilities",
    discovery_source: str = "live",
    compatibility: str = "capabilities_v1",
) -> CapabilityDocument:
    unique_routes = list(merge_capability_route_ids(supported_routes))
    unique_flags = list(dict.fromkeys(feature_flags or ()))
    return CapabilityDocument(
        relayna_version=relayna_version(),
        topology_kind=topology_kind(topology),
        alias_config_summary=build_alias_config_summary(alias_config),
        supported_routes=unique_routes,
        feature_flags=unique_flags,
        service_metadata=CapabilityServiceMetadata(
            service_title=service_title,
            capability_path=capability_path,
            discovery_source=discovery_source,
            compatibility=compatibility,
        ),
    )


def build_legacy_fallback_capability_document(*, capability_path: str = "/relayna/capabilities") -> CapabilityDocument:
    return CapabilityDocument(
        relayna_version="unknown",
        topology_kind="unknown",
        alias_config_summary=AliasConfigSummary(),
        supported_routes=[],
        feature_flags=[],
        service_metadata=CapabilityServiceMetadata(
            service_title=None,
            capability_path=capability_path,
            discovery_source="fallback",
            compatibility="legacy_no_capabilities_endpoint",
        ),
    )


def create_capabilities_router(
    *,
    topology: RelaynaTopology,
    supported_routes: Iterable[str],
    alias_config: ContractAliasConfig | None = None,
    feature_flags: Iterable[str] | None = None,
    service_title: str | None = None,
    capability_path: str = "/relayna/capabilities",
) -> APIRouter:
    router = APIRouter()
    supported_routes_snapshot = tuple(supported_routes)
    feature_flags_snapshot = tuple(feature_flags or ())

    @router.get(capability_path, response_model=CapabilityDocument)
    async def capabilities() -> CapabilityDocument:
        return build_capability_document(
            topology=topology,
            supported_routes=supported_routes_snapshot,
            alias_config=alias_config,
            feature_flags=feature_flags_snapshot,
            service_title=service_title,
            capability_path=capability_path,
        )

    return router


__all__ = [
    "ALL_CAPABILITY_ROUTE_IDS",
    "DLQ_MESSAGES_ROUTE_ID",
    "DLQ_MESSAGE_DETAIL_ROUTE_ID",
    "DLQ_CAPABILITY_ROUTE_IDS",
    "DLQ_QUEUES_ROUTE_ID",
    "DLQ_REPLAY_ROUTE_ID",
    "EVENTS_CAPABILITY_ROUTE_IDS",
    "EVENTS_FEED_ROUTE_ID",
    "EXECUTION_CAPABILITY_ROUTE_IDS",
    "EXECUTION_GRAPH_ROUTE_ID",
    "HEALTH_CAPABILITY_ROUTE_IDS",
    "HEALTH_WORKERS_ROUTE_ID",
    "STATUS_CAPABILITY_ROUTE_IDS",
    "STATUS_EVENTS_ROUTE_ID",
    "STATUS_HISTORY_ROUTE_ID",
    "STATUS_LATEST_ROUTE_ID",
    "WORKFLOW_CAPABILITY_ROUTE_IDS",
    "WORKFLOW_STAGES_ROUTE_ID",
    "WORKFLOW_TOPOLOGY_ROUTE_ID",
    "build_alias_config_summary",
    "build_capability_document",
    "build_legacy_fallback_capability_document",
    "create_capabilities_router",
    "merge_capability_route_ids",
    "relayna_version",
]
