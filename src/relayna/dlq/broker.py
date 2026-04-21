from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .models import BrokerDLQMessage, inspect_dead_letter_body


class BrokerDLQMessageInspector(Protocol):
    async def list_messages(self, queue_name: str, *, limit: int = 50) -> list[BrokerDLQMessage]: ...


def _default_async_client_factory(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_seconds)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_key(queue_name: str, raw_body_b64: str, headers: dict[str, Any], properties: dict[str, Any]) -> str:
    for candidate in (
        properties.get("message_id"),
        headers.get("message_id"),
        headers.get("x-message-id"),
        headers.get("x-relayna-message-id"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    digest = hashlib.sha256()
    digest.update(queue_name.encode("utf-8"))
    digest.update(raw_body_b64.encode("ascii"))
    digest.update(str(properties.get("correlation_id") or "").encode("utf-8"))
    return digest.hexdigest()


def _normalize_headers(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _normalize_properties(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _normalized_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _extract_dead_letter_time(headers: dict[str, Any], properties: dict[str, Any]) -> datetime | None:
    for candidate in (
        headers.get("x-first-death-time"),
        headers.get("x-last-death-time"),
        properties.get("timestamp"),
    ):
        parsed = _parse_datetime(candidate)
        if parsed is not None:
            return parsed
    x_death = headers.get("x-death")
    if isinstance(x_death, list):
        for item in x_death:
            if not isinstance(item, dict):
                continue
            parsed = _parse_datetime(item.get("time"))
            if parsed is not None:
                return parsed
    return None


def _extract_source_queue_name(headers: dict[str, Any]) -> str | None:
    for candidate in (
        headers.get("x-first-death-queue"),
        headers.get("x-last-death-queue"),
        headers.get("x-relayna-source-queue"),
    ):
        normalized = _normalized_string(candidate)
        if normalized is not None:
            return normalized
    x_death = headers.get("x-death")
    if isinstance(x_death, list):
        for item in x_death:
            if not isinstance(item, dict):
                continue
            normalized = _normalized_string(item.get("queue"))
            if normalized is not None:
                return normalized
    return None


def _extract_reason(headers: dict[str, Any]) -> str | None:
    for candidate in (
        headers.get("x-first-death-reason"),
        headers.get("x-last-death-reason"),
        headers.get("x-relayna-failure-reason"),
    ):
        normalized = _normalized_string(candidate)
        if normalized is not None:
            return normalized
    x_death = headers.get("x-death")
    if isinstance(x_death, list):
        for item in x_death:
            if not isinstance(item, dict):
                continue
            normalized = _normalized_string(item.get("reason"))
            if normalized is not None:
                return normalized
    return None


def _extract_task_id(headers: dict[str, Any], body: Any) -> str | None:
    for candidate in (
        headers.get("task_id"),
        headers.get("x-relayna-task-id"),
    ):
        normalized = _normalized_string(candidate)
        if normalized is not None:
            return normalized
    if isinstance(body, dict):
        normalized = _normalized_string(body.get("task_id"))
        if normalized is not None:
            return normalized
    return None


def _extract_correlation_id(headers: dict[str, Any], properties: dict[str, Any], body: Any) -> str | None:
    for candidate in (
        properties.get("correlation_id"),
        headers.get("correlation_id"),
        headers.get("x-correlation-id"),
    ):
        normalized = _normalized_string(candidate)
        if normalized is not None:
            return normalized
    if isinstance(body, dict):
        normalized = _normalized_string(body.get("correlation_id"))
        if normalized is not None:
            return normalized
    return None


def broker_message_from_management_payload(queue_name: str, payload: dict[str, Any]) -> BrokerDLQMessage:
    payload_encoding = _normalized_string(payload.get("payload_encoding"))
    encoded_payload = payload.get("payload")
    if isinstance(encoded_payload, bytes):
        body_bytes = encoded_payload
    elif isinstance(encoded_payload, str) and payload_encoding == "base64":
        body_bytes = base64.b64decode(encoded_payload.encode("ascii"))
    elif isinstance(encoded_payload, str):
        body_bytes = encoded_payload.encode("utf-8")
    else:
        body_bytes = b""
    body, body_encoding, raw_body_b64 = inspect_dead_letter_body(body_bytes)
    properties = _normalize_properties(payload.get("properties"))
    headers = _normalize_headers(properties.get("headers"))
    return BrokerDLQMessage(
        queue_name=queue_name,
        message_key=_message_key(queue_name, raw_body_b64, headers, properties),
        task_id=_extract_task_id(headers, body),
        correlation_id=_extract_correlation_id(headers, properties, body),
        reason=_extract_reason(headers),
        source_queue_name=_extract_source_queue_name(headers),
        content_type=_normalized_string(properties.get("content_type")),
        body_encoding=body_encoding,
        dead_lettered_at=_extract_dead_letter_time(headers, properties),
        headers=headers,
        body=body,
        raw_body_b64=raw_body_b64,
        redelivered=bool(payload.get("redelivered")) if payload.get("redelivered") is not None else None,
    )


@dataclass(slots=True)
class RabbitMQManagementDLQInspector:
    base_url: str
    username: str
    password: str
    vhost: str = "/"
    timeout_seconds: float = 5.0
    client_factory: Callable[[float], httpx.AsyncClient] = _default_async_client_factory

    async def list_messages(self, queue_name: str, *, limit: int = 50) -> list[BrokerDLQMessage]:
        normalized_queue_name = str(queue_name).strip()
        if not normalized_queue_name:
            raise ValueError("queue_name must not be empty")
        normalized_limit = max(1, min(int(limit), 200))
        client = self.client_factory(self.timeout_seconds)
        try:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/queues/{quote(self.vhost, safe='')}/{quote(normalized_queue_name, safe='')}/get",
                auth=(self.username, self.password),
                json={
                    "count": normalized_limit,
                    "ackmode": "ack_requeue_true",
                    "encoding": "auto",
                    "truncate": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()
        finally:
            await client.aclose()
        if not isinstance(payload, list):
            raise ValueError("RabbitMQ management API returned an invalid DLQ inspection payload.")
        items: list[BrokerDLQMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            items.append(broker_message_from_management_payload(normalized_queue_name, item))
        return items


__all__ = [
    "BrokerDLQMessageInspector",
    "RabbitMQManagementDLQInspector",
    "broker_message_from_management_payload",
]
