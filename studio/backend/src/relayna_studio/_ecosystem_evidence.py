from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from relayna.observability import (
    RelaynaServiceEvent,
    ServiceEventSourceKind,
    StudioEventIngestMethod,
    normalize_status_feed_event,
)

from .events import StudioEventEnvelope, StudioEventStore
from .registry import OutboundUrlPolicyError, ServiceRecord, StudioOutboundUrlPolicy

_LOG = logging.getLogger(__name__)
_USAGE_LIMIT = 100
_CELL_EVENT_LIMIT = 64


@dataclass(frozen=True, slots=True)
class _GatewayUsage:
    base_url: str
    token: str = field(repr=False)
    services: dict[str, str]

    @classmethod
    def from_environment(cls) -> _GatewayUsage | None:
        url = os.getenv("RELAYNA_STUDIO_GATEWAY_URL")
        token = os.getenv("RELAYNA_STUDIO_GATEWAY_USAGE_TOKEN")
        if not url or not token:
            return None
        try:
            services = json.loads(os.getenv("RELAYNA_STUDIO_GATEWAY_SERVICE_MAP", "{}"))
        except ValueError as exc:
            raise ValueError("RELAYNA_STUDIO_GATEWAY_SERVICE_MAP must be a JSON object") from exc
        if (
            not isinstance(services, dict)
            or not services
            or not all(key and isinstance(value, str) and value for key, value in services.items())
        ):
            raise ValueError("Gateway usage requires an explicit service map")
        return cls(url.rstrip("/"), token, services)


class _BrokerStats(BaseModel):
    broker: Literal["spooltail"]
    payload_reads: int = Field(ge=0)
    recovered_frames: int = Field(ge=0)
    version: str | None = None


class _UsageRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    request_id: str = Field(min_length=1, max_length=256)
    created_at: AwareDatetime
    task_id: str
    service_name: str
    run_id: str | None = None
    model: str | None = None
    provider: str
    status_code: int
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class _CellObservation(BaseModel):
    event_id: str = Field(min_length=1, max_length=256)
    timestamp: AwareDatetime
    decision: Literal["allow", "deny", "result", "error"]
    status: int | None = Field(default=None, ge=100, le=599)
    reason: str | None = Field(default=None, max_length=256, pattern=r"^[a-z][a-z0-9_]*$")


def _observation(
    task_id: str, source: str, suffix: str, key: str, timestamp: str | None, payload: dict[str, Any]
) -> RelaynaServiceEvent:
    identity = _event_identity(source, task_id, key)
    return RelaynaServiceEvent(
        cursor=identity,
        event_id=identity,
        task_id=task_id,
        event_type=f"{source}.{suffix}",
        source_kind=ServiceEventSourceKind.OBSERVATION,
        component=source,
        timestamp=timestamp,
        payload=payload,
    )


def _event_identity(source: str, task_id: str, key: str) -> str:
    digest = sha256(json.dumps([task_id, key]).encode()).hexdigest()
    return f"{source}:{digest}"


def _cell_observations(status: RelaynaServiceEvent) -> list[RelaynaServiceEvent]:
    meta = status.payload.get("meta")
    cell = meta.get("cell") if isinstance(meta, dict) else None
    if not isinstance(cell, dict) or not isinstance(cell_id := cell.get("cell_id"), str) or not cell_id:
        return []
    identity = {"cell_id": cell_id}
    if isinstance(execution := cell.get("execution_id"), str):
        identity["execution_id"] = execution
    if isinstance(attempt := status.payload.get("attempt_id"), str):
        identity["attempt_id"] = attempt
    audit = cell.get("inference")
    audit_rows = audit.get("events") if isinstance(audit, dict) else None
    report = identity.copy()
    if isinstance(audit, dict) and isinstance(audit.get("truncated"), bool):
        report["inference_truncated"] = audit["truncated"]
    events = [
        _observation(status.task_id, "cell", "launcher_report", f"{cell_id}:{status.cursor}", status.timestamp, report)
    ]
    if isinstance(audit_rows, list) and len(audit_rows) <= _CELL_EVENT_LIMIT:
        for row in audit_rows:
            try:
                item = _CellObservation.model_validate(row)
            except ValidationError:
                continue
            events.append(
                _observation(
                    status.task_id,
                    "cell",
                    f"inference.{item.decision}",
                    f"{cell_id}:{item.event_id}",
                    item.timestamp.isoformat(),
                    identity | item.model_dump(mode="json", exclude_none=True),
                )
            )
    return events


@dataclass(slots=True)
class _SpooltailEvidence:
    event_store: StudioEventStore
    client: httpx.AsyncClient
    policy: StudioOutboundUrlPolicy
    gateway: _GatewayUsage | None = None

    async def enrich(
        self,
        service: ServiceRecord,
        task_id: str,
        history: dict[str, Any],
        latest: dict[str, Any] | None,
        *,
        broker_available: bool,
    ) -> dict[str, Any]:
        broker, usage_rows = await asyncio.gather(
            self._broker(service.base_url) if broker_available else asyncio.sleep(0, result=None),
            self._usage(service.service_id, task_id),
        )
        history_events = history["events"]
        latest_event = latest.get("event") if latest is not None else None
        statuses: dict[str, RelaynaServiceEvent] = {}
        for raw in [*history_events, latest_event]:
            if isinstance(raw, dict) and raw.get("task_id") == task_id:
                event = normalize_status_feed_event(raw)
                if event is not None:
                    statuses[event.cursor] = event
        evidence: list[RelaynaServiceEvent] = []
        for status in statuses.values():
            payload = dict(status.payload)
            for key in ("status", "stage"):
                if isinstance(value := payload.get(key), str) and len(value) > 128:
                    payload.pop(key)
            evidence.append(
                status.model_copy(
                    update={
                        "component": "sdk",
                        "event_id": _event_identity("sdk", task_id, status.cursor),
                        "event_type": status.event_type[:128],
                        "correlation_id": status.correlation_id if len(status.correlation_id or "") <= 255 else None,
                        "payload": payload,
                    }
                )
            )
            evidence.extend(_cell_observations(status))
        for row in usage_rows or []:
            usage = row.model_dump(mode="json", exclude_none=True)
            if row.run_id:
                usage["attempt_id"] = row.run_id
            evidence.append(
                _observation(task_id, "gateway", "usage", row.request_id, row.created_at.isoformat(), usage)
            )
        for event in evidence:
            await self.event_store.insert_event(
                StudioEventEnvelope(
                    service_id=service.service_id, ingest_method=StudioEventIngestMethod.PULL, event=event
                )
            )
        saved = await self.event_store.list_task_events(service.service_id, task_id, limit=400)
        components = [
            event.model_dump(mode="json") for event in reversed(saved.items) if event.component in {"gateway", "cell"}
        ]
        return history | {
            "broker_stats": broker,
            "component_events": components,
            "gateway_usage": self._summarize_usage(components, source_available=usage_rows is not None)
            if self.gateway and service.service_id in self.gateway.services
            else None,
        }

    async def _get(
        self, base_url: str, path: str, *, params: dict[str, Any] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        try:
            self.policy.validate_url(base_url, label="Task evidence endpoint")
            response = await self.client.get(
                f"{base_url.rstrip('/')}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"} if token else None,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (OutboundUrlPolicyError, httpx.HTTPError, ValueError):
            _LOG.warning("Unable to read task evidence at %s", path)
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _broker(self, base_url: str) -> dict[str, Any] | None:
        payload = await self._get(base_url, "/relayna/broker")
        try:
            return _BrokerStats.model_validate(payload).model_dump(exclude_none=True)
        except ValidationError:
            return None

    async def _usage(self, service_id: str, task_id: str) -> list[_UsageRow] | None:
        scope = self.gateway
        service = scope.services.get(service_id) if scope is not None else None
        if not service or scope is None:
            return None
        payload = await self._get(
            scope.base_url,
            "/admin-ui/admin/usage/events",
            params={"task_id": task_id, "service": service, "limit": _USAGE_LIMIT},
            token=scope.token,
        )
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        result = []
        for raw in rows[:_USAGE_LIMIT]:
            try:
                row = _UsageRow.model_validate(raw)
            except ValidationError:
                continue
            if row.service_name == service and row.task_id == task_id:
                result.append(row)
        return result

    @staticmethod
    def _summarize_usage(components: list[dict[str, Any]], *, source_available: bool) -> dict[str, Any]:
        usage = []
        for item in components:
            if item["event_type"] == "gateway.usage":
                try:
                    usage.append(_UsageRow.model_validate(item["payload"]))
                except ValidationError:
                    continue
        costs = [row.estimated_cost_usd for row in usage if row.estimated_cost_usd is not None]
        return {
            "source_available": source_available,
            "observations": len(usage),
            "input_tokens": sum(row.input_tokens for row in usage),
            "output_tokens": sum(row.output_tokens for row in usage),
            "total_tokens": sum(row.total_tokens for row in usage),
            "estimated_cost_usd": sum(costs) if costs else None,
        }
